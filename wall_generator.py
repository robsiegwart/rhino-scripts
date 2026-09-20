# -*- coding: utf-8 -*-
"""
wall_generator.py
Floor-plan-to-wall generator for Rhino 7/8 (RhinoPython)

WHAT IT DOES
------------
Turns closed floor-plan curves into extruded 3D wall solids, then cuts
door and window openings out of them using boolean difference.

INPUT GEOMETRY
---------------
  - Wall outlines: closed planar curves representing the wall footprint
    in plan (already at wall thickness -- not centerlines). Curves that
    share an elevation are treated as one region, so an inner curve
    nested inside an outer one (e.g. a building's outer perimeter plus
    its inner face) cuts a hole for the interior space, the same way
    Rhino's PlanarSrf/ExtrudeCrv commands handle nested profile curves.
  - Window openings: closed planar curves drawn so they fully enclose
    the wall thickness at that location. Extruded from a sill height
    up to a head height and used as boolean cutters.
  - Door openings: same idea as windows, but extruded from the base of
    the wall (no vertical offset) up to the door height.

Each curve's own elevation (from its plane/bounding box) is used as its
base -- so wall, window, and door curves for one floor should all sit
at the same Z, and the script can be re-run per floor for multi-story
buildings.

Usage:
  Run from Rhino Python Editor (Tools > PythonScript > Edit)
  or save and call via a toolbar button. Select wall curves first,
  then window curves (Enter to skip), then door curves (Enter to skip).

CAVEATS
-------
  - One floor at a time: curves should belong to a single elevation:
    re-run the script for each additional floor.
  - Opening rectangles must fully enclose the wall thickness at that
    location, or the boolean cut will be incomplete.
  - Coincident/flush cutter faces can cause Rhino booleans to fail;
    drawing openings slightly proud of the wall faces avoids this.
  - Nested wall curves only cut a hole when they're genuinely coplanar
    (same elevation, within document tolerance) and don't cross each
    other; if the region can't be built, that group falls back to a
    plain per-curve extrusion (no hole) and a warning is printed.
"""

import rhinoscriptsyntax as rs
import Rhino.Geometry as rg
import scriptcontext as sc


# ---------------------------------------------------------------------------
# DEFAULTS  (all units in whatever your Rhino document units are)
# ---------------------------------------------------------------------------
DEFAULTS = {
    "wall_height":         96.0,   # 8'-0"
    "door_height":         80.0,
    "window_sill_height":  36.0,
    "window_head_height":  82.0,
}


# ---------------------------------------------------------------------------
# INPUT DIALOG
# ---------------------------------------------------------------------------
def get_parameters():
    """Show a multi-prompt dialog and return a params dict, or None if cancelled."""
    d = DEFAULTS

    prompts = [
        "Wall height",
        "Door height",
        "Window sill height",
        "Window head height",
    ]

    defaults = [
        str(d["wall_height"]),
        str(d["door_height"]),
        str(d["window_sill_height"]),
        str(d["window_head_height"]),
    ]

    results = rs.PropertyListBox(prompts, defaults, "Wall Parameters", "Generate Walls")
    if results is None:
        return None

    try:
        params = {
            "wall_height":        float(results[0]),
            "door_height":        float(results[1]),
            "window_sill_height": float(results[2]),
            "window_head_height": float(results[3]),
        }
    except (ValueError, IndexError) as e:
        rs.MessageBox("Invalid input: {}".format(e), title="Wall Generator")
        return None

    if params["window_head_height"] <= params["window_sill_height"]:
        rs.MessageBox(
            "Window head height must be greater than window sill height.",
            title="Wall Generator"
        )
        return None

    for key in ("wall_height", "door_height", "window_sill_height", "window_head_height"):
        if params[key] <= 0:
            rs.MessageBox("'{}' must be greater than zero.".format(key), title="Wall Generator")
            return None

    return params


