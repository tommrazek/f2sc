"""Thin the dense piecewise-cubic splines of a STEP file that SolidCraft cannot import.

cadmpeg writes a helix (a modelled thread's edges and flanks) as cubic Hermite segments: a
B-spline whose interior knots all have multiplicity three, so each segment is a Bezier
curve. It doubles the segment count until the fit is far inside its tolerance; a thread of
14 turns gets 1024 segments. SolidCraft 0.49 refuses such an edge on a face that winds round
its axis ("an edge of a face that winds round its axis, whose curve has too many knot
spans"), and the body comes in as open surfaces (README, "SolidCraft issues").

`thin_step` merges neighbouring segments: each kept break keeps the curve's point and
derivative there, and the new segment between two kept breaks is the cubic Hermite
interpolant of them, as cadmpeg builds it. Merging stops at the coarsest level whose every
sample stays within `tol` (mm) of the original. Surfaces that are such a spline in one
direction and linear in the other (a thread flank) are merged the same way, row by row on
shared breaks. Every other entity is left as it is.

Only the standard library is used, like the rest of f2sc.
"""

from __future__ import annotations

import math
import os
import re
import sys

#: Splines with fewer segments than this are left alone.
MIN_SEGMENTS = 64
#: How far (mm) a thinned spline may stray from the original.
TOL = 1e-4
#: Samples per merged segment at which the deviation is measured.
SAMPLES = (0.125, 0.25, 0.375, 0.5, 0.625, 0.75, 0.875)

_ENTITY = re.compile(r"(?m)^#(\d+)\s*=\s*(.*?);\s*$", re.S)
_POINT = re.compile(r"CARTESIAN_POINT\s*\(\s*'[^']*'\s*,\s*\(\s*([^)]*)\)\s*\)")
_CURVE = re.compile(r"B_SPLINE_CURVE_WITH_KNOTS\s*\(\s*('[^']*')\s*,\s*3\s*,\s*\(([^()]*)\)\s*,\s*(\.\w+\.)\s*,"
                    r"\s*(\.\w\.)\s*,\s*(\.\w\.)\s*,\s*\(([^()]*)\)\s*,\s*\(([^()]*)\)\s*,\s*(\.\w+\.)\s*\)$", re.S)
_SURFACE = re.compile(r"B_SPLINE_SURFACE_WITH_KNOTS\s*\(\s*('[^']*')\s*,\s*1\s*,\s*3\s*,\s*\(\s*\(([^()]*)\)\s*,\s*\(([^()]*)\)\s*\)\s*,"
                      r"\s*(\.\w+\.)\s*,\s*(\.\w\.)\s*,\s*(\.\w\.)\s*,\s*(\.\w\.)\s*,\s*\(([^()]*)\)\s*,\s*\(([^()]*)\)\s*,"
                      r"\s*\(([^()]*)\)\s*,\s*\(([^()]*)\)\s*,\s*(\.\w+\.)\s*\)$", re.S)


def _ids(text):
    return [int(x.strip().lstrip("#")) for x in text.split(",")]


def _nums(text, kind=float):
    return [kind(x) for x in text.split(",")]


def _breaks(mults, knots):
    """The segment ends of a piecewise Bezier cubic, or None when it is not one."""
    if len(mults) != len(knots) or len(knots) < 2 or mults[0] != 4 or mults[-1] != 4 \
            or any(m != 3 for m in mults[1:-1]):
        return None
    return knots


def _bezier(b, s):
    u = 1.0 - s
    return [u * u * u * b[0][k] + 3 * u * u * s * b[1][k] + 3 * u * s * s * b[2][k] + s * s * s * b[3][k] for k in range(3)]


