# -*- coding: utf-8 -*-
"""
stair_generator.py
Parametric stair generator for Rhino 7/8 (RhinoPython)

Parameters:
  - Total rise
  - Number of steps
  - Unit run (tread depth, not including overhang)
  - Stair width
  - Stringer width (nominal lumber, e.g. 2x10, 2x12)
  - Tread board thickness
  - Tread overhang front (nosing projection beyond riser face)
  - Tread overhang side (projection beyond stringer face)
  - Nosing radius (front-edge fillet)
  - Riser board thickness

Coordinate system:
  - Stairs run in the +Y direction
  - Rise in the +Z direction
  - Width in the +X direction

Usage:
  Run from Rhino Python Editor (Tools > PythonScript > Edit)
  or save and call via a toolbar button.
"""

import rhinoscriptsyntax as rs
import Rhino.Geometry as rg
import scriptcontext as sc
import math


# ---------------------------------------------------------------------------
# DEFAULTS  (all units in whatever your Rhino document units are)
# ---------------------------------------------------------------------------
DEFAULTS = {
    "total_rise":           108.0,   # e.g. 108" = 9'-0"
    "num_steps":            14,
    "unit_run":             10.0,    # horizontal depth of each step
    "stair_width":          36.0,
    "stringer_width":       11.25,   # actual dimension of a 2x12 (1.5 x 11.25)
    "stringer_thickness":   1.5,     # actual thickness of stringer board
    "tread_thickness":      1.5,     # actual thickness of a 5/4 or 2x tread board
    "tread_overhang_front": 1.25,    # nosing projection past riser face
    "tread_overhang_side":  0.75,    # projection past stringer face each side
    "nosing_radius":        0.25,    # front-edge fillet radius
    "riser_thickness":      0.75,    # riser board thickness
}


# ---------------------------------------------------------------------------
# INPUT DIALOG
# ---------------------------------------------------------------------------
def get_parameters():
    """Show a multi-prompt dialog and return a params dict, or None if cancelled."""
    d = DEFAULTS

    prompts = [
        "Total rise",
        "Number of steps",
        "Unit run (tread depth, not incl. overhang)",
        "Stair width",
        "Stringer actual width (e.g. 11.25 for 2x12)",
        "Stringer actual thickness (e.g. 1.5)",
        "Tread board thickness (e.g. 1.5)",
        "Tread overhang front (nosing)",
        "Tread overhang side",
        "Nosing fillet radius",
        "Riser board thickness (e.g. 0.75)",
    ]

    defaults = [
        str(d["total_rise"]),
        str(d["num_steps"]),
        str(d["unit_run"]),
        str(d["stair_width"]),
        str(d["stringer_width"]),
        str(d["stringer_thickness"]),
        str(d["tread_thickness"]),
        str(d["tread_overhang_front"]),
        str(d["tread_overhang_side"]),
        str(d["nosing_radius"]),
        str(d["riser_thickness"]),
    ]

    results = rs.PropertyListBox(prompts, defaults, "Stair Parameters", "Generate Stairs")
    if results is None:
        return None

    try:
        params = {
            "total_rise":           float(results[0]),
            "num_steps":            int(results[1]),
            "unit_run":             float(results[2]),
            "stair_width":          float(results[3]),
            "stringer_width":       float(results[4]),
            "stringer_thickness":   float(results[5]),
            "tread_thickness":      float(results[6]),
            "tread_overhang_front": float(results[7]),
            "tread_overhang_side":  float(results[8]),
            "nosing_radius":        float(results[9]),
            "riser_thickness":      float(results[10]),
        }
    except (ValueError, IndexError) as e:
        rs.MessageBox("Invalid input: {}".format(e), title="Stair Generator")
        return None

    return params


# ---------------------------------------------------------------------------
# GEOMETRY HELPERS
# ---------------------------------------------------------------------------
def make_box_from_corners(x0, y0, z0, x1, y1, z1):
    """Return a Brep box from two opposite corner points."""
    pt_min = rg.Point3d(x0, y0, z0)
    pt_max = rg.Point3d(x1, y1, z1)
    box = rg.BoundingBox(pt_min, pt_max)
    return rg.Brep.CreateFromBox(box)


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


