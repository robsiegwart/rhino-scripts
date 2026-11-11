"""
Board-and-batten generator -- GhPython component (Rhino 7 / IronPython 2.7)

Lays out battens on planar wall faces. Each batten is trimmed to the face
outline, its openings and any extra opening curves, then extruded outward.

INPUTS  (right-click each input: set Type hint and Access)
  Walls      Brep      List   Exterior wall surfaces or polysurfaces. Closed massing is OK;
                              roof and floor faces are skipped automatically.
  Width      float     Item   Batten face width.                      Default 2.5 in
  Thick      float     Item   Batten thickness (projection off wall). Default 0.75 in
  Spacing    float     Item   Center-to-center spacing.               Default 16 in
  Margin     float     Item   Keep-out at the left/right edges of each face
                              (corner board width). Default 0
  Clear      float     Item   Clearance added around openings (casing width). Default 0
  Justify    int       Item   0 = start at left edge, 1 = centered, 2 = start at right edge.
                              Left/right as seen from outside. Default 1
  Anchor     Point3d   Item   Optional. Battens are centered on a grid through this point,
                              overriding Justify. Use it to line up battens across faces.
  Openings   Curve     List   Optional closed curves drawn on wall planes, cut out like holes.
                              Curves may cross a face edge (door notches).
  Direction  Vector3d  Item   Batten direction. Default world Z.
  Interior   Point3d   Item   Optional point inside the building. Each face is oriented away
                              from it. Recommended for loose surfaces with unreliable normals.
  Flip       bool      Item   Reverse all face normals (ignored when Interior is given).
  MinLen     float     Item   Drop pieces shorter than this. Default = Width
  FlipFaces  str       List   OPTIONAL INPUT (works without adding it). Faces to reverse,
                              as 'brep;face' tags read from Info, e.g. 0;2

OUTPUTS
  Battens    tree {brep;face}  Batten solids
  Lengths    tree {brep;face}  Long-point length of each piece along the batten direction
  Frames     list              Layout plane per processed face (Z = outward, X = left to right)
  Info       list              Summary, per-face counts, warnings

All lengths are in model units. Defaults are converted from inches.
Battens sit on the input surface, so feed the face they mount to (board/sheathing face).
"""

import math
import System
import Rhino
import Rhino.Geometry as rg
import rhinoscriptsyntax as rs
import Grasshopper
from Grasshopper import DataTree
from Grasshopper.Kernel.Data import GH_Path

# ---------------- advanced settings ----------------
MAX_TILT = 0.26             # skip faces where |Direction . normal| > this (~15 deg off batten direction)
SLIVER_FRAC = 0.5           # drop pieces narrower than this fraction of Width
MERGE_COPLANAR = True       # join inputs and merge coplanar faces (wall + gable share one layout)
OPENING_PLANE_TOL_IN = 1.0  # Openings curves must lie within this distance (inches) of a face plane
RETRY_INSET = 20            # on a failed curve boolean, retry with the strip inset by this many tolerances

doc = Rhino.RhinoDoc.ActiveDoc
tol = doc.ModelAbsoluteTolerance
in2m = Rhino.RhinoMath.UnitScale(Rhino.UnitSystem.Inches, doc.ModelUnitSystem)
m2ft = Rhino.RhinoMath.UnitScale(doc.ModelUnitSystem, Rhino.UnitSystem.Feet)
REL = rg.RegionContainment


def warn(msg):
    ghenv.Component.AddRuntimeMessage(
        Grasshopper.Kernel.GH_RuntimeMessageLevel.Warning, msg)


def num(v, default):
    return default if v is None else float(v)


def as_list(v):
    """Accept Item or List access: None -> [], single object -> [obj]."""
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return list(v)
    if isinstance(v, (rg.GeometryBase, System.Guid, str, int, float)):
        return [v]
    try:
        return list(v)
    except TypeError:
        return [v]


# ---------------- coercion ----------------
def to_brep(obj):
    if obj is None:
        return None
    if isinstance(obj, System.Guid):
        obj = rs.coercegeometry(obj)
    if isinstance(obj, rg.Brep):
        return obj.DuplicateBrep()
    if isinstance(obj, rg.Extrusion):       # before Surface: Extrusion is a Surface
        return obj.ToBrep(True)
    if isinstance(obj, rg.BrepFace):        # before Surface: BrepFace is a Surface
        return obj.DuplicateFace(False)
    if isinstance(obj, rg.Surface):
        return obj.ToBrep()
    return None


