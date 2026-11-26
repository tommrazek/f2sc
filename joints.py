"""Fusion's assembly joints, rebuilt as SolidCraft joints.

cadmpeg decodes each joint (`assembly_joints`): its kind (fixed, slider, revolute, ...), its
limits and offsets, and its two connectors, each a frame in the coordinates of the occurrence
it sits on (or of a linked design, for a connector inside one). The converter places every
occurrence at its final pose, so each joint is made where the parts already stand
(`joint.as_built`), at the second connector's frame.

SolidCraft starts an as-built joint's value at 0 and moves part b along the joint's z as the
value grows. In Fusion the slide value v of a joint satisfies
    offset_z + v = -(B - A) . z_B
(A, B: the connectors' world origins; z_B: the second connector's z), so v grows as b moves
along -z_B. The joint is therefore made along -z_B, and Fusion's limits [min, max] become
[min - v, max - v]. Angular limits are not carried over (the current angle's sign convention
is not established).

SolidCraft grounds only the first part inserted. In each group of parts joined by joints the
part most often joined to stays grounded (a Fusion joint moves its first part onto its second),
the others are ungrounded so they move with their joints, and parts in no joint are grounded
where they stand.
"""

from __future__ import annotations

import math

#: Fusion joint kinds and SolidCraft joint types.
TYPES = {"fixed": "rigid", "rigid": "rigid", "slider": "slider", "revolute": "revolute",
         "cylindrical": "cylindrical", "ball": "ball", "planar": "planar", "pin_slot": "pin_slot"}


def _col(T, j):
    return [T[i][j] for i in range(3)]


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _rnd(v):
    return [round(x, 9) + 0.0 for x in v]


def external_connectors(m, occs) -> dict:
    """Linked-design connectors (`kind: external`): the document id -> the occurrence that
    brings it in. A joint made just after a Component Insert joins what that insert brought
    in, so each external document id goes to the latest insert before the joint's feature
    whose occurrence is not yet taken."""
    feats = sorted(m.get("features", []), key=lambda f: f.get("ordinal", 0))
    links = m.get("_insert_links", {})
    out, taken, last = {}, set(), []
    for f in feats:
        if f.get("suppressed"):
            continue
        d = f.get("definition") or {}
        if f.get("source_tag") == "Component Insert":
            o = links.get(f["id"]) or d.get("occurrence")
            if o in occs:
                last.append(o)
        elif d.get("definition") == "assembly_joint":
            j = next((j for j in m.get("assembly_joints", []) if j["id"] == d.get("joint")), None)
            for c in ((j or {}).get("operands", {}).get("value", {}).get("connectors") or []):
                ct = c["operand"]["container"]
                doc = (ct.get("external_document") or {}).get("document_id")
                if ct.get("kind") != "external" or not doc or doc in out:
                    continue
                cand = [o for o in reversed(last) if o not in taken]
                if cand:
                    out[doc] = cand[0]
                    taken.add(cand[0])
    return out