# ---------------------------------------------------------------------------
# STRINGER GEOMETRY
# ---------------------------------------------------------------------------
def build_stringer(p, side_x):
    """
    Build one stringer as a notched solid.

    The stringer runs from y=0 to y = num_steps * unit_run.
    It is notched for each tread/riser cutout.
    side_x: x position of the near (inner) face of the stringer.
    Returns a Brep.
    """
    n       = p["num_steps"]
    run     = p["unit_run"]
    rise    = p["total_rise"] / n
    sw      = p["stringer_width"]
    st      = p["stringer_thickness"]
    tw      = p["tread_thickness"]

    # Stringer outer bounding box (un-notched)
    # The stringer board is tilted, but for construction purposes we model it
    # as a vertical board cut to a stair profile (standard site-built method).
    # This is the profile in the Y-Z plane, extruded in X.

    # Build the stringer profile as a closed polyline in the YZ plane,
    # then extrude in X.
    pts = []

    # Start at bottom-left of stringer (front-bottom)
    # Walk up the stair profile, tracing the notched shape.
    # The notch removes a tread_thickness + nosing region horizontally
    # and one rise vertically at each step.

    # Bottom front corner
    pts.append(rg.Point3d(0, 0, 0))

    for i in range(n):
        y_riser_face = i * run          # face of riser at this step
        z_bottom     = i * rise         # bottom of tread at this step
        z_top        = z_bottom + tw    # top surface of tread
        y_tread_back = y_riser_face + run  # back of tread

        # Notch: go up the riser face, out along tread bottom
        pts.append(rg.Point3d(0, y_riser_face, z_bottom))
        pts.append(rg.Point3d(0, y_riser_face, z_top))
        pts.append(rg.Point3d(0, y_tread_back, z_top))

    # Top-back corner of last tread
    pts.append(rg.Point3d(0, n * run, n * rise))

    # Now trace the back/bottom of the stringer.
    # The stringer bottom follows the rake line. We simply close
    # back to origin via two points to form a closed polygon.
    pts.append(rg.Point3d(0, 0, 0 - (sw - tw)))   # bottom of stringer at front
    pts.append(pts[0])  # close

    # Build polyline curve in YZ plane
    profile = rg.PolylineCurve([rg.Point3d(pt.Y, pt.Z, 0) for pt in pts])

    # Extrude in X direction by stringer thickness
    extrude_dir = rg.Vector3d(st, 0, 0)
    extrusion = rg.Extrusion.Create(profile, st, True)
    if extrusion:
        brep = extrusion.ToBrep()
    else:
        brep = rg.Brep.CreateFromSurface(
            rg.PlaneSurface(rg.Plane.WorldXY, rg.Interval(0, 1), rg.Interval(0, 1))
        )

    # Transform: rotate profile from YZ to XZ plane and move to side_x
    xform = rg.Transform.Translation(side_x, 0, 0)
    brep.Transform(xform)

    return brep


