# rhino-scripts

A collection of scripts for [Rhino](https://www.rhino3d.com/), used mainly for architectural modeling.

## Rhino 7 vs. Rhino 8

Rhino's scripting engine changed between versions, and scripts are **not** automatically cross-compatible:

- **Rhino 7** runs scripts on IronPython 2.7 (Python 2). Use `rhinoscriptsyntax` / `Rhino.Geometry` via the classic Python 2 script editor (`EditPythonScript`) or `RunPythonScript`.
- **Rhino 8** adds a CPython 3 script engine alongside the legacy one. New scripts can target Python 3, but Python 2 scripts still run fine under the legacy engine for backward compatibility.

Each script below notes which engine(s) it targets. When in doubt, check the header comment at the top of the script file — it states the intended Rhino version and Python engine.

## Scripts

| Script | Rhino / Python | Description |
| --- | --- | --- |
| [`stair_generator.py`](stair_generator.py) | Rhino 7/8 (Python 2 & 3 compatible) | Parametric stair generator. Prompts for total rise, step count, tread run, stair width, stringer dimensions, and nosing/overhang details, then builds treads, risers, and notched stringers as Rhino geometry on organized layers. |
| [`retire_layer.py`](retire_layer.py) | Rhino 7 (IronPython 2.7) | Utility to retire a layer by moving all its contents — including objects buried inside nested block definitions — to a target layer, then deletes the source layer if it ends up empty. Skips linked (externally-referenced) blocks and reports them. |
| [`wall_generator.py`](wall_generator.py) | Rhino 7/8 (Python 2 & 3 compatible) | Converts closed floor-plan curves into extruded 3D wall solids, then cuts door and window openings out of them via boolean difference using separate opening curves. Prompts for wall/door/window curves in sequence and for wall/door/window heights. |

## Usage

Run a script from Rhino via:

- `EditPythonScript` (or `RunPythonScript`) and browse to the file, or
- an alias/toolbar button, e.g. `-_RunPythonScript "C:\path\to\script.py"`

See each script's header comment for specific parameters and caveats.