# ---------------------------------------------------------------------------
# GEOMETRY HELPERS
# ---------------------------------------------------------------------------
def ensure_layer(name, color=None):
    """Create layer if it doesn't exist, including parent layers."""
    if not rs.IsLayer(name):
        rs.AddLayer(name, color)


def add_brep(brep, layer):
    """Add a Brep to the document on the given layer and return its GUID."""
    if brep is None or not brep.IsValid:
        print("  Warning: invalid brep skipped on layer '{}'".format(layer))
        return None
    ensure_layer(layer)
    guid = sc.doc.Objects.AddBrep(brep)
    rs.ObjectLayer(guid, layer)
    return guid


def curve_base_elevation(curve_id):
    """Return the Z of a planar curve's plane origin (its drawn elevation)."""
    plane = rs.CurvePlane(curve_id)
    if plane is not None:
        return plane.Origin.Z
    bbox = rs.BoundingBox(curve_id)
    return min(pt.Z for pt in bbox)


def extrude_closed_curve(curve_id, z0, height):
    """
    Build a capped solid Brep from a closed planar curve, extruded
    vertically from z0 to z0 + height. Returns a Brep, or None on failure.
    """
    curve = rs.coercecurve(curve_id)
    if curve is None:
        return None

    # Move a copy of the curve down to its own plane origin's Z, then
    # extrude straight up -- curve is assumed planar/closed already.
    move_to_base = rg.Transform.Translation(0, 0, z0 - curve.PointAtStart.Z)
    base_curve = curve.Duplicate()
    base_curve.Transform(move_to_base)

    extrusion = rg.Extrusion.Create(base_curve, height, True)
    if extrusion is not None:
        brep = extrusion.ToBrep()
    else:
        brep = rg.Brep.CreateFromExtrusion(base_curve, rg.Vector3d(0, 0, height))
        if brep is not None:
            capped = brep.CapPlanarHoles(sc.doc.ModelAbsoluteTolerance)
            if capped:
                brep = capped

    return brep


def build_wall_solids(wall_curve_ids, height, tolerance):
    """
    Build wall solids from the selected wall curves. Curves that share an
    elevation are treated together as one planar region -- so a nested
    curve (e.g. an inner boundary drawn inside an outer perimeter) cuts a
    hole (interior/void space) out of the wall mass, the same way Rhino's
    PlanarSrf/ExtrudeCrv commands treat nested profile curves. Falls back
    to a plain single-curve extrusion (no holes) if the region/offset
    approach fails for a given elevation group.
    """
    groups = []  # list of [z, [curve_ids]]
    for curve_id in wall_curve_ids:
        if not rs.IsCurveClosed(curve_id):
            print("  Warning: skipping non-closed wall curve.")
            continue
        if not rs.IsCurvePlanar(curve_id):
            print("  Warning: skipping non-planar wall curve.")
            continue

        z = curve_base_elevation(curve_id)
        for g in groups:
            if abs(g[0] - z) <= tolerance:
                g[1].append(curve_id)
                break
        else:
            groups.append([z, [curve_id]])

    breps = []
    for z0, curve_ids in groups:
        curves = [rs.coercecurve(cid) for cid in curve_ids]
        planar_breps = rg.Brep.CreatePlanarBreps(curves, tolerance)

        group_breps = []
        fallback = not planar_breps

        if not fallback:
            for pbrep in planar_breps:
                face = pbrep.Faces[0]
                u_mid = face.Domain(0).Mid
                v_mid = face.Domain(1).Mid
                normal = face.NormalAt(u_mid, v_mid)
                offset = height if normal.Z >= 0 else -height
                solid = rg.Brep.CreateFromOffsetFace(face, offset, tolerance, False, True)
                if solid is None:
                    fallback = True
                    break
                group_breps.append(solid)

        if fallback:
            print("  Warning: could not build a hole-aware wall region for {} curve(s) "
                  "at elevation {:.4f} -- extruding curves individually (no holes).".format(
                      len(curve_ids), z0))
            group_breps = []
            for curve_id in curve_ids:
                brep = extrude_closed_curve(curve_id, z0, height)
                if brep is not None:
                    group_breps.append(brep)

        breps.extend(group_breps)

    return breps