def build_stringer_simple(p, x_near, x_far):
    """
    Builds a notched stringer by constructing a closed planar polyline
    in the plane x = x_near and extruding it in +X to x_far.

    The profile is built already trimmed:
      - bottom: cut by the horizontal floor plane z = 0
      - top:    cut by a vertical XZ plane at the stringer's last vertex,
                one full step beyond the top riser
    The notches sit under the treads and behind the risers, including the
    top riser, so the stringer's top flat is level with the total rise.
    """
    n    = p["num_steps"]
    run  = float(p["unit_run"])
    rise = float(p["total_rise"]) / float(n)
    sw   = float(p["stringer_width"])
    tw   = float(p["tread_thickness"])
    rt   = float(p["riser_thickness"])
    x0   = float(x_near)
    thickness = float(x_far) - x0

    if n < 2:
        print("  Warning: stringer needs at least 2 steps.")
        return None

    # Bottom edge of the stringer: parallel to the rake, offset
    # perpendicular to it by the stringer width. It passes through
    # (y_b0, z_b0) with slope rise/run.
    angle = math.atan2(rise, run)
    y_b0  = rt + sw * math.sin(angle)
    z_b0  = -tw - sw * math.cos(angle)
    slope = rise / run

    y_end   = (n + 1) * run + rt                    # top-end cut plane
    y_foot  = y_b0 + (0.0 - z_b0) / slope           # bottom edge meets floor
    z_heel  = z_b0 + (y_end - y_b0) * slope         # bottom edge meets top cut
    y_first = run + rt                              # back face of first riser

    if not (y_first < y_foot < y_end and z_heel < n * rise):
        print("  Warning: stringer too narrow/wide to trim for these parameters.")
        return None

    def pt(y, z):
        return rg.Point3d(x0, y, z)

    pts = []

    # Top (notched) edge: up behind each riser (including the top riser),
    # then along under each tread
    for i in range(1, n + 1):
        y = i * run + rt
        pts.append(pt(y, (i - 1) * rise))
        pts.append(pt(y, i * rise))

    # End of the last notch, then down the top-end cut to the bottom edge,
    # down the bottom edge to the floor, then along the floor back to the start
    pts.append(pt(y_end, n * rise))
    pts.append(pt(y_end, z_heel))
    pts.append(pt(y_foot, 0.0))
    pts.append(pts[0])

    # Build the polyline and convert to NurbsCurve for extrusion
    polyline = rg.Polyline(pts)
    if not polyline.IsValid:
        print("  Warning: stringer polyline is invalid.")
        return None

    curve = polyline.ToNurbsCurve()
    if curve is None:
        print("  Warning: could not convert stringer polyline to curve.")
        return None

    if not curve.IsClosed:
        curve.MakeClosed(0.001)

    # Extrude the curve in the X direction
    extrude_vec = rg.Vector3d(thickness, 0.0, 0.0)
    srf = rg.Surface.CreateExtrusion(curve, extrude_vec)
    if srf is None:
        print("  Warning: stringer extrusion failed.")
        return None
    brep = srf.ToBrep()

    # Cap the extrusion
    capped = brep.CapPlanarHoles(sc.doc.ModelAbsoluteTolerance)
    if capped:
        brep = capped

    return brep
# ---------------------------------------------------------------------------
# TREAD GEOMETRY  (with optional front-edge fillet)
# ---------------------------------------------------------------------------
def build_tread(p, step_index, x_left, x_right):
    """
    Build one tread as a box, optionally with a front-edge fillet.
    Returns a Brep.
    """
    n    = p["num_steps"]
    run  = p["unit_run"]
    rise = p["total_rise"] / n
    tt   = p["tread_thickness"]
    ovf  = p["tread_overhang_front"]
    ovs  = p["tread_overhang_side"]
    nr   = p["nosing_radius"]

    i = step_index

    # Tread extents
    y_riser_face = i * run
    y_front      = y_riser_face - ovf          # nosing projects past riser
    y_back       = y_riser_face + run          # back of tread (flush with next riser)
    z_bottom     = i * rise
    z_top        = z_bottom + tt

    xl = x_left  - ovs
    xr = x_right + ovs

    # Base box
    box_brep = make_box_from_corners(xl, y_front, z_bottom, xr, y_back, z_top)

    # Front-edge fillet (the nosing)
    if nr > 0.001 and box_brep is not None:
        # Find the front-top edge: highest Z, smallest Y
        target_y = y_front
        target_z = z_top
        best_edge = None
        best_dist = 1e9
        for edge in box_brep.Edges:
            mp = edge.PointAtNormalizedLength(0.5)
            dist = abs(mp.Y - target_y) + abs(mp.Z - target_z)
            if dist < best_dist:
                best_dist = dist
                best_edge = edge.EdgeIndex

        if best_edge is not None:
            filleted = rg.Brep.CreateFilletEdges(
                box_brep,
                [best_edge],
                [nr],
                [nr],
                rg.BlendType.Fillet,
                rg.RailType.DistanceFromEdge,
                0.001
            )
            if filleted and len(filleted) > 0:
                return filleted[0]

    return box_brep