def to_curve(obj):
    if obj is None:
        return None
    if isinstance(obj, System.Guid):
        obj = rs.coercecurve(obj)
    return obj if isinstance(obj, rg.Curve) else None


# ---------------- geometry helpers ----------------
def face_normal(face):
    """Face normal honoring face orientation (NormalAt alone ignores it)."""
    n = face.NormalAt(face.Domain(0).Mid, face.Domain(1).Mid)
    if face.OrientationIsReversed:
        n.Reverse()
    n.Unitize()
    return n


def mesh_normals(brep):
    """
    Per-face outward normals from Rhino's meshing, which orients each mesh by its
    face orientation (the direction Dir shows). Returns None if meshing fails.
    """
    try:
        meshes = rg.Mesh.CreateFromBrep(brep, rg.MeshingParameters.Coarse)
    except Exception:
        return None
    if meshes is None or len(meshes) != brep.Faces.Count:
        return None
    out = []
    for m in meshes:
        m.FaceNormals.ComputeFaceNormals()
        v = rg.Vector3d(0, 0, 0)
        for i in range(m.FaceNormals.Count):
            v = v + rg.Vector3d(m.FaceNormals[i])
        out.append(v if v.Unitize() else None)
    return out


def parse_face_tags(v):
    """FlipFaces entries: '0;2' or '{0;2}' (brep;face), or an int face index on brep 0."""
    tags = set()
    for item in as_list(v):
        s = str(item).strip().strip("{}")
        try:
            if ";" in s:
                a, b = s.split(";")
                tags.add((int(a), int(b)))
            else:
                tags.add((0, int(float(s))))
        except ValueError:
            warn("FlipFaces: could not read '%s'" % item)
    return tags


def plane_bbox(geo, xf):
    """Bounding box of geo expressed in layout-plane coordinates."""
    dup = geo.Duplicate()
    dup.Transform(xf)
    return dup.GetBoundingBox(True)


def curve_area(c):
    amp = rg.AreaMassProperties.Compute(c)
    return amp.Area if amp else 0.0


def offset_out(crv, plane, dist):
    """Offset a closed planar curve outward (picks the larger-area result)."""
    best, best_a = crv, curve_area(crv)
    for d in (dist, -dist):
        res = crv.Offset(plane, d, tol, rg.CurveOffsetCornerStyle.Sharp)
        for r in (res or []):
            if r.IsClosed:
                a = curve_area(r)
                if a > best_a:
                    best, best_a = r, a
    return best


def relation(a, b, plane):
    return rg.Curve.PlanarClosedCurveRelationship(a, b, plane, tol)


def centers(a, b, plane, W, S, J, anchor):
    """Batten centerline x-positions (plane coords) with each batten fully inside [a, b]."""
    if b - a < W - tol:
        return []
    if anchor is not None:
        ok, s, t = plane.ClosestParameter(anchor)
        k0 = int(math.ceil((a + W / 2.0 - tol - s) / S))
        k1 = int(math.floor((b - W / 2.0 + tol - s) / S))
        return [s + k * S for k in range(k0, k1 + 1)]
    n = int(math.floor((b - a - W + tol) / S)) + 1
    span = (n - 1) * S + W
    if J == 0:
        first = a + W / 2.0
    elif J == 2:
        first = b - span + W / 2.0
    else:
        first = a + (b - a - span) / 2.0 + W / 2.0
    return [first + i * S for i in range(n)]


