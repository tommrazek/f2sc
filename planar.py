"""Planar arrangement of sketch curves: the closed regions they bound, found the way a CAD
sketch finds profiles (curves split where they meet or overlap, dangling pieces ignored).

cadmpeg builds a sketch's profiles from whole curves, so a region bounded partly by a piece of
a longer line (a triangle drawn on an existing edge) gets no profile there; this finds it.
Curves come in as polylines; regions go out as polygons, counter-clockwise."""
from __future__ import annotations

import math

SNAP = 1e-7


def _key(p):
    return (round(p[0] / SNAP), round(p[1] / SNAP))


def _seg_hits(p, q, r, s):
    """Parameters along p-q where segment r-s meets it (crossing, touching or overlapping)."""
    d1 = (q[0] - p[0], q[1] - p[1])
    d2 = (s[0] - r[0], s[1] - r[1])
    den = d1[0] * d2[1] - d1[1] * d2[0]
    L1 = d1[0] ** 2 + d1[1] ** 2
    if L1 == 0:
        return []
    w = (r[0] - p[0], r[1] - p[1])
    if abs(den) < 1e-14 * max(1.0, L1):
        # parallel: collinear overlap puts the other segment's ends on this one
        if abs(w[0] * d1[1] - w[1] * d1[0]) > 1e-9 * math.sqrt(L1):
            return []
        out = []
        for e in (r, s):
            t = ((e[0] - p[0]) * d1[0] + (e[1] - p[1]) * d1[1]) / L1
            if -1e-12 < t < 1 + 1e-12:
                out.append(min(1.0, max(0.0, t)))
        return out
    t = (w[0] * d2[1] - w[1] * d2[0]) / den
    u = (w[0] * d1[1] - w[1] * d1[0]) / den
    e = 1e-9
    if -e <= t <= 1 + e and -e <= u <= 1 + e:
        return [min(1.0, max(0.0, t))]
    return []


def regions(polylines):
    """Bounded faces of the arrangement of `polylines` (lists of [x, y]): a list of
    counter-clockwise polygons, each with its signed area (> 0)."""
    segs = []
    for pl in polylines:
        for a, b in zip(pl, pl[1:]):
            if math.dist(a, b) > SNAP:
                segs.append((tuple(a), tuple(b)))
    n = len(segs)
    boxes = [(min(a[0], b[0]), max(a[0], b[0]), min(a[1], b[1]), max(a[1], b[1])) for a, b in segs]
    cuts = [{0.0, 1.0} for _ in segs]
    order = sorted(range(n), key=lambda i: boxes[i][0])
    for ii, i in enumerate(order):
        bi = boxes[i]
        for j in order[ii + 1:]:
            bj = boxes[j]
            if bj[0] > bi[1] + 1e-9:
                break
            if bj[2] > bi[3] + 1e-9 or bj[3] < bi[2] - 1e-9:
                continue
            (p, q), (r, s) = segs[i], segs[j]
            cuts[i].update(_seg_hits(p, q, r, s))
            cuts[j].update(_seg_hits(r, s, p, q))
    pts: dict = {}
    edges = set()
    for (p, q), ts in zip(segs, cuts):
        ts = sorted(ts)
        chain = [(p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t) for t in ts]
        for a, b in zip(chain, chain[1:]):
            ka, kb = _key(a), _key(b)
            if ka == kb:
                continue
            pts.setdefault(ka, a)
            pts.setdefault(kb, b)
            edges.add((ka, kb))
            edges.add((kb, ka))
    # Drop dangling edges (a vertex of degree 1) until none are left.
    out: dict = {}
    for a, b in edges:
        out.setdefault(a, set()).add(b)
    changed = True
    while changed:
        changed = False
        for v in [v for v, nb in out.items() if len(nb) < 2]:
            for w in out.pop(v, ()):
                if w in out:
                    out[w].discard(v)
            changed = True
    ang = {}
    for v, nb in out.items():
        pv = pts[v]
        ang[v] = sorted(nb, key=lambda w: math.atan2(pts[w][1] - pv[1], pts[w][0] - pv[0]))
    seen, faces = set(), []
    for v in out:
        for w in out[v]:
            if (v, w) in seen:
                continue
            cyc, a, b = [], v, w
            while (a, b) not in seen:
                seen.add((a, b))
                cyc.append(pts[a])
                ring = ang[b]
                k = ring.index(a)
                a, b = b, ring[(k - 1) % len(ring)]  # the next edge clockwise from b->a: face on the left
                if len(cyc) > 100000:
                    break
            area = 0.5 * sum(p[0] * q[1] - q[0] * p[1] for p, q in zip(cyc, cyc[1:] + cyc[:1]))
            if area > 1e-12:
                faces.append((cyc, area))
    return faces


def segments(poly):
    return list(zip(poly, poly[1:] + poly[:1]))