# ---------------------------------------------------------------------------
# RISER GEOMETRY
# ---------------------------------------------------------------------------
def build_riser(p, step_index, x_left, x_right):
    """
    Build one riser board (riser_thickness thick, set at the riser face).
    Returns a Brep.
    """
    n    = p["num_steps"]
    run  = p["unit_run"]
    rise = p["total_rise"] / n
    rt   = p["riser_thickness"]

    i = step_index

    y_face   = i * run
    y_back   = y_face + rt
    z_bottom = (i - 1) * rise if i > 0 else 0.0
    z_top    = i * rise

    # Risers sit between stringers (no side overhang)
    return make_box_from_corners(x_left, y_face, z_bottom, x_right, y_back, z_top)


# ---------------------------------------------------------------------------
# LAYER SETUP
# ---------------------------------------------------------------------------
def setup_layers():
    layers = {
        "Stairs":            (80,  80,  80),
        "Stairs::Treads":    (200, 160, 120),
        "Stairs::Risers":    (220, 200, 180),
        "Stairs::Stringers": (140, 100, 60),
    }
    # Parent layer must exist before children
    for name, color in layers.items():
        if not rs.IsLayer(name):
            rs.AddLayer(name, rs.CreateColor(color[0], color[1], color[2]))


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def main():
    p = get_parameters()
    if p is None:
        return

    # Derived values
    n    = p["num_steps"]
    st   = p["stringer_thickness"]
    sw_  = p["stair_width"]
    ovs  = p["tread_overhang_side"]

    # X positions
    # Tread/riser region: x = st to st + sw_
    # Each stringer sits under the treads with its outer face flush with
    # the outer end of the risers.
    x_tread_left     = st
    x_tread_right    = st + sw_
    x_str_left_near  = x_tread_left
    x_str_left_far   = x_tread_left + st
    x_str_right_near = x_tread_right - st
    x_str_right_far  = x_tread_right

    setup_layers()

    rs.EnableRedraw(False)
    added = {"treads": 0, "risers": 0, "stringers": 0}

    # --- Treads (skip tread 0 — the stair starts with a riser at the floor) ---
    for i in range(1, n):
        brep = build_tread(p, i, x_tread_left, x_tread_right)
        if brep:
            add_brep(brep, "Stairs::Treads")
            added["treads"] += 1

    # --- Risers 1..n (riser n is the top riser, reaching the full total rise) ---
    for i in range(1, n + 1):
        brep = build_riser(p, i, x_tread_left, x_tread_right)
        if brep:
            add_brep(brep, "Stairs::Risers")
            added["risers"] += 1

    # --- Stringers ---
    for x_near, x_far in [(x_str_left_near, x_str_left_far),
                           (x_str_right_near, x_str_right_far)]:
        brep = build_stringer_simple(p, x_near, x_far)
        if brep:
            add_brep(brep, "Stairs::Stringers")
            added["stringers"] += 1

    rs.EnableRedraw(True)

    rise_per_step = p["total_rise"] / n
    rs.MessageBox(
        "Stair generated successfully.\n\n"
        "Steps:        {}\n"
        "Rise/step:    {:.4f}\n"
        "Run/step:     {:.4f}\n"
        "Total run:    {:.4f}\n"
        "Total rise:   {:.4f}\n\n"
        "Treads:    {}\n"
        "Risers:    {}\n"
        "Stringers: {}".format(
            n,
            rise_per_step,
            p["unit_run"],
            n * p["unit_run"],
            p["total_rise"],
            added["treads"],
            added["risers"],
            added["stringers"],
        ),
        title="Stair Generator — Done"
    )


if __name__ == "__main__":
    main()