def cut_strip(xa, xb, fx0, fx1, y0, y1, plane, outer, holes, W):
    """
    Trim the strip xa..xb to the face outline minus holes.
    Returns (closed curves, ok). Curves may include holes fully inside a piece;
    CreatePlanarBreps nests them.
    """
    pad = (y1 - y0) * 0.1 + W
    # Push strip sides past the face extremes where they touch, so they never
    # coincide with the face's own side edges (coincident edges break curve booleans).
    ra = xa if xa > fx0 + tol else fx0 - pad
    rb = xb if xb < fx1 - tol else fx1 + pad
    rect = rg.Rectangle3d(plane, rg.Interval(ra, rb),
                          rg.Interval(y0 - pad, y1 + pad)).ToNurbsCurve()

    r = relation(rect, outer, plane)
    if r == REL.Disjoint:
        return [], True
    if r == REL.BInsideA:                    # whole face fits within the strip
        pieces = [outer.DuplicateCurve()]
    elif r == REL.MutualIntersection:
        pieces = list(rg.Curve.CreateBooleanIntersection(rect, outer, tol) or [])
        if not pieces:
            return [], False
    else:                                    # AInsideB: not expected given the padding
        pieces = [rect]

    contained = []
    for h in holes:
        nxt = []
        for p in pieces:
            r = relation(p, h, plane)
            if r == REL.Disjoint:
                nxt.append(p)
            elif r == REL.AInsideB:          # piece lies entirely in the opening
                continue
            elif r == REL.BInsideA:          # opening lies entirely within the piece
                nxt.append(p)
                contained.append(h)
            else:
                res = rg.Curve.CreateBooleanDifference(p, h, tol)
                if not res:
                    return [], False
                nxt.extend(res)
        pieces = nxt
    return pieces + contained, True