def _segments(poles):
    return [poles[3 * i:3 * i + 4] for i in range((len(poles) - 1) // 3)]


def _merge(poles, breaks, factor):
    """Poles of the spline whose segments each span `factor` of the old ones (the last may span
    fewer), the Hermite interpolant of the old curve's points and derivatives at the kept breaks."""
    segs = _segments(poles)
    n = len(segs)
    keep = list(range(0, n, factor)) + [n]
    out = [poles[0]]
    for a, b in zip(keep, keep[1:]):
        h_new = breaks[b] - breaks[a]
        # derivative by the parameter at the start of segment a and the end of segment b-1
        sa, sb = segs[a], segs[b - 1]
        ha, hb = breaks[a + 1] - breaks[a], breaks[b] - breaks[b - 1]
        d0 = [3 * (sa[1][k] - sa[0][k]) / ha for k in range(3)]
        d1 = [3 * (sb[3][k] - sb[2][k]) / hb for k in range(3)]
        p0, p1 = sa[0], sb[3]
        out += [[p0[k] + d0[k] * h_new / 3 for k in range(3)], [p1[k] - d1[k] * h_new / 3 for k in range(3)], list(p1)]
    return out, [breaks[i] for i in keep]


def _deviation(old, old_breaks, new, new_breaks):
    """Largest distance between the two curves at matching parameters, sampled per new segment."""
    worst = 0.0
    j = 0
    old_segs, new_segs = _segments(old), _segments(new)
    for i, seg in enumerate(new_segs):
        t0, t1 = new_breaks[i], new_breaks[i + 1]
        for s in SAMPLES:
            t = t0 + (t1 - t0) * s
            while j + 1 < len(old_segs) and old_breaks[j + 1] <= t:
                j += 1
            u = (t - old_breaks[j]) / (old_breaks[j + 1] - old_breaks[j])
            worst = max(worst, math.dist(_bezier(seg, s), _bezier(old_segs[j], u)))
            if worst > TOL:
                return worst
    return worst


def _thin(rows, breaks):
    """The coarsest merge of the pole rows (sharing `breaks`) within TOL: (rows, breaks) or None."""
    best = None
    factor = 2
    while len(breaks) - 1 >= factor * 2:
        merged = [_merge(r, breaks, factor) for r in rows]
        new_breaks = merged[0][1]
        if any(_deviation(r, breaks, m[0], new_breaks) > TOL for r, m in zip(rows, merged)):
            break
        best = ([m[0] for m in merged], new_breaks)
        factor *= 2
    return best


def _knot_text(breaks):
    mults = [4] + [3] * (len(breaks) - 2) + [4]
    return ",".join(str(m) for m in mults), ",".join(repr(float(t)) for t in breaks)


def thin_step(path: str) -> int:
    """Thin the STEP file at `path` in place; the number of splines thinned. A file it cannot
    read or thin is left as it is (0)."""
    try:
        return _thin_step(path)
    except (OSError, ValueError, IndexError, ZeroDivisionError, AttributeError) as e:
        print(f"f3d2sc: note: {os.path.basename(path)}: splines not thinned ({e})", file=sys.stderr)
        return 0


def _thin_step(path: str) -> int:
    with open(path, encoding="utf-8", errors="surrogateescape") as fh:
        text = fh.read()
    ents = {int(m.group(1)): m.group(2) for m in _ENTITY.finditer(text)}
    if not ents:
        return 0
    next_id = max(ents) + 1
    added: dict[int, str] = {}
    replaced: dict[int, str] = {}

    def point(i):
        m = _POINT.match(ents.get(i, ""))
        p = _nums(m.group(1)) if m else None
        return p if p is not None and len(p) == 3 else None  # 2D: a pcurve's, left alone

    def new_point(p):
        nonlocal next_id
        i = next_id
        next_id += 1
        added[i] = "CARTESIAN_POINT('',(%r,%r,%r))" % tuple(float(x) for x in p)
        return "#%d" % i

    for i, body in ents.items():
        flat = " ".join(body.split())
        if flat.startswith("B_SPLINE_CURVE_WITH_KNOTS"):
            m = _CURVE.match(flat)
            if not m:
                continue
            breaks = _breaks(_nums(m.group(6), int), _nums(m.group(7)))
            if breaks is None or len(breaks) - 1 < MIN_SEGMENTS:
                continue
            poles = [point(k) for k in _ids(m.group(2))]
            if any(p is None for p in poles) or len(poles) != 3 * (len(breaks) - 1) + 1:
                continue
            r = _thin([poles], breaks)
            if r is None:
                continue
            mults, knots = _knot_text(r[1])
            replaced[i] = "B_SPLINE_CURVE_WITH_KNOTS(%s,3,(%s),%s,%s,%s,(%s),(%s),%s)" % (
                m.group(1), ",".join(new_point(p) for p in r[0][0]), m.group(3), m.group(4), m.group(5), mults, knots, m.group(8))
        elif flat.startswith("B_SPLINE_SURFACE_WITH_KNOTS"):
            m = _SURFACE.match(flat)
            if not m:
                continue
            breaks = _breaks(_nums(m.group(9), int), _nums(m.group(11)))
            if breaks is None or len(breaks) - 1 < MIN_SEGMENTS:
                continue
            rows = [[point(k) for k in _ids(m.group(g))] for g in (2, 3)]
            if any(p is None for r_ in rows for p in r_) or any(len(r_) != 3 * (len(breaks) - 1) + 1 for r_ in rows):
                continue
            r = _thin(rows, breaks)
            if r is None:
                continue
            mults, knots = _knot_text(r[1])
            grid = ",".join("(" + ",".join(new_point(p) for p in row) + ")" for row in r[0])
            replaced[i] = "B_SPLINE_SURFACE_WITH_KNOTS(%s,1,3,(%s),%s,%s,%s,%s,(%s),(%s),(%s),(%s),%s)" % (
                m.group(1), grid, m.group(4), m.group(5), m.group(6), m.group(7), m.group(8), mults, m.group(10), knots, m.group(12))
    if not replaced:
        return 0
    # the old poles, unless something else still names them
    old = {int(x) for i in replaced for x in re.findall(r"#(\d+)", ents[i])}
    for i, body in ents.items():
        if i not in replaced and i not in old:
            old.difference_update(int(x) for x in re.findall(r"#(\d+)", body))
    old = {i for i in old if _POINT.match(ents.get(i, ""))}
    text = _ENTITY.sub(lambda mo: "#%s = %s;" % (mo.group(1), replaced[int(mo.group(1))]) if int(mo.group(1)) in replaced
                       else "" if int(mo.group(1)) in old else mo.group(0), text)
    text = re.sub(r"\n{2,}", "\n", text)
    end = text.rfind("ENDSEC;")
    text = text[:end] + "".join("#%d = %s;\n" % kv for kv in added.items()) + text[end:]
    with open(path, "w", encoding="utf-8", errors="surrogateescape") as fh:
        fh.write(text)
    return len(replaced)


if __name__ == "__main__":
    for p in sys.argv[1:]:
        print(p, thin_step(p))