def joint_commands(m, occs, sc_name, world, notes) -> tuple[list[dict], list[str]]:
    """Commands making Fusion's joints between the inserted occurrences, and the names of the
    occurrences to unground first. sc_name: occurrence id -> SolidCraft occurrence ("Name:1");
    world(o): an occurrence's world transform (3x4, mm)."""
    ext = external_connectors(m, occs)
    made, groups, skipped = [], [], {}
    for j in m.get("assembly_joints", []):
        if j.get("suppressed"):
            continue
        val = (j.get("operands") or {}).get("value") or {}
        kind = val.get("kind") or {}
        typ = TYPES.get(kind.get("kind"))
        cons = val.get("connectors") or []
        if typ is None or len(cons) != 2:
            skipped[kind.get("kind") or "?"] = skipped.get(kind.get("kind") or "?", 0) + 1
            continue
        ends = []
        for c in cons:
            ct = c["operand"]["container"]
            oid = ct.get("occurrence") if ct.get("kind") == "occurrence" else \
                ext.get((ct.get("external_document") or {}).get("document_id"))
            if oid not in sc_name or not c.get("frame"):
                break
            T = world(occs[oid])
            F = c["frame"]
            W = [[sum(T[i][k] * F[k][jj] for k in range(3)) + (T[i][3] if jj == 3 else 0.0) for jj in range(4)]
                 for i in range(3)]
            ends.append((oid, W))
        if len(ends) != 2:
            skipped["on a part not inserted"] = skipped.get("on a part not inserted", 0) + 1
            continue
        (oa, A), (ob, B) = ends
        if oa == ob:
            continue
        zb = _col(B, 2)
        z = [-x for x in zb]
        x = _col(B, 0)
        params = {"type": typ, "a": sc_name[oa], "b": sc_name[ob],
                  "at": {"occurrence": sc_name[ob], "point": _rnd(_col(B, 3)), "z": _rnd(z), "x": _rnd(x)},
                  "name": f"Joint {len(made) + 1}", "quiet": True}
        lim = kind.get("linear_limits")
        if typ in ("slider", "cylindrical") and isinstance(lim, dict) and lim.get("kind") == "range":
            off = (kind.get("translation_offset") or [0.0, 0.0, 0.0])[2]
            d = [B[i][3] - A[i][3] for i in range(3)]
            v = -_dot(d, zb) - off
            lo, hi = lim.get("minimum"), lim.get("maximum")
            if isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and lo - 1e-6 <= v <= hi + 1e-6:
                params["limits"] = [[round(lo - v, 9), round(hi - v, 9)]]
            else:
                notes.append(f"joint {len(made) + 1}: its current slide {v:.4g} mm lies outside Fusion's limits "
                             f"[{lo}, {hi}]; limits not carried over")
        made.append({"command": "joint.as_built", "params": params})
        groups.append({oa, ob})
    # Groups of parts joined to each other. In Fusion a joint moves its first part onto its
    # second, so each group stays grounded at the part most often joined to (ties: Fusion's
    # order) and the rest are ungrounded to move with their joints. Parts in no joint stay
    # grounded where they are (SolidCraft grounds only the first part inserted).
    merged: list[set] = []
    for g in groups:
        hit = [s for s in merged if s & g]
        new = set(g).union(*hit) if hit else set(g)
        merged = [s for s in merged if not (s & g)] + [new]
    order = {o["id"]: (o.get("ordinal", 0), o["id"]) for o in occs.values()}
    score: dict = {}
    for g, c in zip(groups, made):
        a_, b_ = sorted(g, key=lambda o_: c["params"]["a"] != sc_name[o_])  # (a, b)
        score[b_] = score.get(b_, 0) + 1
        score[a_] = score.get(a_, 0) - 1
    free, anchors = [], []
    for s in merged:
        ranked = sorted(s, key=lambda o_: (-score.get(o_, 0), order.get(o_, (0, o_))))
        anchors.append(sc_name[ranked[0]])
        free += [sc_name[o_] for o_ in ranked[1:]]
    joined = {o_ for s in merged for o_ in s}
    fixed = anchors + [n for o_, n in sc_name.items() if o_ not in joined]
    if made:
        notes.append(f"{len(made)} joint(s) made as built; grounded: {', '.join(n.rsplit(':', 1)[0] for n in anchors)} "
                     f"and the parts in no joint; {len(free)} part(s) move with their joints")
    if skipped:
        notes.append("joints not made: " + ", ".join(f"{n} {k}" for k, n in sorted(skipped.items())))
    if not made:
        return [], []
    ground = [{"command": "occurrence.ground", "params": {"occurrence": n, "grounded": True}} for n in fixed] + \
             [{"command": "occurrence.ground", "params": {"occurrence": n, "grounded": False}} for n in free]
    return ground + made, free