# ---------------- main ----------------
def run():
    W = num(Width, 2.5 * in2m)
    T = num(Thick, 0.75 * in2m)
    S = num(Spacing, 16.0 * in2m)
    M = num(Margin, 0.0)
    G = num(Clear, 0.0)
    J = 1 if Justify is None else int(Justify)
    L_MIN = num(MinLen, W)
    D = rg.Vector3d.ZAxis if (Direction is None or Direction.IsTiny()) else rg.Vector3d(Direction)
    D.Unitize()
    flip = bool(Flip) if Flip is not None else False

    battens = DataTree[object]()
    lengths = DataTree[object]()
    frames = []
    log = []

    if W <= 0 or T <= 0:
        warn("Width and Thick must be positive.")
        return battens, lengths, frames, ["Invalid Width/Thick"]
    if S <= W:
        warn("Spacing must exceed Width.")
        return battens, lengths, frames, ["Invalid Spacing"]

    breps = [b for b in (to_brep(x) for x in as_list(Walls)) if b is not None]
    if not breps:
        return battens, lengths, frames, ["No wall geometry"]

    if MERGE_COPLANAR:
        joined = rg.Brep.JoinBreps(breps, tol)
        if joined:
            breps = list(joined)
        for b in breps:
            b.MergeCoplanarFaces(tol)

    for b in breps:
        if b.IsSolid and b.SolidOrientation == rg.BrepSolidOrientation.Inward:
            b.Flip()

    ext_curves = [c for c in (to_curve(x) for x in as_list(Openings))
                  if c is not None and c.IsClosed]
    plane_lim = OPENING_PLANE_TOL_IN * in2m + tol

    total_len = 0.0
    count = 0
    skipped_tilt = 0
    skipped_nonplanar = 0

    flip_faces = parse_face_tags(globals().get("FlipFaces"))

    for bi, b in enumerate(breps):
        mnorms = mesh_normals(b)
        for face in b.Faces:
            tag = "{%d;%d}" % (bi, face.FaceIndex)
            ok, fpl = face.TryGetPlane(tol)
            if not ok:
                skipped_nonplanar += 1
                continue

            # outward normal: mesh normal (follows face orientation, as Dir shows it),
            # falling back to NormalAt + OrientationIsReversed; snapped to the exact plane normal
            fn = face_normal(face)
            mn = mnorms[face.FaceIndex] if mnorms else None
            ref = mn if mn is not None else fn
            src = "mesh" if mn is not None else "NormalAt"
            if mn is not None and mn * fn < 0:
                src += ", NormalAt disagreed"
            n = fpl.Normal if fpl.Normal * ref > 0 else -fpl.Normal
            fbrep = face.DuplicateFace(False)
            amp = rg.AreaMassProperties.Compute(fbrep)
            ctr = fpl.ClosestPoint(amp.Centroid if amp else fpl.Origin)
            if Interior is not None:
                if (ctr - Interior) * n < 0:
                    n = -n
                    src += ", Interior"
            elif flip:
                n = -n
                src += ", Flip"
            if (bi, face.FaceIndex) in flip_faces:
                n = -n
                src += ", FlipFaces"

            dn = D * n
            if abs(dn) > MAX_TILT:
                skipped_tilt += 1
                continue

            # layout frame: Y along battens, X left-to-right seen from outside, Z outward
            y = D - n * dn
            y.Unitize()
            x = rg.Vector3d.CrossProduct(y, n)
            plane = rg.Plane(ctr, x, y)
            frames.append(plane)
            xf = rg.Transform.PlaneToPlane(plane, rg.Plane.WorldXY)

            fb = plane_bbox(fbrep, xf)
            fx0, fx1, y0, y1 = fb.Min.X, fb.Max.X, fb.Min.Y, fb.Max.Y

            ol = face.OuterLoop.To3dCurve()
            if ol is None:
                log.append(tag + " skipped: bad outer loop")
                continue
            outer = rg.Curve.ProjectToPlane(ol, plane)

            holes = []
            for lp in face.Loops:
                if lp.LoopType == rg.BrepLoopType.Inner:
                    c = lp.To3dCurve()
                    if c is not None:
                        holes.append(rg.Curve.ProjectToPlane(c, plane))
            for c in ext_curves:
                cb = plane_bbox(c, xf)
                if abs(cb.Min.Z) <= plane_lim and abs(cb.Max.Z) <= plane_lim:
                    holes.append(rg.Curve.ProjectToPlane(c, plane))
            if G > tol:
                holes = [offset_out(h, plane, G) for h in holes]

            path = GH_Path(bi, face.FaceIndex)
            nface = 0
            for xc in centers(fx0 + M, fx1 - M, plane, W, S, J, Anchor):
                xa, xb = xc - W / 2.0, xc + W / 2.0
                crvs, ok = cut_strip(xa, xb, fx0, fx1, y0, y1, plane, outer, holes, W)
                if not ok:
                    e = RETRY_INSET * tol
                    crvs, ok = cut_strip(xa + e, xb - e, fx0, fx1, y0, y1,
                                         plane, outer, holes, W)
                if not ok:
                    log.append("%s batten at x=%.3f: trim failed" % (tag, xc))
                    continue
                if not crvs:
                    continue

                regions = rg.Brep.CreatePlanarBreps(crvs, tol)
                if not regions:
                    log.append("%s batten at x=%.3f: planar region failed" % (tag, xc))
                    continue

                for pb in regions:
                    pbb = plane_bbox(pb, xf)
                    dx = pbb.Max.X - pbb.Min.X
                    dy = pbb.Max.Y - pbb.Min.Y
                    if dx < SLIVER_FRAC * W or dy < L_MIN - tol:
                        continue
                    c0 = plane.PointAt((pbb.Min.X + pbb.Max.X) / 2.0,
                                       (pbb.Min.Y + pbb.Max.Y) / 2.0)
                    solid = pb.Faces[0].CreateExtrusion(rg.LineCurve(c0, c0 + n * T), True)
                    if solid is None:
                        log.append("%s batten at x=%.3f: extrusion failed" % (tag, xc))
                        continue
                    if solid.IsSolid and solid.SolidOrientation == rg.BrepSolidOrientation.Inward:
                        solid.Flip()
                    battens.Add(solid, path)
                    lengths.Add(dy, path)
                    total_len += dy
                    count += 1
                    nface += 1

            log.append("%s %d pieces  outward (%.2f, %.2f, %.2f)  [%s]"
                       % (tag, nface, n.X, n.Y, n.Z, src))

    info = [
        "Pieces: %d" % count,
        "Total length: %.2f model units (%.1f ft)" % (total_len, total_len * m2ft),
        "W %.4f  T %.4f  S %.4f  (model units)" % (W, T, S),
    ]
    if skipped_tilt:
        info.append("Skipped %d faces not parallel to batten direction (roof/floor/soffit)" % skipped_tilt)
    if skipped_nonplanar:
        info.append("Skipped %d non-planar faces" % skipped_nonplanar)
        warn("%d non-planar faces skipped" % skipped_nonplanar)
    if any("failed" in s for s in log):
        warn("Some battens failed to trim; see Info")
    return battens, lengths, frames, info + log


Battens, Lengths, Frames, Info = run()