def collect_solids(curve_ids, z_offset, height, label):
    """
    Extrude each curve in curve_ids from (its own base elevation + z_offset)
    up by height. Skips and warns on non-closed/non-planar curves.
    Returns a list of Breps.
    """
    breps = []
    for curve_id in curve_ids or []:
        if not rs.IsCurveClosed(curve_id):
            print("  Warning: skipping non-closed {} curve.".format(label))
            continue
        if not rs.IsCurvePlanar(curve_id):
            print("  Warning: skipping non-planar {} curve.".format(label))
            continue

        z0 = curve_base_elevation(curve_id) + z_offset
        brep = extrude_closed_curve(curve_id, z0, height)
        if brep is None:
            print("  Warning: failed to extrude a {} curve.".format(label))
            continue
        breps.append(brep)

    return breps


# ---------------------------------------------------------------------------
# LAYER SETUP
# ---------------------------------------------------------------------------
def setup_layers():
    if not rs.IsLayer("Walls"):
        rs.AddLayer("Walls", rs.CreateColor(190, 190, 190))


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def main():
    p = get_parameters()
    if p is None:
        return

    wall_curves = rs.GetObjects(
        "Select closed wall outline curves", filter=rs.filter.curve, preselect=True
    )
    if not wall_curves:
        print("No wall curves selected -- aborting.")
        return

    window_curves = rs.GetObjects(
        "Select window opening curves (Enter to skip)", filter=rs.filter.curve
    )
    door_curves = rs.GetObjects(
        "Select door opening curves (Enter to skip)", filter=rs.filter.curve
    )

    setup_layers()
    rs.EnableRedraw(False)

    tolerance = sc.doc.ModelAbsoluteTolerance

    wall_breps = build_wall_solids(wall_curves, p["wall_height"], tolerance)
    window_cutters = collect_solids(
        window_curves,
        p["window_sill_height"],
        p["window_head_height"] - p["window_sill_height"],
        "window",
    )
    door_cutters = collect_solids(door_curves, 0.0, p["door_height"], "door")

    if not wall_breps:
        rs.EnableRedraw(True)
        rs.MessageBox("No valid wall solids were built -- nothing to do.", title="Wall Generator")
        return

    # --- Union all wall solids into one mass ---
    unioned = rg.Brep.CreateBooleanUnion(wall_breps, tolerance)
    if unioned:
        walls = list(unioned)
        union_fallback = False
    else:
        print("  Warning: wall union failed -- keeping walls as separate solids.")
        walls = wall_breps
        union_fallback = True

    # --- Cut windows ---
    windows_cut = 0
    if window_cutters:
        result = rg.Brep.CreateBooleanDifference(walls, window_cutters, tolerance)
        if result:
            walls = list(result)
            windows_cut = len(window_cutters)
        else:
            print("  Warning: window boolean difference failed -- windows not cut.")

    # --- Cut doors ---
    doors_cut = 0
    if door_cutters:
        result = rg.Brep.CreateBooleanDifference(walls, door_cutters, tolerance)
        if result:
            walls = list(result)
            doors_cut = len(door_cutters)
        else:
            print("  Warning: door boolean difference failed -- doors not cut.")

    added = 0
    for brep in walls:
        if add_brep(brep, "Walls") is not None:
            added += 1

    rs.EnableRedraw(True)

    rs.MessageBox(
        "Wall generation complete.\n\n"
        "Wall curves selected:  {}\n"
        "Wall solids built:     {}\n"
        "Windows cut:           {}\n"
        "Doors cut:             {}\n"
        "Solids added:          {}\n"
        "Wall union fallback:   {}".format(
            len(wall_curves),
            len(wall_breps),
            windows_cut,
            doors_cut,
            added,
            "yes" if union_fallback else "no",
        ),
        title="Wall Generator — Done"
    )


if __name__ == "__main__":
    main()
