"""
retire_layer.py
Rhino 7 (IronPython 2.7 script engine)

PROBLEM THIS SOLVES
--------------------
Rhino won't let you delete a layer if any geometry references it --
including geometry buried inside block definitions, even nested ones.
Manually opening every block to re-layer its contents is impractical
once blocks are nested a few levels deep.

HOW IT WORKS
------------
Rhino stores every block definition in a single flat table, regardless
of how deeply it's nested inside other blocks. So "nested blocks" don't
require recursion through a nesting tree -- you just iterate every
definition in doc.InstanceDefinitions once, and every definition (top
level or buried ten levels deep) shows up as its own entry.

WHAT IT DOES
------------
    1. Prompts for a "source" layer (to retire) and a "target" layer
       (where its contents should move).
    2. Re-layers ordinary top-level document objects on the source layer
       (this also covers block INSTANCES sitting on that layer).
    3. Walks every block DEFINITION in the document and re-layers any of
       its internal objects that reference the source layer.
    4. Skips linked (externally-referenced) block definitions -- their
       geometry lives in another file and isn't editable from here --
       and reports which ones were skipped.
    5. Checks whether the source layer is actually empty (no references
       left anywhere, including inside skipped linked blocks) and, if so,
       deletes it automatically -- no prompt.

CAVEATS
-------
    - Linked blocks need to be fixed in their source file; this script
      only touches embedded (non-linked) definitions.
    - If the source layer has child layers, or is still the current
      layer, layer deletion may still fail -- the script switches the
      current layer to the target first, but doesn't touch children.

RUNNING IT
----------
    Type "RunPythonScript" in Rhino and browse to this file, or wire it
    into an alias:
        -_RunPythonScript "C:\\path\\to\\retire_layer.py"
"""

from __future__ import print_function
import rhinoscriptsyntax as rs
import scriptcontext as sc
import Rhino


def _layer_index_by_name(doc, name):
    for layer in doc.Layers:
        if not layer.IsDeleted and layer.FullPath == name:
            return layer.Index
    return -1


def _layer_is_empty(doc, layer_index):
    """True only if nothing references this layer anywhere -- top-level
    objects or geometry inside any block definition, linked ones included,
    since Rhino still refuses to delete a layer that a linked block's
    geometry sits on even though we can't re-layer it from here."""
    for obj in doc.Objects:
        if obj.Attributes.LayerIndex == layer_index:
            return False
    for idef in doc.InstanceDefinitions:
        if idef.IsDeleted:
            continue
        for obj in idef.GetObjects():
            if obj is None or obj.IsDeleted:
                continue
            if obj.Attributes.LayerIndex == layer_index:
                return False
    return True


def ReassignBlockContentLayer():
    doc = sc.doc

    source_name = rs.GetLayer("Select the layer you want to retire")
    if not source_name:
        return
    target_name = rs.GetLayer("Select the layer to move its contents to")
    if not target_name:
        return
    if source_name == target_name:
        print("Source and target are the same layer -- nothing to do.")
        return

    source_index = _layer_index_by_name(doc, source_name)
    target_index = _layer_index_by_name(doc, target_name)
    if source_index < 0 or target_index < 0:
        print("Could not resolve one of the layers -- aborting.")
        return

    changed_top_level = 0
    changed_in_blocks = 0
    skipped_linked = set()

    # --- 1. Ordinary top-level document objects (includes block instances) ---
    for obj in doc.Objects:
        if obj.Attributes.LayerIndex == source_index:
            obj.Attributes.LayerIndex = target_index
            obj.CommitChanges()
            changed_top_level += 1

    # --- 2. Every block definition in the document -- flat table, so
    #        nested definitions are just other entries here, no manual
    #        recursion through the nesting hierarchy needed ---
    linked_types = (Rhino.DocObjects.InstanceDefinitionUpdateType.Linked,
                    Rhino.DocObjects.InstanceDefinitionUpdateType.LinkedAndEmbedded)

    for idef in doc.InstanceDefinitions:
        if idef.IsDeleted:
            continue
        if idef.UpdateType in linked_types:
            skipped_linked.add(idef.Name)
            continue

        for obj in idef.GetObjects():
            if obj is None or obj.IsDeleted:
                continue
            if obj.Attributes.LayerIndex == source_index:
                obj.Attributes.LayerIndex = target_index
                obj.CommitChanges()
                changed_in_blocks += 1

    sc.doc.Views.Redraw()

    print("Re-layered {0} top-level object(s) and {1} object(s) inside block definitions.".format(
        changed_top_level, changed_in_blocks))

    if skipped_linked:
        print("Skipped {0} linked block definition(s) -- fix these in their source file(s): {1}".format(
            len(skipped_linked), ", ".join(sorted(skipped_linked))))

    # --- 3. Delete the source layer automatically, once confirmed empty ---
    if doc.Layers.CurrentLayer.FullPath == source_name:
        rs.CurrentLayer(target_name)

    if _layer_is_empty(doc, source_index):
        ok = rs.DeleteLayer(source_name)
        if ok:
            print("Layer '{0}' was empty -- deleted.".format(source_name))
        else:
            print("Layer '{0}' appears empty but could not be deleted -- check for "
                  "child layers under it.".format(source_name))
    else:
        print("Layer '{0}' still has references (likely inside a skipped linked "
              "block) -- not deleted.".format(source_name))


def ListLayersUsedInBlocks():
    """Diagnostic: report which layers are referenced by geometry buried
    inside block definitions, before you try to clean anything up."""
    doc = sc.doc
    usage = {}  # layer full path -> object count

    for idef in doc.InstanceDefinitions:
        if idef.IsDeleted:
            continue
        for obj in idef.GetObjects():
            if obj is None or obj.IsDeleted:
                continue
            layer = doc.Layers[obj.Attributes.LayerIndex]
            usage[layer.FullPath] = usage.get(layer.FullPath, 0) + 1

    if not usage:
        print("No block-definition geometry found.")
        return

    print("Layers referenced by geometry inside block definitions:")
    for name, count in sorted(usage.items()):
        print("  {0}: {1} object(s)".format(name, count))


if __name__ == "__main__":
    ReassignBlockContentLayer()
