#!/usr/bin/env python3
"""f3d2sc: turn a Fusion .f3d design into a SolidCraft command script.

Pipeline:
  1. f3d2sc-decode (Rust, cadmpeg) decodes the .f3d into cadmpeg's neutral IR as JSON, extracts
     the design streams, and can write the final B-rep as STEP for reference measurements.
  2. This script walks the timeline and emits SolidCraft commands ({"commands": [...]}, as
     `solidcraft-cli run` replays). Parameters cadmpeg leaves native are read from the design
     stream (fusion_raw.py).
  3. --check runs the script step by step with solidcraft-cli and compares each feature's bodies
     with the body centres Fusion recorded after that feature.

Bodies: Fusion numbers bodies per component ("slots"); cadmpeg reports, for each feature, the
slots it affected and every slot's centre afterwards. The script names SolidCraft bodies after
them: "Body 3" in the first component, "C2 Body 3" in the second, and so on.

Usage:
  f3d2sc.py design.f3d [-o design.solidcraft.json] [--step ref.step] [--check SOLIDCRAFT_CLI]
  f3d2sc.py --ir design.ir.json ...   (use an IR dumped earlier; its .streams folder beside it)
"""
from __future__ import annotations

import argparse
import json
import struct
import math
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import planar
from fusion_raw import CM, Raw  # noqa: E402
from inference import HEURISTICS, Inference  # noqa: E402
import diagnose as diag  # noqa: E402
import ir_compat  # noqa: E402
from step_thin import thin_step  # noqa: E402
import constraints  # noqa: E402
import joints  # noqa: E402

EPS = 1e-6  # mm: sketch endpoints closer than this are one point
CENTRE_TOL = 1e-3  # mm: body centre agreement in --check
APPROX_TOL = 2.0  # mm: centres cadmpeg marks inexact (loose boxes) may differ up to this
SC_BASELINE = (0, 40, 1)  # the oldest SolidCraft the scripts are written for (without --solidcraft)
SC_KNOTS = (0, 49, 0)  # sketch.spline.control_knots; dense splines extrude (SolidCraft issue 1)
SC_THREADED_CUT = (0, 49, 0)  # a cut by a threaded tool keeps its target (SolidCraft issue 3)
SPLINE_TOL = 1e-4  # mm: default deviation allowed when refitting a dense spline
DENSE_POLE_GAP = 1e-3  # mm: control points closer than this mark a spline as dense


# Reference forms in Fusion's body operands: 3 and 33 name bodies, 4 names faces.
BODY_FORMS = (3, 33)


class DecodeError(Exception):
    """cadmpeg could not decode the file."""


class Gap(Exception):
    """A feature the converter cannot express (yet)."""


def short(ref) -> str:
    return ref.split(":")[-1] if isinstance(ref, str) else str(ref)


def parse_centres(s: str | None) -> dict[int, tuple]:
    out = {}
    for part in (s or "").split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            out[int(k)] = tuple(float(x) for x in v.split(","))
    return out


BORE_CUT_OVERRUN = 1e-3  # mm a bore-widening cut runs past each end of the face it widens


def reflect_axis(a, plane):
    """An axis (point, direction) mirrored in plane {origin, normal}."""
    pt, d = a
    n = plane["normal"]
    L = math.sqrt(sum(x * x for x in n))
    n = [x / L for x in n]
    k = sum((p_ - o) * m for p_, o, m in zip(pt, plane["origin"], n))
    j = sum(x * m for x, m in zip(d, n))
    return [p_ - 2 * k * m for p_, m in zip(pt, n)], [x - 2 * j * m for x, m in zip(d, n)]


def close(a, b, tol=1e-6):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


class Namespace:
    def __init__(self, index: int):
        self.index = index
        self.state: dict[int, tuple] = {}
        self.names: dict[int, str] = {}
        self.events: list[tuple[int, list[int]]] = []  # (creating scope record, new slots)

    def name(self, slot: int) -> str:
        return f"Body {slot}" if self.index == 0 else f"C{self.index + 1} Body {slot}"


class Converter:
    def __init__(self, ir: dict, bulk: bytes | None, spline_tol: float | None = SPLINE_TOL, derive_step=None, infer=None,
                 refused=None, unvalidated=None):
        self.spline_tol = spline_tol
        self.sc_cli = None  # solidcraft-cli, for inferences that measure the final B-rep
        self.constraints_on = True  # carry sketch constraints over (constraints.py; needs sc_cli)
        self.sketch_probe = None
        self.sc_target = SC_BASELINE  # the SolidCraft version the script is written for
        self.script_dir = None
        self.refused = refused or {}  # timeline ordinal -> SolidCraft's error when it ran the feature
        self.unvalidated = unvalidated or {}  # timeline ordinal -> SMLib self-check message: run unchecked
        self.offset_by_cut: set = set()  # timeline ordinals whose bore widening runs as a cylinder cut
        self.infer = infer or Inference()
        self.cur = None  # the feature being converted
        # derive_step(final body index, name) -> path of a STEP holding only that body
        self.derive_step = derive_step
        self.ir = ir
        self.m = ir["model"]
        self.raw = Raw(ir.get("native", {}).get("f3d", {}), bulk) if bulk else None
        self.cmds: list[dict] = []
        self.gaps: list[dict] = []
        self.notes: list[str] = []
        self.checks: list[dict] = []  # {feature, upto, expect: {body: centre}}
        self.sketch_by_id = {s["id"]: s for s in self.m.get("sketches", [])}
        self.entities_by_sketch: dict[str, list[dict]] = {}
        for e in self.m.get("sketch_entities", []):
            self.entities_by_sketch.setdefault(e["sketch"], []).append(e)
        self.feats = sorted(self.m.get("features", []), key=lambda f: f.get("ordinal", 0))
        self.feature_by_id = {f["id"]: f for f in self.feats}
        self.curve_ids: dict[str, dict[str, str]] = {}
        self.sketch_names: dict[str, str] = {}
        self.feature_names: dict[str, str] = {}
        self.extrude_params: dict[str, tuple[dict, bool]] = {}
        self.frozen: list[str] = []
        self.cmd_owner: list = []  # timeline ordinal of the feature each command came from
        self.status: dict = {}  # feature id -> converted | gap | imported | left out | not reached | suppressed
        self.feature_notes: dict = {}  # feature id -> notes raised while converting it
        self.check_results: list = []
        self.planes: list[tuple[dict, str]] = []  # (plane, construction plane name)
        self.used_names: set[str] = set()
        self.bodies: set[str] = set()
        self.ns: list[Namespace] = []
        self.ns_of_feature: dict[str, Namespace] = {}
        self.new_slots: dict[str, list[int]] = {}
        self.absorbed: dict[str, str] = {}
        self.skipped: list[str] = []  # features left out so the rest of the timeline converts
        self.hidden: list[str] = []  # bodies hidden at the end (outside Fusion's current model)
        self.plane_of_feature: dict[str, dict] = {}
        self.threaded: list = []  # points on the faces threads were put on
        self.thread_axes: dict = {}  # (component index, body slot) -> [(point on the axis, axis direction)] of its modeled threads
        self.split_pieces: list = []  # (positive-side name, negative-side name, plane)
        self.revolve_geom: dict[str, tuple] = {}
        self.extrude_loops: dict[str, tuple] = {}  # feature id -> (profile picks, picked loops)  # feature id -> (sketch, axis origin, axis dir, regions)  # feature id -> the feature whose import covers it
        self._plan_bodies()

    # ---------- body ledger (pass 1) ----------
    def _plan_bodies(self):
        last = None
        for f in self.feats:
            sp = f.get("source_properties") or {}
            C = parse_centres(sp.get("body_centers"))
            if not C:
                continue
            A = {int(x) for x in sp.get("affected_bodies", "").split(",") if x.strip()}
            D = {int(x) for x in sp.get("deleted_bodies", "").split(",") if x.strip()}
            best, best_score = None, -1
            for ns in self.ns:
                # A body the feature changes can be left without a centre (the decoder gives
                # one only where it can bound the body): it is still there unless deleted.
                kept = set(ns.state) - set(C)
                if kept and not kept <= (A - D):
                    continue
                if not (set(C) - set(ns.state)) <= A:
                    continue
                score = sum(1 for s, c in ns.state.items() if s not in A and close(c, C[s]))
                if score > best_score or (score == best_score and ns is last):
                    best, best_score = ns, score
            if best is None:
                best = Namespace(len(self.ns))
                self.ns.append(best)
            for s_ in set(best.state) - set(C):
                if s_ in A and s_ not in D:
                    C[s_] = best.state[s_]  # changed, centre not given: its last known one
            new = sorted(set(C) - set(best.state))
            pre = [s_ for s_ in new if s_ not in A]
            if pre:
                # Bodies that exist before this feature but no feature made: a Derive or base
                # feature cadmpeg does not decode. They come in from the f3d's final B-rep.
                f["_preexisting"] = pre
                new = [s_ for s_ in new if s_ in A]
            scope = self.raw.scope_of(f) if self.raw else None
            if new:
                best.events.append((scope["record_index"] if scope else 10**12, new))
            self.new_slots[f["id"]] = new
            self.ns_of_feature[f["id"]] = best
            f["_C"], f["_A"] = C, sorted(A)
            f["_prevC"] = dict(best.state)
            f["_exact"] = {int(x) for x in (sp.get("body_centers_exact") or "").split(",") if x.strip()}
            best.state = dict(C)
            last = best
        self.final_state = {ns.index: dict(ns.state) for ns in self.ns}
        self._plan_removed(last_state=self.final_state)
        for ns in self.ns:
            ns.events.sort()
            ns.state = {}
        self._plan_design_ids()

    def _plan_removed(self, last_state):
        """Slots the final model no longer has: no final body at their last centre, or one body
        too few where several slots share a centre (identical bodies). Remove Body features take
        their bodies from these, as their design ids are not always decodable."""
        self.removed: dict[int, list[int]] = {}
        try:
            boxes = final_body_boxes(self.m)
        except Exception:
            return
        centres = [[(lo + hi) / 2 for lo, hi in zip(*bx)] for bx in boxes if bx]
        for k, state in last_state.items():
            used: dict[int, int] = {}
            gone = []
            for slot, c in sorted(state.items()):
                hits = [i for i, x in enumerate(centres) if close(x, c, 1e-3)]
                free = [i for i in hits if used.get(i, 0) == 0]
                if free:
                    used[free[0]] = 1
                else:
                    gone.append(slot)
            self.removed[k] = gone

    def _plan_design_ids(self):
        """Fusion refers to bodies in Move, Combine and Mirror by design ids. Map each id to a
        slot: an operand that lists its body slots says so directly; otherwise ids are allocated
        in creation order, so the k-th new id in a component goes to the next body created
        before the id is first used."""
        self.did_slot: dict[tuple[int, int], list[int]] = {}
        if not self.raw:
            return
        n = self.raw.n
        operands = {o["record_index"]: o for o in n.get("design_body_recipe_operands", [])}
        self.operands = operands
        groups = n.get("design_construction_operand_groups", [])
        scope_ns = {}
        for f in self.feats:
            sc = self.raw.scope_of(f)
            if sc and f["id"] in self.ns_of_feature:
                scope_ns[sc["record_index"]] = self.ns_of_feature[f["id"]]
        op_ns = {}
        for g in groups:
            ns = scope_ns.get(g.get("scope_record_index"))
            for mm in g.get("members", []):
                if ns:
                    op_ns[mm] = ns
        for f in self.feats:
            ns = self.ns_of_feature.get(f["id"])
            for r in re.findall(r"design-record#(\d+)", json.dumps(f.get("definition", {}))):
                if ns:
                    op_ns[int(r)] = ns
        first_seen: dict[tuple[int, int], int] = {}
        anchor: dict[tuple[int, int], int] = {}
        for ri, o in operands.items():
            ns = op_ns.get(ri)
            if ns is None:
                continue
            for ref in o.get("references", []):
                if ref.get("form") not in BODY_FORMS:
                    continue
                key = (ns.index, ref["design_reference"])
                first_seen[key] = min(first_seen.get(key, 10**12), ri)
                slots = ref.get("preceding_body_slots") or []
                if len(slots) == 1 and len(o.get("references", [])) == 1:
                    anchor[key] = slots[0]
        for ns in self.ns:
            ids = sorted(d for (k, d) in first_seen if k == ns.index)
            ptr = -1
            for d in ids:
                key = (ns.index, d)
                if key in anchor:
                    self.did_slot[key] = [anchor[key]]
                    for i, (_, sl) in enumerate(ns.events):
                        if anchor[key] in sl:
                            ptr = max(ptr, i)
                    continue
                if ptr + 1 < len(ns.events) and ns.events[ptr + 1][0] < first_seen[key]:
                    ptr += 1
                if ptr >= 0:
                    self.did_slot[key] = list(ns.events[ptr][1])

    def operand_bodies(self, ns: Namespace, operand_ri: int, counter: dict) -> list[int]:
        o = self.operands.get(operand_ri)
        if o is None:
            raise Gap(f"body operand record {operand_ri} not decoded")
        out = []
        refs = [r for r in o.get("references", []) if r.get("form") in BODY_FORMS]
        if not refs:
            # Only faces named: their bodies are the slots the operand lists.
            return sorted({s for r in o.get("references", []) for s in (r.get("preceding_body_slots") or [])}) or fail(
                f"body operand record {operand_ri} names no body")
        for ref in refs:
            key = (ns.index, ref["design_reference"])
            slots = self.did_slot.get(key)
            if not slots:
                raise Gap(f"no body for design id {ref['design_reference']}")
            k = counter.get(key, 0)
            counter[key] = k + 1
            out.append(slots[min(k, len(slots) - 1)] if len(slots) > 1 else slots[0])
        return sorted(set(out))

    def resolve_bodies(self, ns: Namespace, sel, counter=None) -> list[int]:
        counter = {} if counter is None else counter
        kind = sel.get("kind")
        v = sel.get("value")
        if kind == "historical":
            return sorted({int(b.rsplit(":", 1)[1]) for b in v.get("bodies", [])})
        if kind == "native":
            ri = int(re.search(r"#(\d+)$", v).group(1))
            if ri in self.operands:
                return self.operand_bodies(ns, ri, counter)
            # An operand group (construction-operand-group#<byte offset>): its members.
            for g in self.raw.n.get("design_construction_operand_groups", []):
                if g["byte_offset"] == ri or g["record_index"] == ri:
                    out = []
                    for mm in g.get("members", []):
                        out += self.operand_bodies(ns, mm, counter)
                    return sorted(set(out))
            raise Gap(f"body selection {short(v)} not decoded")
        if kind == "historical_set":
            return sorted({int(m["body"].rsplit(":", 1)[1]) for m in v.get("members", [])})
        if kind == "native_set":
            out = []
            for x in v:
                out += self.resolve_bodies(ns, {"kind": "native", "value": x}, counter)
            return out
        raise Gap(f"body selection kind {kind!r}")

    # ---------- helpers ----------
    def emit(self, command: str, params: dict):
        self.cmds.append({"command": command, "params": params})
        self.cmd_owner.append(getattr(self, "_cur", None))

    def unique(self, name: str) -> str:
        base, n = name, 2
        while name in self.used_names:
            name = f"{base} ({n})"
            n += 1
        self.used_names.add(name)
        return name

    def next_free(self, base: str) -> str:
        """SolidCraft's name for a copy of `base`: the first free `base (k)`, k >= 2."""
        k = 2
        while f"{base} ({k})" in self.bodies:
            k += 1
        return f"{base} ({k})"

    def default_names(self, count: int) -> list[str]:
        out, k = [], 1
        while len(out) < count:
            if f"Body{k}" not in self.bodies:
                out.append(f"Body{k}")
            k += 1
        return out

    def names(self, ns, slots):
        out = []
        for s in slots:
            nm = ns.names.get(s)
            if nm is None or nm not in self.bodies:
                raise Gap(f"body slot {s} of component {ns.index + 1} has no SolidCraft body")
            out.append(nm)
        return out

    def adopt(self, ns, slot, sc_name):
        """Body `sc_name` (as SolidCraft named it) is Fusion's `slot`: rename it to the slot's name."""
        want = ns.name(slot)
        if sc_name != want:
            self.emit("body.rename", {"body": sc_name, "name": want})
        self.bodies.discard(sc_name)
        self.bodies.add(want)
        ns.names[slot] = want

    # ---------- sketches ----------
    def emit_sketch(self, sketch_ref: str, feature_name: str):
        s = self.sketch_by_id.get(sketch_ref)
        if s is None:
            raise Gap(f"sketch {short(sketch_ref)} is not in the IR")
        pl = s.get("placement", {})
        if pl.get("kind") != "resolved":
            raise Gap(f"sketch placement is {pl.get('kind')!r}, not resolved")
        fr = pl["frame"]
        o = [fr["origin"][k] for k in "xyz"]
        n = [fr["normal"][k] for k in "xyz"]
        u = [fr["u_axis"][k] for k in "xyz"]
        v = [n[1] * u[2] - n[2] * u[1], n[2] * u[0] - n[0] * u[2], n[0] * u[1] - n[1] * u[0]]
        # A Fusion sketch's coordinates are measured from the world origin projected onto its
        # plane. cadmpeg's frame origin is that point, except for some sketches on work planes,
        # where it is the work plane's own origin (benchmarks: Side Panels' Sketch 4).
        dn = sum(a * b for a, b in zip(o, n))
        proj = [dn * x for x in n]
        if max(abs(a - b) for a, b in zip(o, proj)) > 1e-9:
            self.notes.append(f"{feature_name}: sketch origin moved from cadmpeg's {fmt(o)} to the projected world origin {fmt(proj)}")
            o = proj
        name = self.unique(feature_name)
        self.sketch_names[sketch_ref] = name
        sketch_start = len(self.cmds)
        self.emit("sketch.create", {"plane": {"origin": rnd(o), "x_dir": rnd(u), "y_dir": rnd(v)}, "name": name})
        canon: list[tuple[float, float]] = []

        def snap(p):
            for q in canon:
                if abs(q[0] - p[0]) < EPS and abs(q[1] - p[1]) < EPS:
                    return [q[0], q[1]]
            canon.append((p[0], p[1]))
            return [p[0], p[1]]

        counters = {"l": 0, "a": 0, "c": 0, "s": 0}
        ids: dict[str, str] = {}
        ents = self.entities_by_sketch.get(sketch_ref, [])
        for e in sorted(ents, key=lambda e: e["geometry"]["kind"] == "point"):
            g = e["geometry"]
            k = g["kind"]
            extra = {"construction": True} if e.get("construction") else {}
            if k == "line":
                a, b = snap(uv(g["start"])), snap(uv(g["end"]))
                if dist(a, b) < EPS:
                    continue
                self.emit("sketch.line", {"points": [a, b], **extra})
                counters["l"] += 1
                ids[e["id"]] = f"l{counters['l']}"
            elif k == "arc":
                c, r = uv(g["center"]), g["radius"]
                t0, t1 = g["start_angle"], g["end_angle"]
                while t1 <= t0:
                    t1 += 2 * math.pi
                a = snap([c[0] + r * math.cos(t0), c[1] + r * math.sin(t0)])
                b = snap([c[0] + r * math.cos(t1), c[1] + r * math.sin(t1)])
                tm = 0.5 * (t0 + t1)
                self.emit("sketch.arc.three_point", {"start": a, "end": b, "through": [c[0] + r * math.cos(tm), c[1] + r * math.sin(tm)], **extra})
                counters["a"] += 1
                ids[e["id"]] = f"a{counters['a']}"
            elif k == "circle":
                self.emit("sketch.circle.center", {"center": uv(g["center"]), "radius": g["radius"], **extra})
                counters["c"] += 1
                ids[e["id"]] = f"c{counters['c']}"
            elif k == "nurbs":
                cv = g["curve"]
                deg, knots = cv["degree"], cv["knots"]
                if cv.get("periodic"):
                    raise Gap("periodic NURBS sketch curve")
                poles, w = nurbs_poles(cv)
                if max(w) - min(w) > 1e-12:
                    raise Gap("rational NURBS sketch curve (SolidCraft scripts take non-rational control splines)")
                if self.sc_target >= SC_KNOTS and 1 <= deg <= 9 and len(poles) <= 500:
                    # SolidCraft 0.49 takes the spline as it is: its own knots, any degree to 9
                    poles[0], poles[-1] = snap(poles[0]), snap(poles[-1])
                    self.emit("sketch.spline.control_knots", {"points": poles, "degree": deg, "knots": knots, **extra})
                    counters["s"] += 1
                    ids[e["id"]] = f"s{counters['s']}"
                    continue
                if deg not in (3, 5):
                    raise Gap(f"degree {deg} NURBS sketch curve (SolidCraft scripts take degree 3 or 5)")
                gaps = [math.dist(a_, b_) for a_, b_ in zip(poles, poles[1:])]
                if self.spline_tol is not None and len(poles) > 4 * (deg + 1) and min(gaps) < DENSE_POLE_GAP:
                    # Densely packed control points (Fusion's fitted involutes) make SolidCraft's
                    # kernel refuse the extruded surface (README, "SolidCraft issues"): refit.
                    fit = refit_spline(poles, knots, deg, self.spline_tol)
                    if fit is None:
                        raise Gap(f"dense spline could not be refitted within {self.spline_tol} mm")
                    self.notes.append(f"{feature_name}: spline of {len(poles)} control points refitted with "
                                      f"{len(fit[0])}, max deviation {fit[1] * 1000:.4f} um")
                    poles, knots = fit[0], clamped_knots(len(fit[0]), deg)
                if not clamped_uniform(knots, deg):
                    # SolidCraft scripts take clamped uniform splines: refit within tolerance.
                    fit = refit_spline(poles, knots, deg, self.spline_tol) if self.spline_tol is not None else None
                    if fit is None:
                        raise Gap("NURBS sketch curve with non-uniform knots (SolidCraft scripts take clamped uniform)")
                    self.notes.append(f"{feature_name}: non-uniform spline of {len(poles)} control points refitted as uniform "
                                      f"with {len(fit[0])}, max deviation {fit[1] * 1000:.4f} um")
                    poles, knots = fit[0], clamped_knots(len(fit[0]), deg)
                poles[0], poles[-1] = snap(poles[0]), snap(poles[-1])
                self.emit("sketch.spline.control_point" if deg == 3 else "sketch.spline.control_point_5", {"points": poles, **extra})
                counters["s"] += 1
                ids[e["id"]] = f"s{counters['s']}"
            elif k == "point":
                p = uv(g["position"])
                if any(abs(q[0] - p[0]) < EPS and abs(q[1] - p[1]) < EPS for q in canon) or (abs(p[0]) < EPS and abs(p[1]) < EPS):
                    continue
                canon.append((p[0], p[1]))
                self.emit("sketch.point", {"point": p})
            else:
                raise Gap(f"sketch entity kind {k!r}")
        self.curve_ids[sketch_ref] = ids
        if self.sketch_probe is not None:
            kept, note = constraints.carry(self, sketch_ref, self.cmds[sketch_start:], ids)
            for c in kept:
                self.emit(c["command"], c["params"])
            if note:
                self.notes.append(f"{feature_name}: {note}")
        self.emit("sketch.finish", {})

    def loop_segments(self, sketch_ref: str, loop) -> list[tuple]:
        ents = {e["id"]: e for e in self.entities_by_sketch.get(sketch_ref, [])}
        segs = []
        for use in loop:
            e = ents.get(use["entity"])
            if e is None:
                raise Gap(f"profile entity {short(use['entity'])} not in the IR")
            pts = polyline(e["geometry"])
            segs += list(zip(pts, pts[1:]))
        return segs

    def profile_picks(self, sketch_ref: str, indices: list[int]) -> list[dict]:
        """Each Fusion profile as a point inside it: Fusion and SolidCraft split overlapping
        curves differently, so the curves bounding a region need not match, but the region does."""
        s = self.sketch_by_id[sketch_ref]
        if sketch_ref not in self.curve_ids:
            raise Gap(f"profile of sketch {short(sketch_ref)}, which was not emitted")
        loops = s.get("profiles", [])
        all_loops = [self.loop_segments(sketch_ref, lp) for lp in loops]
        picks = []
        for i in indices:
            if i >= len(loops):
                raise Gap(f"profile index {i} out of range ({len(loops)} profiles)")
            others = [x for j, x in enumerate(all_loops) if j != i]
            pts = interior_points(all_loops[i], others)
            nested = [j for j, x in enumerate(all_loops) if j != i and x and all(
                len([c_ for c_ in crossings(all_loops[i], q[1] + 1.3e-7) if c_ < q[0] + 1e-7]) % 2 == 1
                for q in [pt for sg in x for pt in sg][:: max(1, len(x) // 8)])]
            if nested:
                self.notes.append(f"profile {i} of {self.sketch_names.get(sketch_ref)} includes the loop(s) nested in it {nested}")
            if not pts:
                raise Gap(f"no interior point found for profile {i}")
            picks.append([{"point": rnd(pt)} for pt in pts])
        self.last_pick_loops = [all_loops[i] for i in indices]
        # Selected loops can nest (a ring and the disc inside it): each region is picked once.
        out, seen = [], set()
        for q in (q for grp in picks for q in grp):
            pt = q["point"]
            sig = tuple(len([c_ for c_ in crossings(lp, pt[1]) if c_ < pt[0]]) % 2 for lp in all_loops if lp)
            if sig not in seen:
                seen.add(sig)
                out.append(q)
        return out

    @staticmethod
    def region_rings(region_selection: dict) -> list[list[dict]]:
        """The boundary loops of each face a stored region selection names. cadmpeg gives the
        selected regions as loops with a region ordinal, and (newer upstream) the atomic regions
        that make up a selected region: the faces of the sketch's curve arrangement it consists of.
        A selected region with atomic regions is its atomic regions; one without is one face."""
        by_ord: dict = {}
        for lp in region_selection.get("regions", []):
            by_ord.setdefault(lp.get("region_ordinal", 0), []).append(lp)
        atomic: dict = {}
        for a in region_selection.get("atomic_regions", []):
            atomic.setdefault(a.get("region_ordinal", 0), []).append(a.get("loops", []))
        rings = []
        for o in sorted(by_ord):
            rings += atomic.get(o) or [by_ord[o]]
        return rings

    def region_picks(self, sketch_ref: str, region_selection: dict) -> list[dict]:
        """Profiles named by Fusion's region selection (curve primary ids): each region is the
        profile loop whose curves it lists (it may also list a curve it shares with a neighbour)."""
        if sketch_ref not in self.curve_ids:
            raise Gap("profile of a sketch that was not emitted")
        suffix = sketch_ref.rsplit("@", 1)[1]
        loops = self.sketch_by_id[sketch_ref].get("profiles", [])
        held_ids = {re.search(r"@\d+c\d+:\d+$", e["id"]).group() for e in self.entities_by_sketch.get(sketch_ref, [])
                    if re.search(r"@\d+c\d+:\d+$", e["id"])}
        rings = []
        for ring in self.region_rings(region_selection):
            ids = [m["curve_primary_id"] for lp in ring for m in lp.get("members", [])]
            if ids and not any(f"@{suffix}c{i}:0" in held_ids for i in ids):
                # Every curve the region names has left the sketch since Fusion stored the selection.
                if not self.infer.enabled("stale_region_left_out"):
                    raise Gap("a stored region none of whose curves the sketch still holds "
                              "(inference stale_region_left_out is off)")
                self.infer.use("stale_region_left_out", self.cur["name"] if self.cur else "?", f"curves {ids}")
                self.notes.append(f"{self.cur['name'] if self.cur else '?'}: a stored profile region names only curves "
                                  f"the sketch no longer holds ({ids}); left out")
                continue
            rings.append(ring)
        if not rings:
            raise Gap("a region selection none of whose curves the sketch still holds")
        indices = []
        for ring in rings:
            best, best_n = None, 0
            if len(ring) == 1:
                want = {f"@{suffix}c{m['curve_primary_id']}:0" for m in ring[0].get("members", [])}
                for i, lp in enumerate(loops):
                    have = {re.search(r"@\d+c\d+:\d+$", u["entity"]).group() for u in lp}
                    if have <= want and len(have) > best_n:
                        best, best_n = i, len(have)
            if best is None:
                return self.region_picks_by_arrangement(sketch_ref, rings)
            indices.append(best)
        return self.profile_picks(sketch_ref, indices)

    def arrangement_face(self, sketch_ref, faces, ids):
        """The faces of the sketch's curve arrangement (indices into `faces`) that fit a stored
        boundary: the face's outline runs along each named curve the sketch holds, or holds the
        curve inside it (a hole loop), and runs along at least one. None when the sketch holds
        none of the curves."""
        suffix = sketch_ref.rsplit("@", 1)[1]
        ents = {re.search(r"@\d+c\d+:\d+$", e["id"]).group(): e for e in self.entities_by_sketch.get(sketch_ref, [])
                if re.search(r"@\d+c\d+:\d+$", e["id"]) and not e.get("construction")}
        held = [ents[k] for k in dict.fromkeys(f"@{suffix}c{i}:0" for i in ids) if k in ents]
        if not held:
            return None
        lines = [polyline(e["geometry"], n_arc=96) for e in held]
        on = lambda q, pl: any(seg_distance(q, a, b) < 1e-6 for a, b in zip(pl, pl[1:]))
        runs_along = lambda c, pl: any(on(a, pl) and on(b, pl) and math.dist(a, b) > 1e-6
                                       for a, b in planar.segments(c))

        def inside(c, pl):
            segs = planar.segments(c)
            return all(len([x for x in crossings(segs, q[1] + 1.3e-7) if x < q[0] + 1e-7]) % 2 == 1
                       and not any(seg_distance(q, a, b) < 1e-6 for a, b in segs) for q in pl[:: max(1, len(pl) // 12)])

        out, exact = [], []
        for i, c in enumerate(faces):
            along = [runs_along(c, pl) for pl in lines]
            if any(along) and all(a or inside(c, pl) for a, pl in zip(along, lines)):
                out.append(i)
                # the face's whole outline lies on the named curves: the stored boundary itself
                if all(any(on(a, pl) and on(b, pl) for pl in lines) for a, b in planar.segments(c)):
                    exact.append(i)
        return exact or out

    def region_picks_by_arrangement(self, sketch_ref, rings):
        """Regions whose member curves bound no whole profile loop (a region bounded by part of a
        longer curve, a region with a hole, or members the sketch does not hold): the one closed
        face of the sketch's curve arrangement that runs along every member curve the sketch holds."""
        if not self.infer.enabled("region_by_arrangement"):
            raise Gap("a region that is no profile loop of its sketch")
        faces = self.sketch_regions(sketch_ref)
        picks, chosen = [], []
        for ring in rings:
            ids = [m["curve_primary_id"] for lp in ring for m in lp.get("members", [])]
            cands = self.arrangement_face(sketch_ref, faces, ids)
            if cands is None:
                raise Gap("a region that is no profile loop of its sketch, with no member curve in it")
            if len(cands) != 1:
                raise Gap(f"a region that is no profile loop of its sketch ({len(cands)} arrangement faces fit)")
            c = faces[cands[0]]
            if any(c is x for x in chosen):
                continue
            pt = interior_point(planar.segments(c), [planar.segments(x) for x in faces if x is not c])
            if pt is None:
                raise Gap("no interior point found for a sketch region")
            picks.append({"point": rnd(pt)})
            chosen.append(c)
            self.infer.use("region_by_arrangement", self.cur["name"] if self.cur else "?",
                           f"members {ids}, point {rnd(pt)}")
        self.last_pick_loops = [planar.segments(c) for c in chosen]
        return picks

    def trimmed_segments(self, sketch_ref, uses):
        ents = {e["id"]: e for e in self.entities_by_sketch.get(sketch_ref, [])}
        segs = []
        for u in uses:
            e = ents.get(u["entity"])
            if e is None:
                raise Gap(f"region curve {short(u['entity'])} not in the IR")
            t0, t1 = u.get("parameter_range") or (0.0, 1.0)
            pts = polyline_range(e["geometry"], t0, t1)
            segs += list(zip(pts, pts[1:]))
        return segs

    def sketch_region_picks(self, sketch_ref, regions):
        """Regions given as trimmed boundary pieces (each curve with the parameter range it
        bounds the region over): a point inside each."""
        if sketch_ref not in self.curve_ids:
            raise Gap("profile of a sketch that was not emitted")
        picks, loops = [], []
        for r in regions:
            outer = self.trimmed_segments(sketch_ref, r.get("outer_boundary", []))
            holes = [self.trimmed_segments(sketch_ref, ib) for ib in r.get("inner_boundaries", []) + r.get("hole_boundaries", [])]
            pt = interior_point(outer, holes)
            if pt is None:
                raise Gap("no interior point found for a region")
            picks.append({"point": rnd(pt)})
            loops.append(outer)
        self.last_pick_loops = loops
        return picks

    def selection_picks(self, sketch_ref, selections):
        """An extrude selection group: the sketch curves it names (by local id) bound the profiles."""
        suffix = sketch_ref.rsplit("@", 1)[1]
        groups = {g["byte_offset"]: g for g in self.raw.n.get("design_extrude_selection_groups", [])}
        members = {m["record_index"]: m for m in self.raw.n.get("design_extrude_selection_members", [])}
        want = set()
        if len(selections) == 1 and "design-parameter-scope#" in selections[0]:
            # cadmpeg keeps a compact selection whose curve members do not bind native (0.6.12,
            # #1262) and names the extrude's own scope, whose decoded profile has the regions.
            off = int(re.search(r"#(\d+)$", selections[0]).group(1))
            scope = next((s for s in self.raw.n.get("design_parameter_scopes", []) if s["byte_offset"] == off), None)
            prof = (scope or {}).get("extrude_profile") or {}
            if prof.get("region_selection") and sketch_ref.endswith("@" + str(prof.get("entity_suffix"))):
                return self.region_picks(sketch_ref, prof["region_selection"])
            raise Gap(f"extrude selection {short(selections[0])} not decoded")
        for sel in selections:
            g = groups.get(int(re.search(r"#(\d+)$", sel).group(1)))
            if g is None:
                raise Gap(f"extrude selection {short(sel)} not decoded")
            for mr in g.get("members", []):
                mm = members.get(mr)
                if mm is None:
                    raise Gap(f"extrude selection member {mr} not decoded")
                want.add(f"@{suffix}c{mm['local_id']}:0")
        loops = self.sketch_by_id[sketch_ref].get("profiles", [])
        idx = [i for i, lp in enumerate(loops)
               if {re.search(r"@\d+c\d+:\d+$", u["entity"]).group() for u in lp} <= want]
        if not idx:
            # Not curve ids: profile ids. Fusion numbers a sketch's profiles in order from the
            # lowest id any extrude selects on it (seen in the benchmarks; --check verifies it).
            ids = sorted(int(w.rsplit("c", 1)[1].split(":")[0]) for w in want)
            pv = lambda f_: (f_.get("definition", {}).get("profile") or {}).get("value")
            sketch_groups = [g for g in groups.values() if any(
                isinstance(pv(f_), dict) and pv(f_).get("sketch") == sketch_ref
                and any(sel_.endswith(f"#{g['byte_offset']}") for sel_ in pv(f_).get("selections", []))
                for f_ in self.feats)]
            all_ids = sorted({members[m]["local_id"] for g in sketch_groups for m in g.get("members", []) if m in members})
            base = all_ids[0] if all_ids else None
            idx = [i - base for i in ids] if base is not None else []
            if idx and max(idx) >= len(loops) and len(all_ids) == len(loops) and self.infer.enabled("profile_ids_by_rank"):
                # ids with gaps: the k-th smallest id the sketch's extrudes select is profile k
                idx = [all_ids.index(i) for i in ids]
                self.infer.use("profile_ids_by_rank", self.cur["name"] if self.cur else "?", f"ids {ids} read as profiles {idx}")
            if not idx or max(idx) >= len(loops):
                raise Gap("an extrude selection that bounds no profile of its sketch")
            self.notes.append(f"extrude selection ids {ids} on {self.sketch_names.get(sketch_ref)} read as profiles {idx}")
        return self.profile_picks(sketch_ref, idx)

    # ---------- features ----------
    def feat_sketch(self, f, d):
        sk = d["sketch"]
        if sk.get("space") != "planar":
            raise Gap(f"{sk.get('space')} sketch")
        self.emit_sketch(sk["sketch"], f["name"])

    def feat_datum_plane(self, f, d):
        fr = d["frame"]
        o = [fr["origin"][k] for k in "xyz"]
        nrm = [fr["normal"][k] for k in "xyz"]
        self.plane_of_feature[f["id"]] = {"origin": o, "normal": nrm}
        for base, axis in (("YZ", 0), ("XZ", 1), ("XY", 2)):
            if abs(abs(nrm[axis]) - 1) < 1e-9:
                name = self.unique(f["name"])
                self.emit("construct.plane.offset", {"base": base, "offset": o[axis], "name": name})
                self.planes.append(({"origin": o, "normal": nrm}, name))
                return
        if self.used_later(f):
            self.notes.append(f"{f['name']}: construction plane at an angle, kept for the features using it (not emitted)")
            return
        self.notes.append(f"{f['name']}: construction plane at an angle that no feature uses: left out (it makes no geometry)")

    def feat_base(self, f, d):
        """A Base Feature: geometry Fusion holds without history. Its bodies come from the f3d's
        final B-rep, valid when no later feature changes them."""
        sel = d.get("bodies") or {}
        ids = (sel.get("value") or {}).get("bodies", []) if sel.get("kind") == "resolved" else []
        idx = {b["id"]: i for i, b in enumerate(self.m.get("bodies", []))}
        if not ids or any(i not in idx for i in ids):
            raise Gap("base feature whose bodies are not final bodies")
        if not self.derive_step:
            raise Gap("base feature bodies need the .f3d itself to write their STEP")
        ns = self.ns_of_feature.get(f["id"])
        if ns is None:
            ns = self.ns[0] if self.ns else Namespace(0)
            if not self.ns:
                self.ns.append(ns)
            used = set(ns.names)
            slots = []
            k = 1
            while len(slots) < len(ids):
                if k not in used:
                    slots.append(k)
                k += 1
        else:
            slots = sorted(set(self.new_slots.get(f["id"], [])) | set(f.get("_A", [])))
            if len(slots) != len(ids):
                raise Gap(f"base feature: {len(ids)} bodies for {len(slots)} ledger slots")
            for g in self.feats:
                if g.get("ordinal", 0) > f.get("ordinal", 0) and self.ns_of_feature.get(g["id"]) is ns and set(g.get("_A", [])) & set(slots):
                    raise Gap("base feature bodies are changed by later features (their base state is not in the final B-rep)")
        for s_, bid in zip(slots, ids):
            path = self.derive_step(idx[bid], f"{f['name']} {ns.name(s_)}")
            self.emit("file.insert_step", {"path": path, "name": self.unique(f["name"]), "names": "solid"})
            self.bodies.add("cadmpeg_model")
            self.adopt(ns, s_, "cadmpeg_model")
        self.notes.append(f"{f['name']}: base feature (geometry without history in Fusion too), imported from the final B-rep")

    def used_later(self, f):
        return any(f["id"] in g.get("dependencies", []) or f["id"] in json.dumps(g.get("definition", {}))
                   for g in self.feats if g.get("ordinal", 0) > f.get("ordinal", 0))

    def live_body(self, f):
        """The body a feature without ledger data works on: the design's only live body."""
        ns = self.ns_of_feature.get(f["id"])
        if ns is not None and len(f.get("_A", [])) == 1 and f["_A"][0] in ns.names:
            return ns.names[f["_A"][0]]
        live = sorted(b for b in self.bodies if b != "cadmpeg_model")
        if len(live) != 1:
            raise Gap(f"{f.get('source_tag')} with no body data, in a design with {len(live)} live bodies")
        return live[0]

    def feat_split(self, f, d):
        tool = d.get("tool", {})
        if tool.get("kind") != "plane":
            raise Gap(f"split by {tool.get('kind')}")
        ref = (tool.get("value") or {}).get("plane")
        plane = self.plane_of_feature.get(ref) if isinstance(ref, str) else None
        if plane is None and isinstance(tool.get("value"), dict) and "normal" in tool["value"]:
            v = tool["value"]
            plane = {"origin": [v["origin"][k] for k in "xyz"], "normal": [v["normal"][k] for k in "xyz"]}
        if plane is None:
            raise Gap("split plane not decoded")
        body = self.live_body(f)
        self.emit("solid.split_body", {"body": body, "plane": {"origin": rnd(plane["origin"]), "normal": rnd(plane["normal"])},
                                       "name": self.unique(f["name"])})
        # SolidCraft keeps the name on the piece the normal points to; the other is "<name> (1)".
        self.bodies.add(body + " (1)")
        self.split_pieces.append((body, body + " (1)", plane))
        self.notes.append(f"{f['name']}: split by its plane; the pieces are {body!r} (normal side) and {body + ' (1)'!r}")

    def occurrence_copy(self, f):
        """A Copy/Paste in an assembly that copies a component: it adds an occurrence (which the
        model lists with its placement) and changes no body. True when it has no body data and
        its component has at least one occurrence more than it has Copy/Paste features."""
        if (f.get("source_properties") or {}).get("body_centers"):
            return False
        pd = f.get("product_definition")
        occ = sum(1 for o in self.m.get("occurrences", []) if (o.get("prototype") or {}).get("definition") == pd)
        copies = sum(1 for g in self.feats if g.get("source_tag") == "CopyPaste" and g.get("product_definition") == pd)
        return occ >= copies + 1

    def feat_no_geometry(self, f, d):
        """Features that place parts or mark frames but make no geometry (joints, joint origins,
        work points): left out, as the occurrences carry where each part sits."""
        self.notes.append(f"{f['name']}: {f.get('source_tag')} makes no geometry, left out")

    def feat_datum_axis(self, f, d):
        """A construction axis through two points; features that use it read their own copy of
        the axis, so this only keeps it in the timeline."""
        o = [d["origin"][k] for k in "xyz"]
        v = [d["direction"][k] for k in "xyz"]
        self.notes.append(f"{f['name']}: construction axis left out (SolidCraft's construct.axis needs points "
                          "or edges; patterns use the axis directly)")

    def feat_delete_body(self, f, d):
        ns = self.ns_of_feature.get(f["id"]) or (self.ns[0] if len(self.ns) == 1 else None)
        if ns is None and self.split_pieces:
            return self.remove_split_piece(f)
        if ns is None:
            raise Gap("Remove Body in a design with several components")
        need = 0
        try:
            want = self.resolve_bodies(ns, d["bodies"])
        except Gap:
            want = []
        g = d["bodies"].get("value")
        for grp in self.raw.n.get("design_construction_operand_groups", []):
            if isinstance(g, str) and g.endswith(f"#{grp['byte_offset']}"):
                need = sum(len(self.operands[m]["references"]) for m in grp["members"] if m in self.operands)
        gone = [s_ for s_ in self.removed.get(ns.index, []) if s_ in ns.names]
        slots = [s_ for s_ in want if s_ in gone]
        for s_ in reversed(gone):
            if len(slots) >= max(need, 1):
                break
            if s_ not in slots:
                slots.append(s_)
        if not slots and self.split_pieces:
            return self.remove_split_piece(f)
        if not slots:
            raise Gap("Remove Body: no body of the final model is missing")
        if sorted(slots) != sorted(want):
            self.notes.append(f"{f['name']}: removes {[ns.name(x) for x in slots]}, the bodies the final model no longer "
                              "has (its design ids do not resolve uniquely)")
        names = self.names(ns, slots)
        self.emit("solid.remove", {"bodies": names, "name": self.unique(f["name"])})
        for s_ in slots:
            self.bodies.discard(ns.names.pop(s_, ""))

    def remove_split_piece(self, f):
        """Remove Body after a Split: the piece on the side of the plane the final model has no
        body on."""
        pos, neg, plane = self.split_pieces[-1]
        forced = self.infer.override(f["name"], "remove_piece")
        if forced in ("positive", "negative"):
            gone = pos if forced == "positive" else neg
            self.emit("solid.remove", {"bodies": [gone], "name": self.unique(f["name"])})
            self.bodies.discard(gone)
            self.split_pieces.pop()
            self.notes.append(f"{f['name']}: removes split piece {gone!r} (override)")
            return
        if not self.infer.enabled("split_piece_by_final_side"):
            raise Gap("Remove Body after a Split: which piece is not decoded (inference split_piece_by_final_side is off)")
        boxes = getattr(self, "_final_boxes", None) or final_body_boxes(self.m)
        self._final_boxes = boxes
        side = lambda c: sum((a - b) * n for a, b, n in zip(c, plane["origin"], plane["normal"]))
        sides = [side([(lo + hi) / 2 for lo, hi in zip(*bx)]) for bx in boxes if bx]
        if sides and all(x > 1e-6 for x in sides):
            gone = neg
        elif sides and all(x < -1e-6 for x in sides):
            gone = pos
        else:
            raise Gap("Remove Body after a Split: the final bodies lie on both sides of the plane")
        self.emit("solid.remove", {"bodies": [gone], "name": self.unique(f["name"])})
        self.bodies.discard(gone)
        self.split_pieces.pop()
        self.notes.append(f"{f['name']}: removes split piece {gone!r}, the side of the plane the final model has no body on")
        self.infer.use("split_piece_by_final_side", f["name"], f"removed {gone!r}")

    def final_body_for(self, centre):
        """The final B-rep body at a slot's centre: the one whose vertex box has it exactly, else
        the nearest when it is clearly nearer than any other (vertex boxes miss curved bulges)."""
        boxes = getattr(self, "_final_boxes", None)
        if boxes is None:
            boxes = self._final_boxes = final_body_boxes(self.m)
        ds = sorted((math.dist([(lo + hi) / 2 for lo, hi in zip(*bx)], centre), i) for i, bx in enumerate(boxes) if bx)
        if not ds:
            return None
        exact = [i for d_, i in ds if d_ < 1e-3]
        if len(exact) == 1:
            return exact[0]
        if exact:
            return None
        if len(ds) == 1 or ds[1][0] > max(3 * ds[0][0], ds[0][0] + 1.0):
            return ds[0][1]
        return None

    def freeze(self, f, reason):
        """Fallback for a feature that cannot be converted: when no later feature changes the
        bodies it makes or changes, bring those bodies in from the f3d's final B-rep (they end up
        non-editable) and go on with the timeline. Returns whether it could."""
        ns = self.ns_of_feature.get(f["id"])
        slots = None
        if not f.get("_A") and not self.new_slots.get(f["id"]) and f.get("outputs"):
            done = self.freeze_outputs(f, reason)
            if done is not None:
                return done
        if (ns is None or not f.get("_A")) and len(self.ns) == 1:
            # No body data of its own (a pattern whose seeds the decoder does not give): the
            # bodies the next feature with body data finds already there are the ones it can
            # have changed; they come in final, with every later feature changing them.
            nxt = next((g for g in self.feats if g.get("ordinal", 0) > f.get("ordinal", 0)
                        and self.ns_of_feature.get(g["id"]) is self.ns[0] and g.get("_A")), None)
            if nxt is not None:
                ns = self.ns[0]
                slots = sorted(set(nxt["_A"]) - set(self.new_slots.get(nxt["id"], [])))
        if ns is None or not self.derive_step or not (slots or f.get("_A")) or not self.infer.enabled("freeze_from_final_brep"):
            return False
        if slots is None:
            slots = sorted(set(f["_A"]) | set(self.new_slots.get(f["id"], [])))
        # Later features that change these bodies (or bodies made from them) go with them: the
        # bodies come in as they end up, and those features are left to the import.
        S, tail = set(slots), []
        for g in self.feats:
            if g.get("ordinal", 0) <= f.get("ordinal", 0):
                continue
            if self.ns_of_feature.get(g["id"]) is ns and set(g.get("_A", [])) & S:
                tail.append(g)
                S |= set(g.get("_A", [])) | set(self.new_slots.get(g["id"], []))
            elif len(self.ns) == 1 and g["id"] not in self.ns_of_feature and \
                    (g.get("definition", {}).get("definition") in NEEDS_BODY or g.get("definition", {}).get("family") in NEEDS_BODY):
                tail.append(g)  # no ledger data: it works on this design's bodies, which come in final
        final = self.final_state.get(ns.index, {})
        gone = set(self.removed.get(ns.index, []))
        picks = []
        for s_ in sorted(S):
            if tail and s_ in gone and s_ in final and self.final_body_for(final[s_]) is not None:
                gone.discard(s_)  # a final body stands where it ends: not removed after all
            if tail and (s_ in gone or s_ not in final):
                if s_ in slots and s_ not in final:
                    return False
                continue
            i = self.final_body_for(final[s_] if tail else f["_C"][s_])
            if i is None:
                return False
            picks.append((s_, i))
        if not picks:
            return False  # nothing of these bodies is in the final model to import
        slots = sorted(S)
        mark = len(self.cmds)
        try:
            old = [ns.names[s_] for s_ in slots if s_ in ns.names and ns.names[s_] in self.bodies]
            if old:
                # renamed first: SolidCraft keeps a removed body's name taken by the Remove
                gone = []
                for o_ in old:
                    tmp = self.unique(f"{o_} (replaced)")
                    self.emit("body.rename", {"body": o_, "name": tmp})
                    gone.append(tmp)
                self.emit("solid.remove", {"bodies": gone, "name": self.unique(f"{f['name']} (replaced)")})
                self.bodies.difference_update(old)
            for s_, i in picks:
                path = self.derive_step(i, f"{f['name']} {ns.name(s_)}")
                if self.sc_cli and step_volume(self.sc_cli, path, self.script_dir) is None:
                    # SolidCraft would not see a closed solid (fitted thread surfaces): no import
                    raise Gap(f"the final body {ns.name(s_)} does not import as a closed solid")
                self.emit("file.insert_step", {"path": path, "name": self.unique(f"{f['name']} (imported)"), "names": "solid"})
                self.bodies.add("cadmpeg_model")
                self.adopt(ns, s_, "cadmpeg_model")
        except Gap:
            del self.cmds[mark:]
            return False
        if tail:
            self.notes.append(f"{f['name']}: not converted ({reason}); its bodies {[ns.name(x) for x in slots]} are imported "
                              f"as the f3d's final B-rep has them, non-editable, so the later features changing them "
                              f"({', '.join(g['name'] for g in tail)}) are left to the import")
            for g in tail:
                self.absorbed[g["id"]] = f["name"]
        else:
            self.notes.append(f"{f['name']}: not converted ({reason}); its bodies {[ns.name(x) for x in slots]} are "
                              "imported from the f3d's final B-rep (no later feature changes them), non-editable")
        self.frozen.append(f["name"])
        self.infer.use("freeze_from_final_brep", f["name"], f"not converted ({reason}); {len(slots)} body(ies) imported"
                        + (f" with {len(tail)} later feature(s)" if tail else ""))
        return True

    def freeze_outputs(self, f, reason):
        """`freeze` for a feature with no body-ledger data of its own but with the final bodies
        it makes named (cadmpeg's `outputs`): a surface feature such as a Patch, whose sheet
        bodies the ledger does not track. Those bodies come in from the final B-rep, surface
        bodies as surfaces. None when this does not apply (an output that is no final body, or
        one a later feature also names, i.e. changes)."""
        if not self.derive_step or not self.infer.enabled("freeze_from_final_brep"):
            return None
        idx = {b["id"]: i for i, b in enumerate(self.m.get("bodies", []))}
        outs = list(f["outputs"])
        if any(o not in idx for o in outs):
            return None
        later = {o for g in self.feats if g.get("ordinal", 0) > f.get("ordinal", 0) for o in g.get("outputs") or []}
        if later & set(outs):
            return None
        mark, kinds = len(self.cmds), []
        try:
            for k, o in enumerate(outs, 1):
                i = idx[o]
                sheet = self.m["bodies"][i].get("kind") == "sheet"
                label = f"{f['name']} {'surface' if sheet else 'body'} {k}"
                path = self.derive_step(i, label)
                if not sheet and self.sc_cli and step_volume(self.sc_cli, path, self.script_dir) is None:
                    raise Gap(f"the final body {o} does not import as a closed solid")
                self.emit("file.insert_step", {"path": path, "name": self.unique(f"{f['name']} (imported)"), "names": "solid"})
                label = self.unique(label)
                self.emit("body.rename", {"body": "cadmpeg_model", "name": label})
                self.bodies.add(label)
                if self.m["bodies"][i].get("visible") is False:
                    self.emit("browser.visibility", {"items": [label], "visible": False})
                kinds.append("surface" if sheet else "solid")
        except Gap:
            del self.cmds[mark:]
            del self.cmd_owner[mark:]
            return False
        self.notes.append(f"{f['name']}: not converted ({reason}); the {len(outs)} body(ies) it makes "
                          f"({', '.join(kinds)}) are imported from the f3d's final B-rep, non-editable")
        self.frozen.append(f["name"])
        self.infer.use("freeze_from_final_brep", f["name"], f"not converted ({reason}); {len(outs)} output body(ies) imported")
        return True

    def import_preexisting(self, f):
        """Bodies that exist before `f` without a feature making them (Fusion's Derive): import
        them from the f3d's final B-rep. Only valid when no later feature changes them."""
        ns = self.ns_of_feature[f["id"]]
        pre = f["_preexisting"]
        for g in self.feats:
            if g.get("ordinal", 0) > f.get("ordinal", 0) and self.ns_of_feature.get(g["id"]) is ns:
                hit = set(g.get("_A", [])) & set(pre)
                if hit:
                    raise Gap(f"derived body slot(s) {sorted(hit)} are changed later by {g['name']}; "
                              "their state before that is not in the final B-rep")
        if not self.derive_step:
            raise Gap("derived bodies need the .f3d itself (give it, or --source with --ir) to write their STEP")
        for s_ in pre:
            i = self.final_body_for(f["_C"][s_])
            if i is None:
                raise Gap(f"derived body slot {s_}: no final body clearly has its centre")
            path = self.derive_step(i, ns.name(s_))
            self.emit("file.insert_step", {"path": path, "name": self.unique(f"Derived {ns.name(s_)}"), "names": "solid"})
            self.bodies.add("cadmpeg_model")
            self.adopt(ns, s_, "cadmpeg_model")
        self.notes.append(f"{f['name']}: slot(s) {pre} exist before it with no decoded feature (a Derive or base "
                          "feature); imported from the f3d's final B-rep as non-editable bodies")

    def plane_ref(self, plane):
        for p, name in self.planes:
            n1, n2 = p["normal"], plane["normal"]
            par = abs(abs(sum(a * b for a, b in zip(n1, n2))) - 1) < 1e-9
            on = abs(sum((a - b) * c for a, b, c in zip(plane["origin"], p["origin"], n1))) < 1e-6
            if par and on:
                return name
        return {"origin": rnd(plane["origin"]), "normal": rnd(plane["normal"])}

    def extrude_from_raw(self, f, scope) -> dict:
        """An extrude cadmpeg keeps native: its prologue, profile and parameter records."""
        pro = scope.get("extrude_prologue")
        prof = scope.get("extrude_profile")
        if not pro or not prof:
            raise Gap("native extrude without a decoded prologue and profile")
        R = lambda role: self.raw.role(scope, role)
        sref = next((s for s in self.sketch_by_id if s.endswith("@" + prof["entity_id"].split("_", 1)[1])), None)
        if sref is None:
            raise Gap(f"extrude profile sketch {prof['entity_id']} not found")
        d = {"profile": {"kind": "region", "sketch": sref, "regions": prof["region_selection"]},
             "direction": {"kind": "reversed_profile_normal" if pro.get("direction_reversed") else "profile_normal"},
             "op": pro["operation"], "solid": pro.get("solid_operation", True)}
        if R("AlongDistance") is None:
            raise Gap("native extrude without an AlongDistance parameter")
        if pro.get("start") == "offset_profile_plane":
            d["start"] = {"kind": "offset_profile_plane", "offset": R("ProfileOffset") or 0.0}
        else:
            d["start"] = {"kind": "profile_plane"}
        if pro.get("extent") == "two_sided_distance":
            d["extent"] = {"kind": "two_sided", "first": {"termination": {"kind": "blind", "length": R("AlongDistance")}, "taper": R("TaperAngle")},
                           "second": {"termination": {"kind": "blind", "length": R("AgainstDistance")}, "taper": R("Side2TaperAngle")}}
        elif pro.get("extent") == "one_sided_distance":
            d["extent"] = {"kind": "one_sided", "side": {"termination": {"kind": "blind", "length": R("AlongDistance")}, "taper": R("TaperAngle")}}
        else:
            raise Gap(f"native extrude extent {pro.get('extent')!r}")
        return d

    def feat_extrude(self, f, d):
        ns = self.ns_of_feature.get(f["id"])
        scope = self.raw.scope_of(f) if self.raw else None
        thin = [r for r in (self.raw.roles.get(scope["record_index"], {}) if scope and self.raw else {}) if r.endswith("WallThickness")]
        if thin:
            # The decoder keeps a thin extrude's wall inputs native and states the rest as a solid extrude.
            raise Gap(f"thin extrude ({', '.join(sorted(thin))}) is not mapped")
        if d.get("definition") == "native":
            if not scope:
                raise Gap("native extrude without its scope record")
            d = self.extrude_from_raw(f, scope)
        elif scope and self.raw:
            # cadmpeg reads distances; take tapers from the parameter records too.
            ext = d.get("extent", {})
            t1, t2 = self.raw.role(scope, "TaperAngle"), self.raw.role(scope, "Side2TaperAngle")
            if ext.get("kind") == "one_sided" and t1:
                ext["side"]["taper"] = t1
            elif ext.get("kind") == "two_sided":
                if t1:
                    ext["first"]["taper"] = t1
                if t2:
                    ext["second"]["taper"] = t2
        prof = d["profile"]
        p: dict = {}
        sp = f.get("source_properties") or {}
        self.last_pick_loops = None
        if prof["kind"] == "sketch_profiles":
            sref = prof["value"]["sketch"]
            p["sketch"] = self.sketch_names.get(sref) or fail(f"sketch {short(sref)} not emitted")
            p["profiles"] = self.profile_picks(sref, prof["value"]["profiles"])
        elif prof["kind"] == "sketch_regions":
            sref = prof["value"]["sketch"]
            p["sketch"] = self.sketch_names.get(sref) or fail("sketch not emitted")
            p["profiles"] = self.sketch_region_picks(sref, prof["value"]["regions"])
        elif prof["kind"] == "sketch_selection":
            sref = prof["value"]["sketch"]
            p["sketch"] = self.sketch_names.get(sref) or fail("sketch not emitted")
            p["profiles"] = self.selection_picks(sref, prof["value"]["selections"])
        elif prof["kind"] == "region":
            p["sketch"] = self.sketch_names.get(prof["sketch"]) or fail("sketch not emitted")
            p["profiles"] = self.region_picks(prof["sketch"], prof["regions"])
        elif prof["kind"] == "historical_faces" and sp.get("historical_face_sketch"):
            # cadmpeg matched the picked faces to a sketch profile lying `offset` along its normal.
            sref = sp["historical_face_sketch"]
            p["sketch"] = self.sketch_names.get(sref) or fail("sketch not emitted")
            p["profiles"] = self.profile_picks(sref, [int(x) for x in sp["historical_face_profiles"].split(",")])
            # The faces lie `historical_face_offset` along that sketch's normal; cadmpeg's own
            # start and extent (drafts, two sides) are measured from the faces' plane.
            d = dict(d)
            st = d.get("start") or {}
            base = st.get("offset", 0.0) if st.get("kind") == "offset_profile_plane" else 0.0
            if st.get("kind") not in (None, "profile_plane", "offset_profile_plane"):
                raise Gap(f"extrude from faces with start {st.get('kind')!r}")
            d["start"] = {"kind": "offset_profile_plane", "offset": float(sp["historical_face_offset"]) + base}
            ext_ = d.get("extent") or {}
            blind = all(s_.get("termination", {}).get("kind") in ("blind", "through_all")
                        for s_ in ([ext_["side"]] if "side" in ext_ else [ext_.get("first", {}), ext_.get("second", {})]))
            if sp.get("extrude_to_distance") and not blind:
                d["extent"] = {"kind": "one_sided", "side": {"termination": {"kind": "blind", "length": float(sp["extrude_to_distance"])}}}
        else:
            raise Gap(f"extrude profile from {prof['kind']}")
        flip = {"profile_normal": False, "reversed_profile_normal": True}.get(d.get("direction", {}).get("kind"))
        if flip is None:
            raise Gap(f"extrude direction {d.get('direction')}")
        start = d.get("start", {})
        if start.get("kind") == "offset_profile_plane":
            p["start_offset"] = start["offset"]
        elif start.get("kind") != "profile_plane":
            raise Gap(f"extrude start {start.get('kind')!r}")
        op = {"new_body": "new", "join": "join", "cut": "cut", "intersect": "intersect"}.get(d.get("op"))
        if op is None:
            raise Gap(f"extrude operation {d.get('op')!r}")
        if not d.get("solid", True):
            raise Gap("surface extrude")

        def side(s):
            t = s["termination"]
            if t["kind"] == "through_all":
                return "through_all", 0.0
            if t["kind"] != "blind":
                raise Gap(f"extrude termination {t['kind']!r}")
            # CADIR v11 names a side's draft `draft` (outward positive, as SolidCraft's taper);
            # `taper` is this converter's own name for one read from the raw parameters
            return t["length"], s.get("taper") or s.get("draft") or 0.0

        ext = d["extent"]
        second = None
        if ext["kind"] == "one_sided":
            L, tp = side(ext["side"])
            if L == "through_all":
                p["through_all"] = True
            else:
                p["distance"] = L
            p["direction"] = "negative" if flip else "positive"
            if tp:
                p["taper"] = deg(tp)
        elif ext["kind"] == "two_sided":
            (L1, t1), (L2, t2) = side(ext["first"]), side(ext["second"])
            if "through_all" in (L1, L2):
                raise Gap("two-sided extrude with a through-all side")
            p["direction"] = "negative" if flip else "positive"
            p["distance"] = L1
            if t1 or t2:
                # SolidCraft's taper applies to the whole extrude: build each side on its own.
                if t1:
                    p["taper"] = deg(t1)
                second = dict(p, distance=L2, direction="positive" if flip else "negative")
                second.pop("taper", None)
                if t2:
                    second["taper"] = deg(t2)
            else:
                p["distance2"] = L2
        elif ext["kind"] == "symmetric":
            p["distance"] = ext["side"]["termination"]["length"] if "side" in ext else ext.get("length")
            p["direction"] = "symmetric"
        else:
            raise Gap(f"extrude extent {ext['kind']!r}")
        p["operation"] = op
        name = self.unique(f["name"])
        p["name"] = name
        self.extrude_params[f["id"]] = (dict(p), second is not None, self.extrude_boxes(p, d))
        self.extrude_loops[f["id"]] = (p.get("profiles", []), self.last_pick_loops or [])
        rest = None
        if op == "new" and len(self.new_slots.get(f["id"], [])) == 1 and len(p.get("profiles") or []) > 1:
            # Fusion makes one body of the regions (they share faces); SolidCraft makes one
            # per region: the first region makes the body, the others are joined into it.
            rest = dict(p, profiles=p["profiles"][1:], operation="join", touching="one_body")
            rest.pop("body_name", None)
            p["profiles"] = p["profiles"][:1]
        self.emit_body_op(f, "solid.extrude", p, op)
        if op == "new":
            new = self.new_slots.get(f["id"], [])
            if rest:
                rest.update(targets=[ns.name(new[0])], name=self.unique(f["name"] + " regions"))
                self.emit("solid.extrude", rest)
            if second:
                second.update(operation="join", targets=[ns.name(new[0])], name=self.unique(f["name"] + " side 2"))
                second.pop("body_name", None)
                self.emit("solid.extrude", second)
        elif second:
            second.update(name=self.unique(f["name"] + " side 2"))
            self.emit("solid.extrude", second)
        self.feature_names[f["id"]] = name

    def emit_body_op(self, f, command, p, op):
        """Emit a profile feature (extrude, revolve) with Fusion's body bookkeeping: a new body
        takes its ledger slot's name; a boolean targets the bodies the ledger says it changes."""
        ns = self.ns_of_feature.get(f["id"])
        A = f.get("_A", [])
        new = self.new_slots.get(f["id"], [])
        if op == "new" and (ns is None or not new) and len(self.ns) <= 1:
            # No ledger data for this feature (Fusion records none for some): in a design with
            # one body namespace, a new body takes the next free slot, as Fusion numbers them.
            if not self.ns:
                self.ns.append(Namespace(0))
            ns = self.ns[0]
            used = set(ns.names) | {s_ for v in self.new_slots.values() for s_ in v}
            # The ledger gives a body first to the next feature with body data, even one that
            # cannot make bodies (a thread): such a slot is this feature's.
            later = [g for g in self.feats if g.get("ordinal", 0) > f.get("ordinal", 0) and self.ns_of_feature.get(g["id"]) is ns]
            slot = None
            for g in later:
                gd = g.get("definition", {})
                if set(self.new_slots.get(g["id"], [])) and (gd.get("definition") in NEEDS_BODY or gd.get("family") in NEEDS_BODY):
                    slot = min(self.new_slots[g["id"]])
                    self.new_slots[g["id"]] = [x for x in self.new_slots[g["id"]] if x != slot]
                    break
            if slot is None:
                slot = next(k for k in range(1, 10000) if k not in used)
            new = self.new_slots[f["id"]] = [slot]
            self.ns_of_feature[f["id"]] = ns
            self.notes.append(f"{f['name']}: no body data in the file; its new body is {ns.name(slot)}")
        if op == "new":
            if ns is None or not new:
                raise Gap(f"new-body {command.split('.')[-1]} whose bodies are not in the ledger")
            p["body_name"] = ns.name(new[0])
            made = [ns.name(new[0])] + [f"{ns.name(new[0])} ({k})" for k in range(2, len(new) + 1)]
            order = self.new_bodies_by_centre(f, command, new)
            if order:
                # SolidCraft makes the solids in pick order, Fusion numbers them its own way:
                # name each solid after the Fusion body whose recorded centre it has.
                p.pop("body_name")
                p["body_names"] = made = [ns.name(s_) for s_ in order]
                new = order
            self.emit(command, p)
            for s_, nm in zip(new, made):
                self.bodies.add(nm)
                self.adopt(ns, s_, nm)
        else:
            if ns is not None and A:
                p["targets"] = self.names(ns, A)
            self.emit(command, p)

    def new_bodies_by_centre(self, f, command, new):
        """The new slots in the order of the solids an extrude makes, paired by least total
        distance between each solid's predicted box centre and the slot's recorded centre;
        None when the boxes or the centres are not all known."""
        if command != "solid.extrude" or len(new) < 2 or len(new) > 7:
            return None
        params = self.extrude_params.get(f["id"])
        C = f.get("_C") or {}
        if not params or params[1] or len(params[2]) != len(new) or not all(s_ in C for s_ in new):
            return None
        from itertools import permutations
        centres = [[(a + b) / 2 for a, b in zip(lo, hi)] for lo, hi in params[2]]
        cost = lambda perm: sum(math.dist(c, C[s_]) for c, s_ in zip(centres, perm))
        order = list(min(permutations(new), key=cost))
        return order if order != list(new) else None

    # ---------- revolve ----------
    def sketch_frame(self, sref):
        fr = self.sketch_by_id[sref]["placement"]["frame"]
        o = [fr["origin"][k] for k in "xyz"]
        n = [fr["normal"][k] for k in "xyz"]
        u = [fr["u_axis"][k] for k in "xyz"]
        v = [n[1] * u[2] - n[2] * u[1], n[2] * u[0] - n[0] * u[2], n[0] * u[1] - n[1] * u[0]]
        dn = sum(a * b for a, b in zip(o, n))
        return [dn * x for x in n], u, v, n  # coordinates are from the projected world origin

    def to_sketch(self, sref, p, vector=False):
        o, u, v, n = self.sketch_frame(sref)
        q = p if vector else [a - b for a, b in zip(p, o)]
        return [sum(a * b for a, b in zip(q, u)), sum(a * b for a, b in zip(q, v)), sum(a * b for a, b in zip(q, n))]

    def sketch_regions(self, sref):
        """The closed regions of a sketch's curves (construction curves left out), as polygons."""
        pls = [polyline(e["geometry"], n_arc=96) for e in self.entities_by_sketch.get(sref, [])
               if not e.get("construction") and e["geometry"]["kind"] != "point"]
        return [c for c, _ in planar.regions([pl for pl in pls if pl])]

    def revolve_picks(self, sref, origin, dirn):
        """The regions of the sketch on one side of the axis (Fusion's whole-sketch selection
        revolves every profile the axis does not cut), each as a point inside it."""
        if sref not in self.curve_ids:
            raise Gap(f"revolve of sketch {short(sref)}, which was not emitted")
        o2, d2 = self.to_sketch(sref, origin), self.to_sketch(sref, dirn, vector=True)
        if abs(o2[2]) > 1e-6 or abs(d2[2]) > 1e-9:
            raise Gap("revolve axis outside the sketch plane")
        L = math.hypot(d2[0], d2[1])
        side = lambda p: ((p[0] - o2[0]) * d2[1] - (p[1] - o2[1]) * d2[0]) / L
        faces = self.sketch_regions(sref)
        keep = [c for c in faces if all(side(p) > -1e-7 for p in c) or all(side(p) < 1e-7 for p in c)]
        if not keep:
            raise Gap("no sketch region lies on one side of the revolve axis")
        picks = []
        for c in keep:
            pt = interior_point(planar.segments(c), [planar.segments(x) for x in faces if x is not c])
            if pt is None:
                raise Gap("no interior point found for a revolve region")
            picks.append({"point": rnd(pt)})
        self.last_pick_loops = [planar.segments(c) for c in keep]
        return picks, keep, (o2, d2, L)

    def revolve_regions_by_selection(self, f, sref, selections, faces):
        """cadmpeg keeps a compact region selection with further regions native (its DR-340): read
        each group's initial region as the selected one, the face of the sketch's arrangement that
        runs along every curve of that region. None when that does not single out faces."""
        if not self.raw or not self.infer.enabled("revolve_region_from_initial_loop"):
            return None
        groups = {g["byte_offset"]: g for g in self.raw.n.get("design_construction_operand_groups", [])}
        suffix = sref.rsplit("@", 1)[1]
        ents = {re.search(r"@\d+c\d+:\d+$", e["id"]).group(): e for e in self.entities_by_sketch.get(sref, [])
                if re.search(r"@\d+c\d+:\d+$", e["id"]) and not e.get("construction")}
        on = lambda q, pl: any(seg_distance(q, a, b) < 1e-6 for a, b in zip(pl, pl[1:]))
        runs_along = lambda c, pl: any(on(a, pl) and on(b, pl) and math.dist(a, b) > 1e-6 for a, b in planar.segments(c))
        chosen, ids_all = [], []
        for sel in selections:
            m_ = re.search(r"operand-group#(\d+)$", sel)
            g = groups.get(int(m_.group(1))) if m_ else None
            cs = [c for c in self.raw.n.get("design_profile_curve_selections", [])
                  if g is not None and c.get("group_record_index") == g["record_index"]
                  and c.get("scope_record_index") == g.get("scope_record_index")]
            rs = ((g or {}).get("sketch_profile") or {}).get("region_selection")
            if len(cs) == 1 and str(cs[0].get("owner_text")) == suffix:
                stored = [[e_["primary_id"] for lp in grp.get("region", []) for e_ in lp.get("entries", [])]
                          for grp in cs[0].get("groups", [])]
            elif rs and str(g["sketch_profile"].get("entity_suffix")) == suffix:
                # Newer cadmpeg reads the record as a nested selection: the selected regions' own loops.
                by_ord: dict = {}
                for lp in rs.get("regions", []):
                    by_ord.setdefault(lp.get("region_ordinal", 0), []).extend(m["curve_primary_id"] for m in lp.get("members", []))
                stored = [by_ord[o] for o in sorted(by_ord)]
            else:
                return None
            for ids in stored:
                held = [ents[k] for k in (f"@{suffix}c{i}:0" for i in ids) if k in ents]
                if not held or len(held) != len(set(ids)):
                    return None
                lines = [polyline(e["geometry"], n_arc=96) for e in held]
                cands = [i for i, c in enumerate(faces) if all(runs_along(c, pl) for pl in lines)]
                if len(cands) != 1:
                    return None
                chosen.append(cands[0])
                ids_all.append(ids)
        if not chosen:
            return None
        chosen = sorted(set(chosen))
        self.infer.use("revolve_region_from_initial_loop", f["name"], f"{len(chosen)} of {len(faces)} regions, curves {ids_all}")
        self.notes.append(f"{f['name']}: revolves {len(chosen)} of the {len(faces)} sketch regions on the axis's side: the "
                          "initial region of Fusion's stored selection")
        return chosen

    def revolve_regions_by_volume(self, f, regions, o2, d2, L):
        """Fusion records which of the sketch's regions a revolve takes as a profile selection
        cadmpeg does not decode; with several regions on the axis's side, take the set whose
        revolved volume (Pappus) equals the final body's, when no later feature changes that
        body. None when that cannot decide (the caller then revolves them all)."""
        if not self.infer.enabled("revolve_regions_by_final_volume") or not self.sc_cli or not self.derive_step:
            return None
        ns = self.ns_of_feature.get(f["id"])
        new = self.new_slots.get(f["id"], [])
        if ns is None or len(new) != 1 or len(regions) > 6:
            return None
        if any(g.get("ordinal", 0) > f.get("ordinal", 0) and self.ns_of_feature.get(g["id"]) is ns
               and new[0] in g.get("_A", []) for g in self.feats):
            return None  # changed later: the final body is not this revolve's
        final = self.final_state.get(ns.index, {}).get(new[0])
        if final is None and len(self.m.get("bodies", [])) == 1:
            i = 0
        else:
            i = self.final_body_for(final) if final is not None else None
        if i is None:
            return None
        try:
            path = self.derive_step(i, f"{f['name']} final reference")
        except Gap:
            return None
        vol = step_volume(self.sc_cli, path, self.script_dir)
        if not vol:
            return None

        def q(poly):  # first moment of the region about the axis line
            pts = [(((p[0] - o2[0]) * d2[0] + (p[1] - o2[1]) * d2[1]) / L, ((p[0] - o2[0]) * d2[1] - (p[1] - o2[1]) * d2[0]) / L)
                   for p in poly]
            return abs(sum((a[1] + b[1]) * (a[0] * b[1] - b[0] * a[1]) for a, b in zip(pts, pts[1:] + pts[:1])) / 6)

        vols = [2 * math.pi * q(r) for r in regions]
        hits = []
        for mask in range(1, 1 << len(regions)):
            v = sum(vols[k] for k in range(len(regions)) if mask >> k & 1)
            if abs(v - vol) <= 2e-3 * vol:
                hits.append(mask)
        if len(hits) != 1:
            return None
        chosen = [k for k in range(len(regions)) if hits[0] >> k & 1]
        if len(chosen) == len(regions):
            return None
        self.infer.use("revolve_regions_by_final_volume", f["name"], f"regions {chosen} of {len(regions)} "
                       f"(revolved {sum(vols[k] for k in chosen):.4f} mm3 vs final {vol:.4f})")
        self.notes.append(f"{f['name']}: revolves {len(chosen)} of the {len(regions)} sketch regions on the axis's side, "
                          "the ones whose revolved volume matches the final body")
        return chosen

    def revolve_from_raw(self, f):
        """A revolve cadmpeg leaves unresolved: its sketch, operation and angle from the native
        scope (prologue: marker 1 at +20, operation u32 at +25; else operation at +21)."""
        scope = self.raw.scope_of(f) if self.raw else None
        if not scope:
            raise Gap("unresolved revolve without its scope record")
        B, a = self.raw.B, scope["byte_offset"]
        op_at = a + 25 if B[a + 20] == 1 else a + 21
        op = {1: "join", 2: "cut", 3: "intersect", 4: "new_body"}.get(struct.unpack_from("<I", B, op_at)[0])
        if op is None:
            raise Gap("unresolved revolve: no operation code in its scope")
        angle = self.raw.role(scope, "AlongAngle")
        if angle is None:
            vals = list(self.raw.feature_params(scope).values())
            if len(vals) != 1:
                raise Gap(f"unresolved revolve with {len(vals)} parameters")
            angle = vals[0]
        stream = f.get("native_ref", "").split(":design-parameter-scope")[0].split(":", 1)[-1]
        sketches = set()
        for ri in self.raw.refs(scope):
            x, y = self.raw.span(ri)
            for m in re.finditer(rb"(?:[0-9]\x00)+", B[x:y]):
                k = m.group().decode("utf-16le")
                sketches.update(s_ for s_ in self.sketch_by_id if s_.endswith("@" + k) and stream.replace("[", "").split("/")[-1] in s_.replace("[", ""))
        if len(sketches) != 1:
            raise Gap(f"unresolved revolve whose sketch is not named once in its records ({len(sketches)})")
        return sketches.pop(), op, angle

    def infer_revolve_axis(self, f, sref, op):
        """The axis of a revolve cadmpeg left unresolved: a line of its sketch (or a world axis in
        the sketch plane) that passes through the centre the ledger gives the body it makes or
        changes (a body of revolution has its box centre on its axis)."""
        ns = self.ns_of_feature.get(f["id"])
        slots = self.new_slots.get(f["id"]) if op == "new_body" else f.get("_A")
        if ns is None or not slots:
            raise Gap("unresolved revolve whose body is not in the ledger")
        C = f["_C"][slots[0]]
        tol = CENTRE_TOL if slots[0] in f.get("_exact", set()) else APPROX_TOL
        cands = []
        for e in self.entities_by_sketch.get(sref, []):
            g = e["geometry"]
            if g["kind"] == "line":
                o, u, v, n = self.sketch_frame(sref)
                a, b = uv(g["start"]), uv(g["end"])
                w = lambda q: [o[i] + q[0] * u[i] + q[1] * v[i] for i in range(3)]
                pa, pb = w(a), w(b)
                d_ = [y - x for x, y in zip(pa, pb)]
                L = math.sqrt(sum(x * x for x in d_))
                if L > EPS:
                    cands.append((pa, [x / L for x in d_]))
        for ax in ([1, 0, 0], [0, 1, 0], [0, 0, 1]):
            if abs(self.to_sketch(sref, ax, vector=True)[2]) < 1e-12 and abs(self.to_sketch(sref, [0, 0, 0])[2]) < 1e-9:
                cands.append(([0.0, 0.0, 0.0], ax))
        scored = []
        for o_, d_ in cands:
            r = [c - x for c, x in zip(C, o_)]
            t = sum(a * b for a, b in zip(r, d_))
            off = math.dist(C, [x + t * y for x, y in zip(o_, d_)])
            try:
                picks, keep, (o2, d2, L) = self.revolve_picks(sref, o_, d_)
            except Gap:
                continue
            axial = 0.0
            if op == "new_body":
                ss = [((p[0] - o2[0]) * d2[0] + (p[1] - o2[1]) * d2[1]) / L for c in keep for p in c]
                axial = abs(0.5 * (min(ss) + max(ss)) - t)
            scored.append((max(off, axial), o_, d_))
        def same(x, y):
            if abs(abs(sum(a * b for a, b in zip(x[2], y[2]))) - 1) > 1e-9:
                return False
            r = [q - p for p, q in zip(x[1], y[1])]
            t = sum(a * b for a, b in zip(r, x[2]))
            return math.dist(r, [t * dd for dd in x[2]]) < 1e-6

        uniq = []
        for c in sorted(scored, key=lambda c: c[0]):
            if not any(same(c, k) for k in uniq):
                uniq.append(c)
        if not uniq or uniq[0][0] > tol:
            raise Gap("unresolved revolve: no sketch line passes through its body's centre")
        if len(uniq) > 1 and uniq[1][0] <= max(tol, 3 * uniq[0][0]):
            raise Gap("unresolved revolve: several candidate axes pass through its body's centre")
        self.notes.append(f"{f['name']}: axis inferred as the sketch line through the body centre, operation "
                          f"{op} and angle from its native scope")
        return uniq[0][1], uniq[0][2]

    def feat_revolve(self, f, d):
        if d.get("definition") == "revolve":
            c = d["construction"]
            prof = c.get("profile", {})
            # cadmpeg #1246 binds the stored selection (profiles, regions or selection operands);
            # the regions are still picked from the whole sketch below (revolve_picks).
            if prof.get("kind") == "sketch":
                sref = prof["value"]
            elif prof.get("kind") in ("sketch_profiles", "sketch_regions", "sketch_selection"):
                sref = prof["value"]["sketch"]
            else:
                raise Gap(f"revolve profile from {prof.get('kind')}")
            if c.get("solid") is False:
                raise Gap("surface revolve")
            op = d.get("op")
            origin = [c["axis"]["origin"][k] for k in "xyz"]
            dirn = [c["axis"]["direction"][k] for k in "xyz"]
            ext = c.get("extent", {})
            if ext.get("kind") != "one_sided" or ext.get("termination", {}).get("kind") != "angle":
                raise Gap(f"revolve extent {ext.get('kind')}/{ext.get('termination', {}).get('kind')}")
            angle = ext["termination"]["angle"]
        else:
            if not self.infer.enabled("revolve_sketch_from_records"):
                raise Gap("undecoded revolve (inference revolve_sketch_from_records is off)")
            sref, op, angle = self.revolve_from_raw(f)
            self.infer.use("revolve_sketch_from_records", f["name"], f"sketch {self.sketch_names.get(sref, short(sref))}")
            forced = self.infer.override(f["name"], "revolve_axis")
            if forced:
                origin, dirn = list(map(float, forced["origin"])), list(map(float, forced["dir"]))
                self.notes.append(f"{f['name']}: axis from the overrides file")
            elif self.infer.enabled("revolve_axis_through_body_centre"):
                origin, dirn = self.infer_revolve_axis(f, sref, op)
                self.infer.use("revolve_axis_through_body_centre", f["name"], f"axis {rnd(origin)} dir {rnd(dirn)}")
            else:
                raise Gap("undecoded revolve axis (inference revolve_axis_through_body_centre is off)")
        opn = {"new_body": "new", "join": "join", "cut": "cut", "intersect": "intersect"}.get(op)
        if opn is None:
            raise Gap(f"revolve operation {op!r}")
        picks, keep, (o2, d2, L2) = self.revolve_picks(sref, origin, dirn)
        if d.get("definition") == "revolve" and prof.get("kind") == "sketch_selection" and len(keep) > 1:
            chosen = self.revolve_regions_by_selection(f, sref, prof["value"].get("selections", []), keep)
            if chosen is not None:
                keep = [keep[i] for i in chosen]
                picks = [picks[i] for i in chosen]
        if len(keep) > 1 and abs(angle - 2 * math.pi) < 1e-9 and opn == "new":
            chosen = self.revolve_regions_by_volume(f, keep, o2, d2, L2)
            if chosen is not None:
                keep = [keep[i] for i in chosen]
                picks = [picks[i] for i in chosen]
        self.revolve_geom[f["id"]] = (sref, origin, dirn, keep)
        p = {"sketch": self.sketch_names[sref], "profiles": picks, "axis": {"origin": rnd(origin), "dir": rnd(dirn)},
             "operation": opn, "name": self.unique(f["name"])}
        if abs(angle - 2 * math.pi) > 1e-9:
            p["angle"] = deg(angle)
            self.notes.append(f"{f['name']}: revolve of {deg(angle)}; its start side follows SolidCraft's sketch-normal convention (unverified against Fusion)")
        self.emit_body_op(f, "solid.revolve", p, opn)
        self.feature_names[f["id"]] = p["name"]

    # ---------- edge treatments ----------
    def edge_points(self, f, groups):
        """The picked edges as points on them (cadmpeg's edge midpoints, in group order)."""
        sp = f.get("source_properties") or {}
        pts = [[float(x) for x in t.split(",")] for t in (sp.get("edge_midpoints") or "").split(";") if t.strip()]
        if any(not isinstance(g.get("edges"), dict) or not isinstance(g["edges"].get("value"), dict) for g in groups):
            raise Gap("edge treatment whose edges are not decoded")
        n = sum(len(g.get("edges", {}).get("value", {}).get("edges", [])) for g in groups)
        if not pts or len(pts) != n:
            raise Gap(f"edge treatment whose edges have no decoded points ({len(pts)} points for {n} edges)")
        out, k = [], 0
        for g in groups:
            m_ = len(g["edges"]["value"]["edges"])
            out.append(pts[k:k + m_])
            k += m_
        return out

    def edge_target(self, f):
        ns = self.ns_of_feature.get(f["id"])
        A = f.get("_A", [])
        if ns is None or len(A) != 1 or A[0] not in ns.names:
            raise Gap(f"edge treatment on {len(A)} bodies of the ledger")
        return ns.names[A[0]]

    def feat_fillet(self, f, d):
        groups = d.get("groups", [])
        body = self.edge_target(f)
        for g, pts in zip(groups, self.edge_points(f, groups)):
            r = g.get("radius", {})
            if r.get("kind") != "constant":
                raise Gap(f"fillet radius {r.get('kind')}")
            if abs(g.get("tangency_weight", 1.0) - 1.0) > 1e-9:
                raise Gap("fillet with a tangency weight")
            self.emit("solid.fillet", {"edges": [rnd(p_) for p_ in pts], "radius": r["radius"], "body": body,
                                       "name": self.unique(f["name"])})
        self.feature_names[f["id"]] = f["name"]

    def feat_chamfer(self, f, d):
        groups = d.get("groups", [])
        body = self.edge_target(f)
        for g, pts in zip(groups, self.edge_points(f, groups)):
            sp_ = g.get("spec", {})
            p = {"edges": [rnd(p_) for p_ in pts], "distance": sp_.get("distance"), "body": body, "name": self.unique(f["name"])}
            if sp_.get("kind") == "distance":
                pass
            elif sp_.get("kind") == "distance_angle":
                p["angle"] = deg(sp_["angle"])
                if d.get("flip_direction"):
                    p["flip"] = True
                self.notes.append(f"{f['name']}: distance-angle chamfer: which face the distance runs along follows "
                                  "SolidCraft's default (the face facing up most); Fusion's choice is not decoded")
            elif sp_.get("kind") == "two_distances":
                p["distance"], p["distance2"] = sp_.get("first"), sp_.get("second")
                self.notes.append(f"{f['name']}: two-distance chamfer: which face takes which distance follows "
                                  "SolidCraft's default (the face facing up most first); Fusion's choice is not decoded")
            else:
                raise Gap(f"chamfer {sp_.get('kind')}")
            if p["distance"] is None:
                raise Gap("chamfer without a distance")
            self.emit("solid.chamfer", p)
        self.feature_names[f["id"]] = f["name"]

    def feat_primitive(self, f, d):
        """A box or cylinder primitive that makes a new body. cadmpeg gives its sizes but not
        where it stands, so it is placed by the centre Fusion records for its body, with its
        sides along the world axes (inference primitive_placed_by_body_centre)."""
        so = d.get("solid") or {}
        if so.get("kind") == "sphere":
            # cadmpeg after CADIR v11: a sphere centred on the primitive's frame
            if so.get("latitudes") or so.get("longitude"):
                raise Gap("partial sphere primitive")
            pl = d.get("placement") or []
            if len(pl) != 3:
                raise Gap("sphere primitive without a placement")
            r = so.get("radius")
            r = r.get("value") if isinstance(r, dict) else r
            return self.feat_sphere(f, {"center": {k: pl[i][3] for i, k in enumerate("xyz")}, "radius": r, "op": d.get("op")})
        ns = self.ns_of_feature.get(f["id"])
        new = self.new_slots.get(f["id"], [])
        if ns is None or len(new) != 1 or new[0] not in f.get("_C", {}) or new[0] not in f.get("_exact", set()):
            raise Gap("primitive whose new body has no exact centre in the file")
        if not self.infer.enabled("primitive_placed_by_body_centre"):
            raise Gap("primitive placement not decoded (inference primitive_placed_by_body_centre is off)")
        C = list(f["_C"][new[0]])
        i = self.final_body_for(C)
        box = self._final_boxes[i] if i is not None else None
        ext = [hi - lo for lo, hi in zip(*box)] if box else None
        if d.get("definition") == "block":
            dims = list(d.get("dimensions") or [])
            rot = [row[:3] for row in d.get("placement") or []]
            pl = d.get("placement") or []
            if len(dims) == 3 and len(pl) == 3 and rot != [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]:
                # cadmpeg's placement is the box's frame, a corner at its origin: build the box
                # there and move it by that frame, when the frame puts its centre where Fusion
                # records the body's.
                centre = [sum(pl[i][j] * dims[j] / 2 for j in range(3)) + pl[i][3] for i in range(3)]
                if d.get("op", "new_body") != "new_body" or max(abs(a_ - b_) for a_, b_ in zip(centre, C)) > 1e-6:
                    raise Gap("box primitive that is turned, whose frame does not centre it on its body")
                name = self.unique(f["name"])
                self.emit_body_op(f, "solid.box", {"length": dims[0], "width": dims[1], "height": dims[2],
                                                   "corner": [0.0, 0.0, 0.0], "name": name}, "new")
                self.emit("solid.move", {"bodies": [ns.name(new[0])], "matrix": [[float(x) for x in row] for row in pl],
                                         "name": self.unique(f"{f['name']} placement")})
                self.feature_names[f["id"]] = name
                return
            if len(dims) != 3 or rot != [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]:
                raise Gap("box primitive that is turned")
            if ext and any(abs(a_ - b_) > 1e-6 for a_, b_ in zip(ext, dims)):
                raise Gap("box primitive whose final body is not its axis-aligned box")
            cmd, p = "solid.box", {"length": dims[0], "width": dims[1], "height": dims[2], "center": rnd(C)}
            detail = f"box {dims} centred at {rnd(C)}"
        else:
            so = d.get("solid") or {}
            if so.get("kind") != "cylinder" or abs(so.get("angle", 0.0) - 2 * math.pi) > 1e-9:
                raise Gap(f"{so.get('kind')} primitive is not mapped")
            r, h = so["radius"], so["height"]
            if not ext:
                raise Gap("cylinder primitive whose body is not in the final model (its axis is not known)")
            ks = [k for k in range(3) if abs(ext[k] - h) < 1e-6]
            if len(ks) != 1:
                raise Gap("cylinder primitive whose axis its final body's box does not tell")
            base = [C[j] - (h / 2 if j == ks[0] else 0.0) for j in range(3)]
            cmd, p = "solid.cylinder", {"radius": r, "height": h, "base": rnd(base), "axis": "XYZ"[ks[0]]}
            detail = f"cylinder r={r} h={h} along {'XYZ'[ks[0]]} from {rnd(base)}"
        self.infer.use("primitive_placed_by_body_centre", f["name"], detail)
        p.update(operation="new", name=self.unique(f["name"]), body_name=ns.name(new[0]))
        self.emit(cmd, p)
        self.bodies.add(ns.name(new[0]))
        self.adopt(ns, new[0], ns.name(new[0]))
        self.feature_names[f["id"]] = f["name"]

    def feat_sphere(self, f, d):
        """A sphere primitive: cadmpeg gives its centre in model space and its radius. Where
        Fusion records the centre of the body it makes, the two must agree."""
        c, r = d.get("center"), d.get("radius")
        if not isinstance(c, dict) or not r:
            raise Gap("sphere without a decoded centre or radius")
        C = [c[k] for k in "xyz"]
        op = {"new_body": "new", "join": "join", "cut": "cut", "intersect": "intersect"}.get(d.get("op"))
        if op is None:
            raise Gap(f"sphere operation {d.get('op')!r}")
        new = self.new_slots.get(f["id"], [])
        if op == "new" and len(new) == 1 and new[0] in f.get("_exact", set()):
            got = f.get("_C", {}).get(new[0])
            if got is not None and math.dist(got, C) > 1e-3:
                raise Gap(f"sphere centre {rnd(C)} is not its body's recorded centre {rnd(list(got))}")
        p = {"radius": r, "center": rnd(C), "operation": op, "name": self.unique(f["name"])}
        self.emit_body_op(f, "solid.sphere", p, op)
        self.feature_names[f["id"]] = p["name"]

    def feat_hole(self, f, d):
        pls = d.get("placements", [])
        if len(pls) != 1 or pls[0].get("kind") != "directed":
            raise Gap(f"hole with {len(pls)} placement(s)")
        pos = [pls[0]["position"][k] for k in "xyz"]
        dr = [pls[0]["direction"][k] for k in "xyz"]
        sh = d["shape"]
        kind = sh.get("construction", {}).get("kind", {}).get("kind")
        p = {"position": rnd(pos), "direction": rnd(dr), "diameter": sh["diameter"]}
        ck = sh.get("construction", {}).get("kind", {})
        if kind == "countersink":
            p["type"] = "countersink"
            p["cs_diameter"] = ck["diameter"]
            p["cs_angle"] = deg(ck["angle"])
        elif kind == "counterbore":
            p["type"] = "counterbore"
            p["cb_diameter"] = ck["diameter"]
            p["cb_depth"] = ck["depth"]
        elif kind == "simple_drilled":
            p["type"] = "drilled"
        elif kind in ("simple", None):
            p["type"] = "simple"
        else:
            raise Gap(f"hole shape {kind!r}")
        ext = d.get("extent", {})
        if ext.get("kind") == "blind":
            p["depth"] = ext["length"]
        elif ext.get("kind") not in ("through_all", None):
            raise Gap(f"hole extent {ext.get('kind')!r}")
        bot = d.get("bottom", {})
        if bot.get("kind") == "angled":
            if bot.get("depth_to_tip"):
                raise Gap("hole depth measured to the drill tip")
            p["tip_angle"] = deg(bot["included_angle"])
        elif bot.get("kind") not in ("flat", None):
            raise Gap(f"hole bottom {bot.get('kind')!r}")
        if "thread" in json.dumps(d).lower():
            raise Gap("threaded hole")
        p["name"] = self.unique(f["name"])
        self.emit("solid.hole", p)
        self.feature_names[f["id"]] = p["name"]

    def feat_move(self, f, d):
        """A Move of whole bodies, from the transform the decoder states."""
        ns = self.ns_of_feature.get(f["id"])
        if d.get("definition") != "move_body" or ns is None:
            raise Gap("Move whose transform the decoder does not state")
        extra = sorted(k for k in d if k not in ("definition", "bodies", "copies", "translation"))
        if extra or d.get("copies"):
            raise Gap(f"Move with {', '.join(extra) or 'copies'} (only translations are mapped so far)")
        t = [d["translation"][k] for k in "xyz"]
        bodies = self.names(ns, f.get("_A", []))
        self.emit("solid.move", {"bodies": bodies, "translate": rnd(t), "name": self.unique(f["name"])})

    def feat_combine(self, f, d):
        ns = self.ns_of_feature.get(f["id"])
        ops = d["operands"]
        target = self.resolve_bodies(ns, ops["target"])
        tools = self.resolve_bodies(ns, ops["tools"])
        if len(target) != 1:
            raise Gap(f"combine with {len(target)} target bodies")
        tools = [t for t in tools if t not in target]
        op = {"join": "join", "cut": "cut", "intersect": "intersect"}.get(d.get("op"))
        if op is None:
            raise Gap(f"combine operation {d.get('op')!r}")
        threaded = [t for t in tools if (ns.index, t) in self.thread_axes] \
            if op == "cut" and self.sc_target < SC_THREADED_CUT and self.infer.enabled("split_threaded_cut_tool") else []
        plain = [t for t in tools if t not in threaded]
        tname = self.names(ns, target)[0]
        if plain or not threaded:
            p = {"target": tname, "tools": self.names(ns, plain), "operation": op, "name": self.unique(f["name"])}
            if d.get("keep_tools"):
                p["keep_tools"] = True
            self.emit("solid.combine", p)
        for t in threaded:
            # SolidCraft issue 3: a cut by a body with a modeled thread can delete the target.
            # Cutting by the tool's two halves, split through the thread's axis, makes the same
            # solid without it.
            tool = self.names(ns, [t])[0]
            if d.get("keep_tools"):
                self.emit("solid.move", {"bodies": [tool], "translate": [0, 0, 0], "copy": True})
                piece = self.unique(f"{f['name']} tool")
                self.emit("body.rename", {"body": self.next_free(tool), "name": piece})
            else:
                piece = tool
                self.bodies.discard(ns.names.pop(t, ""))
            pt, ax = self.thread_axes[(ns.index, t)][0]
            e = min(([1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0]), key=lambda e_: abs(sum(a * b for a, b in zip(e_, ax))))
            nrm = [ax[1] * e[2] - ax[2] * e[1], ax[2] * e[0] - ax[0] * e[2], ax[0] * e[1] - ax[1] * e[0]]
            L = math.sqrt(sum(x * x for x in nrm))
            self.emit("solid.split_body", {"body": piece, "plane": {"origin": rnd(pt), "normal": rnd([x / L for x in nrm])}})
            self.emit("solid.combine", {"target": tname, "tools": [piece, f"{piece} (1)"], "operation": "cut",
                                        "name": self.unique(f["name"])})
            self.infer.use("split_threaded_cut_tool", f["name"], f"{tool} split through its thread's axis")
        if not d.get("keep_tools"):
            for s in tools:
                self.bodies.discard(ns.names.pop(s, ""))

    def feat_pattern(self, f, d):
        ns = self.ns_of_feature.get(f["id"])
        scope = self.raw.scope_of(f) if self.raw else None
        pat = d.get("pattern", {})
        form = pat.get("form") or pat.get("kind")
        seeds = d.get("seeds", [])
        sel: dict = {}
        feats, seed_bodies = [], []
        if seeds and all(s_["kind"] == "faces" for s_ in seeds) and not f.get("source_properties"):
            if self.solids_unchanged_across(f):
                self.notes.append(f"{f['name']}: {form} of faces that changes no solid body in Fusion (it makes surface "
                                  "bodies); left out")
                return
            raise Gap(f"{form} of faces")
        for s in seeds:
            if s["kind"] == "feature":
                nm = self.feature_names.get(s["value"])
                if nm is None:
                    raise Gap(f"{form} of feature {short(s['value'])}, which was not converted")
                feats.append(nm)
            elif s["kind"] == "bodies":
                seed_bodies += self.resolve_bodies(ns, s["value"])
            else:
                raise Gap(f"{form} seed kind {s['kind']!r}")
        if feats and seed_bodies:
            raise Gap(f"{form} of features and bodies together")
        if not feats and not seed_bodies:
            raise Gap(f"{form} whose seeds the decoder does not give")
        new = self.new_slots.get(f["id"], [])
        if feats:
            sel["features"] = feats
        else:
            known = [b_ for b_ in seed_bodies if b_ in ns.names and ns.names[b_] in self.bodies]
            if known == seed_bodies:
                sel["bodies"] = self.names(ns, seed_bodies)
        if feats and not new and all(self.feature_by_id[s_["value"]].get("source_tag") == "Thread" for s_ in seeds) \
                and all(close(f["_C"].get(s_, ()), f.get("_prevC", {}).get(s_, ())) for s_ in f.get("_A", [])):
            # A mirrored or patterned thread that lands on no face: Fusion leaves the bodies as
            # they were, SolidCraft refuses the feature. Leave it out.
            self.notes.append(f"{f['name']}: {form} of a thread that changes no body in Fusion (no face to thread "
                              "where it lands); SolidCraft refuses it, so it is left out")
            return
        name = self.unique(f["name"])
        if form == "mirror":
            if pat.get("plane_normal"):
                plane = {"origin": [pat["plane_origin"][k] for k in "xyz"], "normal": [pat["plane_normal"][k] for k in "xyz"]}
            else:
                plane = None  # the decoder resolves no plane for it
            if plane is None and new:
                plane = self.infer_mirror_plane(f, new)
            if plane is None:
                raise Gap("mirror plane not found in its records")
            if seed_bodies and new:
                geo = self.mirror_seeds_by_centre(f, plane, new)
                if geo and sorted(set(geo)) != sorted(seed_bodies):
                    self.notes.append(f"{f['name']}: seed bodies {[ns.name(x) for x in geo]} found by mirroring centres "
                                      f"(design ids gave slots {seed_bodies})")
                    seed_bodies = sorted(set(geo), key=geo.index)
                    sel.pop("bodies", None)
                if "bodies" not in sel:
                    sel["bodies"] = self.names(ns, seed_bodies)
                # The copies come out in the seeds' order: match each new slot to its seed's copy.
                if geo:
                    new = [n_ for _, n_ in sorted(zip([seed_bodies.index(g) for g in geo], new))]
            if feats and new:
                self.mirror_features_as_bodies(f, ns, seeds, plane, new, name)
                self.feature_names[f["id"]] = name
                return
            p = dict(sel, plane=self.plane_ref(plane), name=name)
            if not new and seed_bodies:
                p["combine"] = True
            self.emit("solid.mirror", p)
            if new:
                if feats:
                    made = self.default_names(len(new))
                else:
                    made = []
                    for b in sel["bodies"]:
                        nm = self.next_free(b)
                        self.bodies.add(nm)
                        made.append(nm)
                for s_, nm in zip(new, made):
                    self.bodies.add(nm)
                    self.adopt(ns, s_, nm)
                if seed_bodies and not feats:
                    for s_, n_ in zip(seed_bodies, new):
                        if (ns.index, s_) in self.thread_axes:
                            self.thread_axes[(ns.index, n_)] = [reflect_axis(a, plane) for a in self.thread_axes[(ns.index, s_)]]
            elif seed_bodies:
                for s_ in seed_bodies:
                    if (ns.index, s_) in self.thread_axes:
                        self.thread_axes[(ns.index, s_)] += [reflect_axis(a, plane) for a in self.thread_axes[(ns.index, s_)]]
        elif form == "circular":
            if "bodies" not in sel and not feats:
                sel["bodies"] = self.names(ns, seed_bodies)
            axis, count, angle = self.raw.circular_pattern(scope) if scope else (None, None, None)
            if axis is None:
                raise Gap("circular pattern axis not found in its records")
            if count is None or angle is None:
                count, angle = self.infer_circular(f, axis, seed_bodies, new, count, angle)
            p = dict(sel, axis={"origin": rnd(axis["origin"]), "dir": rnd(axis["dir"])}, count=count, name=name)
            if angle is not None and abs(abs(angle) - 2 * math.pi) > 1e-9:
                if angle < 0:
                    p["axis"]["dir"] = [-x for x in p["axis"]["dir"]]
                p["angle"] = deg(abs(angle))
            self.emit("solid.pattern.circular", p)
            if new:
                made = self.default_names(len(new)) if feats else [self.next_free(sel["bodies"][0]) for _ in new]
                for s_, nm in zip(new, made):
                    self.bodies.add(nm)
                    self.adopt(ns, s_, nm)
        else:
            raise Gap(f"{form} pattern")
        self.feature_names[f["id"]] = name

    def mirror_features_as_bodies(self, f, ns, seeds, plane, new, name):
        """A feature mirror whose copies Fusion makes new bodies (they touch no body). SolidCraft
        joins a mirrored join into the original body instead, as separate pieces of it, and has
        no way to split pieces into bodies. So: rebuild each seed extrude as new temporary
        bodies (one per disjoint profile), mirror those bodies, and remove the temporaries."""
        temps = []
        for k, s_ in enumerate(seeds):
            ep = self.extrude_params.get(s_["value"])
            if ep is None or ep[1]:
                raise Gap("feature mirror making new bodies of a feature that is not a single extrude")
            params = ep[0]
            base = f"{name} seed {k + 1}"
            q = {kk: vv for kk, vv in params.items() if kk not in ("targets", "body_name")}
            q.update(operation="new", body_name=base, name=self.unique(f"{name} seed {k + 1}"))
            self.emit("solid.extrude", q)
            n_bodies = len(ep[2]) if len(ep) > 2 else len(q.get("profiles") or [None])
            self.temp_boxes = getattr(self, "temp_boxes", {})
            for j, bx in enumerate(ep[2] if len(ep) > 2 else []):
                self.temp_boxes[base if j == 0 else f"{base} ({j + 1})"] = bx
            made = [base] + [f"{base} ({j})" for j in range(2, n_bodies + 1)]
            self.bodies.update(made)
            temps += made
        if len(temps) != len(new):
            raise Gap(f"feature mirror: {len(temps)} mirrored pieces for {len(new)} new Fusion bodies "
                      "(some copies join a body)")
        self.emit("solid.mirror", {"bodies": temps, "plane": self.plane_ref(plane), "name": name})
        copies = []
        for b in temps:
            nm = self.next_free(b)
            self.bodies.add(nm)
            copies.append(nm)
        self.emit("solid.remove", {"bodies": temps, "name": self.unique(f"{name} seeds")})
        self.bodies.difference_update(temps)
        order = self.match_copies(f, temps, copies, new, plane)
        for s_, nm in order:
            self.adopt(ns, s_, nm)
        self.notes.append(f"{f['name']}: feature mirror whose copies are new bodies in Fusion, rebuilt as a body "
                          "mirror of the seed extrude (SolidCraft would join the copies into the original body)")

    def extrude_boxes(self, p, d):
        """World boxes of the solids an extrude of picked profile loops makes (one per loop)."""
        loops = getattr(self, "last_pick_loops", None)
        sk = next((k for k, v in self.sketch_names.items() if v == p.get("sketch")), None)
        if not loops or sk is None or "distance" not in p:
            return []
        fr = self.sketch_by_id[sk]["placement"]["frame"]
        o, n, u = ([fr[k][a] for a in "xyz"] for k in ("origin", "normal", "u_axis"))
        dn = sum(a * b for a, b in zip(o, n))
        o = [dn * x for x in n]
        v = [n[1] * u[2] - n[2] * u[1], n[2] * u[0] - n[0] * u[2], n[0] * u[1] - n[1] * u[0]]
        st = float(p.get("start_offset", 0.0))
        L = float(p["distance"])
        h = {"positive": [st, st + L], "negative": [st - L, st], "symmetric": [st - L / 2, st + L / 2]}[p.get("direction", "positive")]
        if "distance2" in p:
            h = [st - float(p["distance2"]), st + L] if p.get("direction") != "negative" else [st - L, st + float(p["distance2"])]
        # Loops that share a stretch of boundary (regions a sketch line splits) make one solid.
        key = lambda q: (round(q[0], 6), round(q[1], 6))
        groups: list = []
        for lp in loops:
            edges = {frozenset((key(a), key(b))) for a, b in lp}
            hit = [g for g in groups if g[0] & edges]
            if not hit:
                groups.append([edges, list(lp)])
                continue
            hit[0][0] |= edges
            hit[0][1] += list(lp)
            for g in hit[1:]:
                hit[0][0] |= g[0]
                hit[0][1] += g[1]
                groups.remove(g)
        out = []
        for _, lp in groups:
            P = [q for sg in lp for q in sg]
            W = [[o[i] + q[0] * u[i] + q[1] * v[i] + t * n[i] for i in range(3)] for q in P for t in h]
            out.append(([min(w[i] for w in W) for i in range(3)], [max(w[i] for w in W) for i in range(3)]))
        return out

    def match_copies(self, f, temps, copies, new, plane):
        """Pair the mirrored copies with Fusion's new slots by nearest centre (predicted from each
        seed's profile box mirrored across the plane); falls back to creation order."""
        boxes = getattr(self, "temp_boxes", {})
        if not all(t in boxes for t in temps):
            return list(zip(new, copies))
        o, n = plane["origin"], plane["normal"]
        L = math.sqrt(sum(x * x for x in n))
        n = [x / L for x in n]
        pred = {}
        for t, c in zip(temps, copies):
            lo, hi = boxes[t]
            ctr = [(a + b) / 2 for a, b in zip(lo, hi)]
            dd = sum((ctr[i] - o[i]) * n[i] for i in range(3))
            pred[c] = [ctr[i] - 2 * dd * n[i] for i in range(3)]
        left, out = set(copies), []
        for s_ in new:
            c = min(left, key=lambda k: math.dist(pred[k], f["_C"][s_]))
            left.discard(c)
            out.append((s_, c))
        return out

    def mirror_seeds_by_centre(self, f, plane, new):
        """The existing bodies whose centres, mirrored across `plane`, are the new bodies' centres
        (in the new bodies' order), or None when that does not single them out."""
        prev, C = f.get("_prevC", {}), f.get("_C", {})
        o, n = plane["origin"], plane["normal"]
        L = math.sqrt(sum(x * x for x in n))
        n = [x / L for x in n]
        out = []
        for s_ in new:
            c = C.get(s_)
            if c is None:
                return None
            hits = []
            for t, q in prev.items():
                dd = sum((q[i] - o[i]) * n[i] for i in range(3))
                m = [q[i] - 2 * dd * n[i] for i in range(3)]
                if close(m, c, 1e-3):
                    hits.append(t)
            if not hits or any(not close(prev[h], prev[hits[0]]) for h in hits):
                return None
            # Coincident candidates (same box, possibly different inside): take the newest body,
            # which fits the benchmark's final B-rep (Side Panels); the choice is noted.
            if len(hits) > 1:
                self.notes.append(f"{f['name']}: seed is one of the coincident bodies {sorted(hits)}; took the newest "
                                  f"({max(hits)})")
            out.append(max(hits))
        return out

    def infer_mirror_plane(self, f, new):
        """A principal plane that mirrors an existing body's centre onto each new body's centre."""
        prev, C = f.get("_prevC", {}), f.get("_C", {})
        for a in range(3):
            others = [i for i in range(3) if i != a]
            coords = set()
            for s_ in new:
                c = C.get(s_)
                cands = [q for q in prev.values() if c and all(abs(q[i] - c[i]) < 1e-3 for i in others) and abs(q[a] - c[a]) > 1e-6]
                if not cands:
                    break
                coords.add(round((cands[0][a] + c[a]) / 2, 6))
            else:
                if len(coords) == 1:
                    o = [0.0, 0.0, 0.0]
                    o[a] = coords.pop()
                    nrm = [0.0, 0.0, 0.0]
                    nrm[a] = 1.0
                    self.notes.append(f"{f['name']}: mirror plane not in its records; inferred from the bodies it makes "
                                      f"({'XYZ'[a]} = {o[a]:.4f})")
                    return {"origin": o, "normal": nrm}
        return None

    def solids_unchanged_across(self, f):
        """Do the ledgered features around `f` show every body unchanged by it?"""
        before = [g for g in self.feats if g.get("_C") and g.get("ordinal", 0) < f.get("ordinal", 0)]
        after = [g for g in self.feats if g.get("_C") and g.get("ordinal", 0) > f.get("ordinal", 0)]
        if not before or not after:
            return False
        p, q = before[-1], after[0]
        return all(close(p["_C"][s_], q["_C"].get(s_, ())) for s_ in p["_C"] if s_ not in q.get("_A", []))

    def infer_circular(self, f, axis, seed_bodies, new, count, angle):
        """Count and total angle of a body pattern Fusion records without them: one new body per
        seed and copy, at the angle that turns the seed's centre onto its first copy's."""
        if not seed_bodies or not new or len(new) % len(seed_bodies):
            raise Gap("circular pattern count not recorded and not inferable (feature seeds)")
        n = 1 + len(new) // len(seed_bodies)
        if count is not None and count != n:
            raise Gap(f"circular pattern count {count} disagrees with the {len(new)} bodies it makes")
        C, prev = f["_C"], f.get("_prevC", {})
        o, d = axis["origin"], axis["dir"]
        c0 = prev.get(seed_bodies[0]) or C.get(seed_bodies[0])
        c1 = C.get(new[0])

        def radial(c):
            v = [c[i] - o[i] for i in range(3)]
            t = sum(v[i] * d[i] for i in range(3))
            return [v[i] - t * d[i] for i in range(3)]
        u, w = radial(c0), radial(c1)
        if math.hypot(*u) < 1e-6 or math.hypot(*w) < 1e-6:
            raise Gap("circular pattern angle not inferable: a body centre lies on the axis")
        cr = [u[1] * w[2] - u[2] * w[1], u[2] * w[0] - u[0] * w[2], u[0] * w[1] - u[1] * w[0]]
        step = math.atan2(sum(cr[i] * d[i] for i in range(3)), sum(u[i] * w[i] for i in range(3)))
        if abs(abs(step) * n - 2 * math.pi) < 1e-6:
            total = 2 * math.pi
        else:
            total = step * (n - 1)
        self.notes.append(f"{f['name']}: count {n} and angle {math.degrees(total):.4f} deg inferred from the bodies it "
                          "makes (Fusion records no count or angle for it)")
        return n, total

    def thread_record(self, f, d, scope):
        """What the file states about a thread: designation, class, external, modeled, full
        length, length, offset (mm) and hand. From the decoder's thread record; when the decoder
        does not read the record, from its strings as a cosmetic full-length thread (inference
        thread_record_by_strings), which changes no geometry."""
        if d.get("definition") == "cosmetic_thread":
            ext = ((scope or {}).get("thread_construction") or {}).get("extended") or {}
            extent = d.get("extent") or {}
            full = ext.get("full_length")
            if full is None:
                full = extent.get("kind") == "through"
            offset = None
            for o in (self.raw.n.get("design_parameter_owners", []) if self.raw and scope else []):
                if o.get("scope_record_index") == scope["record_index"] and o.get("local_ordinal") == 0:
                    offset = o.get("evaluated_value", 0.0) * 10.0
            cls = ext.get("class") or ""
            external = ext.get("external")
            if external is None and cls:
                external = not cls[-1:].isupper()
            tc = (scope or {}).get("thread_construction") or {}
            return {"major": tc.get("major_diameter") and tc["major_diameter"] * 10.0,
                    "minor": tc.get("minor_diameter") and tc["minor_diameter"] * 10.0,
                    "designation": d.get("designation"), "class": cls, "profile": d.get("standard") or "",
                    "external": external, "modeled": bool(d.get("modeled")), "full": bool(full),
                    "length": extent.get("length"), "offset": offset or 0.0,
                    "left": ext.get("right_handed") is False, "decoded": True}
        if not scope:
            raise Gap("thread without its records")
        if not self.infer.enabled("thread_record_by_strings"):
            raise Gap("thread record not decoded (inference thread_record_by_strings is off)")
        th = self.raw.thread(scope)
        if "designation" not in th:
            raise Gap(f"thread designation not found ({th.get('strings')})")
        self.infer.use("thread_record_by_strings", f["name"], f"{th['designation']} {th.get('class', '')}, taken as cosmetic and full length")
        cls = th.get("class", "")
        return {"designation": th["designation"], "class": cls, "profile": th.get("profile", ""),
                "external": not cls[-1:].isupper() if cls else None, "modeled": False, "full": True,
                "length": None, "offset": 0.0, "left": False, "decoded": False}

    def thread_faces_decoded(self, f, d, scope):
        """The cylinders a thread's face selection names, as the decoder resolves them in the
        state before the thread: [{r, mid, len, radial, ends, src}], or None when any face of the
        selection has no decoded cylinder."""
        if not self.raw or not scope:
            return None
        groups = ((scope.get("thread_construction") or {}).get("face_group_record_indices")) or []
        if not groups:
            return None
        out = []
        for g in groups:
            ops = [o for o in self.raw.n.get("design_face_operands", [])
                   if o.get("group_record_index") == g and o.get("scope_record_index") == scope["record_index"]]
            if len(ops) != 1 or not ops[0].get("resolved_face_slots"):
                return None
            for slot in ops[0]["resolved_face_slots"]:
                ctxs = [c for c in ops[0].get("historical_support_contexts", []) if c.get("active_face_slot") == slot]
                if len(ctxs) != 1:
                    return None
                c = ctxs[0]
                surf = c.get("preceding_surface") or {}
                if surf.get("kind") != "cylinder":
                    return None
                o = [surf["origin"][k] for k in "xyz"]
                ax = [surf["axis"][k] for k in "xyz"]
                L = math.sqrt(sum(x * x for x in ax))
                ax = [x / L for x in ax]
                r = surf["radius"]
                pts = [[q[k] for k in "xyz"] for b_ in c.get("preceding_face_boundaries", []) if b_.get("face_slot") == slot
                       for lp in b_.get("loops", []) for q in lp.get("positions", [])]
                if len(pts) < 2:
                    return None
                ts = [sum((p_[i] - o[i]) * ax[i] for i in range(3)) for p_ in pts]
                t0, t1 = min(ts), max(ts)
                if t1 - t0 < 1e-7:
                    return None
                rad = [pts[0][i] - o[i] - ts[0] * ax[i] for i in range(3)]
                Lr = math.sqrt(sum(x * x for x in rad))
                if abs(Lr - r) > 1e-6 * max(1.0, r):
                    return None  # the boundary vertex is not on the cylinder
                rad = [x / Lr for x in rad]
                at = lambda t, o=o, ax=ax, r=r, rad=rad: [o[i] + t * ax[i] + r * rad[i] for i in range(3)]
                out.append({"r": r, "mid": at(0.5 * (t0 + t1)), "len": t1 - t0, "radial": rad, "axis": ax, "ends": [at(t0), at(t1)],
                            "start": None, "axis_end": at(t1), "src": f"face {slot} of the state before {f['name']}"})
        return out

    def thread_faces_by_radius(self, f, th, D, P, internal, want):
        """Inference thread_face_by_radius: the thread's faces among the cylinders earlier
        features of the body made (extruded circles, revolved profile edges parallel to the axis),
        by the radius the designation fits."""
        targets = [D / 2] + ([(D - 1.0825 * P) / 2] if internal and P else [])
        cyl = [c for c in self.cylinders_before(f) if c.get("external", not internal) != internal
               and not any(math.dist(c["mid"], q) < 1e-6 for q in self.threaded)]
        dep_names = {self.feature_by_id[x]["name"] for x in f.get("dependencies", []) if x in self.feature_by_id}
        if any(c["src"].split(" by ", 1)[1] in dep_names for c in cyl):
            cyl = [c for c in cyl if c["src"].split(" by ", 1)[1] in dep_names]  # the faces of the feature it depends on
        fit = [(min(abs(c["r"] - t) for t in targets), c) for c in cyl]
        # A modeled thread in Fusion takes its designation's size whatever the cylinder's (a 12 mm
        # spigot can carry an M14 thread): the face is the closest cylinder, if clearly so.
        fit = [x for x in fit if x[0] < max(0.05 + (0.6 * P if P else 0.5), 0.25 * D / 2)]
        if not fit:
            raise Gap(f"no cylinder the body's earlier features made fits {th['designation']} "
                      f"(radii {sorted({round(c['r'], 3) for c in cyl})})")
        best = min(e for e, _ in fit)
        faces = []
        for e, c in sorted(fit, key=lambda x: x[0]):
            if e - best > 1e-6:
                continue
            if not any(math.dist(c["mid"], q["mid"]) < 1e-6 for q in faces):
                faces.append(c)
        want = want or len(faces)
        runner = [e for e, c in fit if e - best > 1e-6]
        if runner and min(runner) < best + 0.5 and best > 0.05 + (0.6 * P if P else 0.5):
            raise Gap(f"no cylinder fits {th['designation']} closely, and several are near")
        if len(faces) != want:
            self.notes.append(f"{f['name']}: candidates {[(round(c['r'], 3), rnd(c['mid']), c['src']) for c in faces]}")
            raise Gap(f"{len(faces)} cylinders fit {th['designation']} equally for a thread of {want} face(s)")
        self.infer.use("thread_face_by_radius", f["name"], f"{len(faces)} face(s) r={faces[0]['r']:.4g} mm, {faces[0]['src']}, "
                        f"at {[rnd(c['mid']) for c in faces]}")
        return faces

    def feat_thread(self, f, d):
        """A thread: its record and its faces as the decoder reads them. Only when the decoder
        resolves no cylinder for the selection are the faces inferred by radius (a named
        inference)."""
        scope = self.raw.scope_of(f) if self.raw else None
        th = self.thread_record(f, d, scope)
        if scope is not None and th["decoded"] and scope.get("history_state_id") is None:
            # Fusion stores no result state for it: the feature was not computed (it failed
            # or has nothing to thread) and changes no body.
            self.notes.append(f"{f['name']}: {th['designation']} thread left out: Fusion stores no result for it "
                              "(the feature is not computed in the file)")
            self.status[f["id"]] = "left out"
            return
        modeled = th["modeled"]
        if th["offset"] and not th["full"]:
            if modeled:
                raise Gap("modeled thread with an offset from its end (SolidCraft's thread has no offset)")
            self.notes.append(f"{f['name']}: cosmetic {th['designation']} thread with an offset of {th['offset']:.6g} mm from its "
                              "end left out (SolidCraft's thread has no offset; a cosmetic thread changes no geometry)")
            self.status[f["id"]] = "left out"
            return
        m = re.fullmatch(r"M(\d+(?:\.\d+)?)(?:x(\d+(?:\.\d+)?))?", th["designation"] or "")
        if not m:
            raise Gap(f"thread designation {th['designation']!r} (only ISO metric is mapped)")
        D = float(m.group(1))
        P = float(m.group(2)) if m.group(2) else None
        internal = th["external"] is False
        name_params = {"designation": th["designation"], "modeled": modeled}
        if th["left"]:
            name_params["hand"] = "left"
        forced = self.infer.override(f["name"], "thread_faces")
        if forced:
            for pt in forced:
                self.emit("solid.thread", dict(name_params, face=[float(x) for x in pt], name=self.unique(f["name"])))
            self.notes.append(f"{f['name']}: {th['designation']} on {len(forced)} face(s) from the overrides file")
            self.feature_names[f["id"]] = f["name"]
            return
        faces = self.thread_faces_decoded(f, d, scope) if th["decoded"] else None
        how = "decoded"
        if faces is None:
            if not self.infer.enabled("thread_face_by_radius"):
                raise Gap("thread face not decoded (inference thread_face_by_radius is off)")
            groups = ((scope or {}).get("thread_construction") or {}).get("face_group_record_indices") or []
            faces = self.thread_faces_by_radius(f, th, D, P, internal, len(groups))
            how = "inferred by radius"
        best = min(abs(c["r"] - t) for c in faces for t in ([D / 2] + ([(D - 1.0825 * P) / 2] if internal and P else [])))
        if best > 0.05 + (0.6 * P if P else 0.5):
            self.notes.append(f"{f['name']}: the face (r={faces[0]['r']:.4g} mm) is not {th['designation']}'s size; "
                              "Fusion's modeled thread takes the designation's size on it")
        for c in faces:
            # Fusion resizes the threaded face, cosmetic or modeled: a shaft to the major diameter
            # its thread record states (the class's, e.g. 9.85 mm for M10x1.5 6g), a bore to the
            # record's minor diameter. SolidCraft cuts a modeled thread into a shaft of the nominal
            # size, so a modeled shaft goes to the nominal diameter instead.
            stated = th.get("minor" if internal else "major")
            if modeled or not stated:
                dr = D / 2 - c["r"] if not internal else min(0.0, -((D - 1.0825 * P) / 2 - c["r"]) if P else 0.0)
                if modeled and internal and stated:
                    # only widen: SolidCraft's offset of a bore inwards fails its own check
                    dr = min(0.0, -(stated / 2 - c["r"]))
                to = "nominal size" if not (internal and stated) else "the thread record's minor diameter"
            else:
                dr = (stated / 2 - c["r"]) * (-1 if internal else 1)
                to = f"the thread record's {'minor' if internal else 'major'} diameter ({stated:.6g} mm)"
            shift = lambda q: q
            if abs(dr) > 1e-7 and (modeled or th["decoded"]):
                if internal and dr < 0 and f.get("ordinal") in self.offset_by_cut and c.get("ends") and c.get("axis") \
                        and len(f.get("_A", [])) == 1:
                    # SolidCraft refuses to offset this bore face: cut a coaxial cylinder of the
                    # widened radius over the face's length instead (the same solid)
                    ns_ = self.ns_of_feature.get(f["id"])
                    # a cut whose cap only touches the face's end loses the body (SolidCraft): run
                    # the cylinder a hair past both ends
                    base = [e_ - c["r"] * r_ - BORE_CUT_OVERRUN * a_ for e_, r_, a_ in zip(c["ends"][0], c["radial"], c["axis"])]
                    self.emit("solid.cylinder", {"radius": c["r"] - dr, "height": c["len"] + 2 * BORE_CUT_OVERRUN, "base": rnd(base),
                                                 "axis": rnd(c["axis"]), "operation": "cut",
                                                 "targets": self.names(ns_, f["_A"]), "name": self.unique(f"{f['name']} bore")})
                    self.infer.use("bore_widened_by_cut", f["name"], f"r {c['r']:.6g} to {c['r'] - dr:.6g} mm")
                else:
                    self.emit("solid.offset_face", {"faces": [rnd(c["mid"])], "distance": dr})
                k_ = dr if not internal else -dr  # a bore's outward normal points to the axis
                shift = lambda q, k_=k_, c=c: q and [x + k_ * y for x, y in zip(q, c["radial"])]
                c = dict(c, mid=shift(c["mid"]), start=shift(c.get("start")), ends=[shift(q) for q in c.get("ends", [])],
                         axis_end=shift(c.get("axis_end")))
                self.notes.append(f"{f['name']}: {'bore' if internal else 'shaft'} face "
                                  f"{'widened by a cylinder cut' if self.cmds[-1]['command'] == 'solid.cylinder' else 'offset'} "
                                  f"by {dr:+.4g} mm to {to}, as Fusion's thread resizes it")
            self.threaded.append(c["mid"])
            ns_ = self.ns_of_feature.get(f["id"])
            if modeled and ns_ is not None and len(f.get("_A", [])) == 1 and c.get("axis"):
                pt = [m_ - c["r"] * r_ for m_, r_ in zip(c["mid"], c["radial"])]
                self.thread_axes.setdefault((ns_.index, f["_A"][0]), []).append((pt, list(c["axis"])))
            p = dict(name_params, face=rnd(c["mid"]), name=self.unique(f["name"]))
            if th["length"] and not th["full"] and th["length"] < c["len"] - 1e-6:
                p["extent"] = "length"
                p["length"] = th["length"]
                if c.get("start"):
                    p["from"] = rnd(c["start"])
                elif c.get("axis_end") and self.infer.enabled("thread_start_at_axis_end"):
                    p["from"] = rnd(c["axis_end"])
                    self.infer.use("thread_start_at_axis_end", f["name"], f"{th['length']:.6g} mm from {p['from']}")
            self.emit("solid.thread", p)
        self.notes.append(f"{f['name']}: {th['designation']} {th['class']} ({th['profile']}), "
                          f"{'modeled' if modeled else 'cosmetic'}{', left-handed' if th['left'] else ''}, on {len(faces)} face(s) "
                          f"of r={faces[0]['r']:.4g} mm ({faces[0]['src']}; {how}); Fusion's tolerance class is not carried over")
        self.feature_names[f["id"]] = f["name"]

    def cylinders_before(self, f):
        """Cylindrical faces earlier features made on the bodies `f` changes: {r, mid (a point on
        the face), len, start (near the end it was made from), src}."""
        ns = self.ns_of_feature.get(f["id"])
        slots = set(f.get("_A", []))
        out = []
        deps = set(f.get("dependencies", []))
        for g in self.feats:
            if g.get("ordinal", 0) >= f.get("ordinal", 0):
                continue
            if ns is None and not slots:
                mine = True  # no ledger for this feature: any earlier body feature of the design
            else:
                mine = ns is not None and self.ns_of_feature.get(g["id"]) is ns and \
                    slots & (set(g.get("_A", [])) | set(self.new_slots.get(g["id"], [])))
            if not mine and g["id"] not in deps:
                continue
            if g["id"] in self.revolve_geom:
                sref, o, dvec, keep = self.revolve_geom[g["id"]]
                o2, d2 = self.to_sketch(sref, o), self.to_sketch(sref, dvec, vector=True)
                Ld = math.hypot(d2[0], d2[1])
                fo, u, v, n = self.sketch_frame(sref)
                w2 = [d2[1] / Ld, -d2[0] / Ld]
                w = [w2[0] * u[i] + w2[1] * v[i] for i in range(3)]
                dn = [x / math.sqrt(sum(y * y for y in dvec)) for x in dvec]
                for poly in keep:
                    # one face per run of collinear edges (a sketch vertex can split a line)
                    segs = []
                    for p_, q_ in planar.segments(poly):
                        sp = [((x[0] - o2[0]) * d2[0] + (x[1] - o2[1]) * d2[1]) / Ld for x in (p_, q_)]
                        rr = [((x[0] - o2[0]) * d2[1] - (x[1] - o2[1]) * d2[0]) / Ld for x in (p_, q_)]
                        if abs(rr[0] - rr[1]) > 1e-7 or abs(sp[0] - sp[1]) < 1e-6 or abs(rr[0]) < 1e-6:
                            continue
                        if segs and abs(segs[-1][2][1] - rr[0]) < 1e-7 and math.dist(segs[-1][0][1], p_) < 1e-7:
                            segs[-1] = ((segs[-1][0][0], q_), (segs[-1][1][0], sp[1]), rr)
                            continue
                        segs.append(((p_, q_), sp, rr))
                    if len(segs) > 1 and abs(segs[0][2][0] - segs[-1][2][0]) < 1e-7 and math.dist(segs[-1][0][1], segs[0][0][0]) < 1e-7:
                        segs[0] = ((segs[-1][0][0], segs[0][0][1]), (segs[-1][1][0], segs[0][1][1]), segs[0][2])
                        segs.pop()
                    for (p_, q_), sp, rr in segs:
                        at = lambda s_: [o[i] + s_ * dn[i] + rr[0] * w[i] for i in range(3)]
                        # regions are counter-clockwise: material lies left of the edge; toward
                        # the axis there means a shaft's face, away from it a bore's.
                        left = [-(q_[1] - p_[1]), q_[0] - p_[0]]
                        toward = -(left[0] * d2[1] - left[1] * d2[0]) / Ld * (1 if rr[0] > 0 else -1)
                        out.append({"external": toward > 0, "r": abs(rr[0]), "mid": at(0.5 * (sp[0] + sp[1])), "len": abs(sp[1] - sp[0]),
                                    "start": None, "src": f"revolved by {g['name']}",
                                    "radial": [x * (1 if rr[0] > 0 else -1) for x in w], "axis": dn})
            elif g.get("source_tag") == "Extrude" and g["id"] in self.extrude_params:
                e = g["definition"]
                prof = e.get("profile", {})
                sref = (prof.get("value") or {}).get("sketch") if isinstance(prof.get("value"), dict) else prof.get("sketch")
                ext = e.get("extent", {})
                if not sref or ext.get("kind") != "one_sided" or ext["side"]["termination"].get("kind") != "blind":
                    continue
                fo, u, v, n = self.sketch_frame(sref)
                off = e.get("start", {}).get("offset", 0.0) if e.get("start", {}).get("kind") == "offset_profile_plane" else 0.0
                L = ext["side"]["termination"]["length"]
                sgn = -1 if e.get("direction", {}).get("kind") == "reversed_profile_normal" else 1
                for ent in self.entities_by_sketch.get(sref, []):
                    gg = ent["geometry"]
                    if gg["kind"] != "circle" or ent.get("construction"):
                        continue
                    c2, r = uv(gg["center"]), gg["radius"]
                    picks, loops = self.extrude_loops.get(g["id"], ([], []))
                    pts = [q["point"] for q in picks if isinstance(q, dict) and "point" in q]
                    inside_c = any(math.dist(q, c2) < r for q in pts)
                    hole_of = any(len([x for x in crossings(lp, c2[1]) if x < c2[0]]) % 2 == 1 for lp in loops)
                    if pts and not (inside_c or hole_of):
                        continue  # a circle of the sketch this extrude did not use
                    at = lambda h, rad=0.0: [fo[i] + c2[0] * u[i] + (c2[1] + rad) * v[i] + sgn * h * n[i] for i in range(3)]
                    outside_c = hole_of and any(math.dist(q, c2) > r for q in pts)
                    if pts and inside_c and outside_c:
                        continue  # material gone on both sides: no face along this circle
                    cut = (e.get("op") or "") == "cut"
                    # a point near the end the extrude starts from: later features (a counterbore,
                    # a cross bore) more often take away the far part of the face than its mouth
                    h = off + min(0.25 * L, 0.5)
                    out.append({"external": (not cut) if inside_c else cut, "r": r, "mid": at(h, r), "len": L, "start": at(off + 0.01 * L, r),
                                "src": f"extruded by {g['name']}", "radial": list(v), "axis": list(n)})
        return out

    # ---------- driver ----------
    def run(self, until: str | None = None):
        handlers = {"sketch": self.feat_sketch, "extrude": self.feat_extrude, "hole": self.feat_hole,
                    "combine": self.feat_combine, "pattern": self.feat_pattern, "datum_plane": self.feat_datum_plane,
                    "datum_axis": self.feat_datum_axis, "delete_body": self.feat_delete_body,
                    "base_feature": self.feat_base, "revolve": self.feat_revolve,
                    "fillet": self.feat_fillet, "chamfer": self.feat_chamfer,
                    "assembly_joint": self.feat_no_geometry, "datum_point": self.feat_no_geometry,
                    "insert_component": self.feat_no_geometry, "component_transfer": self.feat_no_geometry,
                    "split_body": self.feat_split, "primitive": self.feat_primitive, "block": self.feat_primitive,
                    "sphere": self.feat_sphere}
        self.stopped = False
        if self.constraints_on and self.sc_cli:
            head = constraints.parameter_commands(self.m)
            self.sketch_probe = constraints.SketchProbe(self.sc_cli, head)
            ok, k, err = self.sketch_probe.run([])
            if ok is None and head:  # a parameter SolidCraft refuses: carry the values only
                self.notes.append(f"user parameters left out: SolidCraft refused one ({err[:120]})")
                head = []
                self.sketch_probe = constraints.SketchProbe(self.sc_cli, head)
            for c in head:
                self.emit(c["command"], c["params"])
            if head:
                self.notes.append(f"{len(head)} user parameter(s) carried over")
        for f in self.feats:
            d = f.get("definition", {})
            kind = d.get("definition")
            tag = f.get("source_tag")
            self._cur = f.get("ordinal")
            n_notes = len(self.notes)
            try:
                self._run_one(f, d, kind, tag, handlers, until)
            except StopIteration:
                break
            finally:
                self.feature_notes[f["id"]] = self.notes[n_notes:]
        if not self.stopped and not until and self.infer.enabled("hide_history_only_bodies"):
            self.hide_history_only_bodies()
        return self.gaps

    def hide_history_only_bodies(self):
        """Hide the bodies Fusion keeps out of its current model (Combine tools kept in the history,
        hidden in Fusion): a slot whose last centre matches no final body and whose number is no
        final body's B-rep entity number."""
        final_entities = {int(m_.group(1)) for b in self.m.get("bodies", [])
                          if (m_ := re.search(r"brep:entity#(\d+)$", b["id"]))}
        hide = []
        for ns in self.ns:
            for s_ in self.removed.get(ns.index, []):
                nm = ns.names.get(s_)
                if nm in self.bodies and s_ not in final_entities and nm not in self.hidden:
                    hide.append(nm)
        if hide:
            self.emit("browser.visibility", {"items": hide, "visible": False})
            self.hidden.extend(hide)
            self.infer.use("hide_history_only_bodies", "end of timeline", ", ".join(hide))
            self.notes.append(f"hidden as in Fusion (outside its current model): {', '.join(hide)}")

    def _run_one(self, f, d, kind, tag, handlers, until):
        if True:
            if until and f["name"] == until:
                self.notes.append(f"stopped before {until} (--until): {sum(1 for x in self.feats if x.get('ordinal', 0) >= f.get('ordinal', 0))} feature(s) left out")
                raise StopIteration
            self.cur = f
            if f.get("suppressed"):
                self.notes.append(f"{f['name']}: suppressed in Fusion, left out")
                self.status[f["id"]] = "suppressed"
                return
            if f["id"] in self.absorbed:
                self.notes.append(f"{f['name']}: its bodies come in with {self.absorbed[f['id']]}'s import (final B-rep)")
                self.status[f["id"]] = "imported"
                self.frozen.append(f["name"])
                return
            if self.stopped:
                self.gaps.append({"feature": f["name"], "kind": tag, "reason": "after an earlier gap"})
                self.status[f["id"]] = "not reached"
                return
            h = handlers.get(kind)
            if kind == "move_body":
                h = self.feat_move
            elif kind == "native" and tag == "Extrude":
                h = self.feat_extrude
            elif kind == "unresolved" and d.get("family") == "thread" or kind in ("thread", "cosmetic_thread"):
                h = self.feat_thread
            elif kind == "native" and tag in ("JointOrigin", "Assemble", "Component Insert", "Joint",
                                              "AsBuiltJoint", "RigidGroup"):
                h = self.feat_no_geometry
            elif kind == "native" and tag == "CopyPaste" and self.occurrence_copy(f):
                h = self.feat_no_geometry
            elif kind == "native" and tag == "Derived Base Feature" and not f.get("outputs") \
                    and not (f.get("source_properties") or {}).get("body_centers"):
                # cadmpeg (CADIR v11) lists a derive it cannot resolve; the bodies it brings in
                # come from the final B-rep as bodies no feature made (import_preexisting)
                self.notes.append(f"{f['name']}: derive not decoded by cadmpeg; its bodies come in from the final B-rep")
                h = self.feat_no_geometry
            elif kind == "native" and tag == "Body history change":
                # cadmpeg (CADIR v11): a body's final B-rep differs from the last feature it
                # decodes, by history it cannot read; the final-shape check judges the result
                self.notes.append(f"{f['name']}: cadmpeg reports body changes after the last decoded feature; "
                                  "not converted (the final-shape check compares the result)")
                h = self.feat_no_geometry
            elif kind == "native" and tag == "Screw" and self.infer.enabled("no_geometry_by_absence") \
                    and not (f.get("source_properties") or {}).get("body_centers") \
                    and not (self.raw and self.raw.scope_of(f) and self.raw.feature_params(self.raw.scope_of(f))):
                self.infer.use("no_geometry_by_absence", f["name"], "native Screw with no parameters and no body data")
                h = self.feat_no_geometry
            elif kind == "unresolved" and d.get("family") == "revolve":
                h = self.feat_revolve
            mark, names_before = len(self.cmds), set(self.bodies)
            try:
                if f.get("_preexisting"):
                    self.import_preexisting(f)
                if h is None:
                    raise Gap(f"{tag} ({kind}) has no mapping")
                if f.get("ordinal") in self.refused:
                    raise Gap(f"SolidCraft refused it: {self.refused[f.get('ordinal')]}")
                try:
                    h(f, d)
                except Gap:
                    raise
                except Exception as e:  # a converter bug on unexpected data: a gap, not a crash
                    raise Gap(f"converter error: {type(e).__name__}: {e}") from e
                if f.get("ordinal") in self.unvalidated and len(self.cmds) > mark:
                    # SMLib's self-check refused what this feature makes (a SolidCraft issue): run it
                    # unchecked; the checks below still compare its bodies with Fusion's
                    self.cmds.insert(mark, {"command": "diagnostics.validation", "params": {"mode": "off"}})
                    self.cmd_owner.insert(mark, f.get("ordinal"))
                    self.emit("diagnostics.validation", {"mode": "each_feature"})
                    self.infer.use("unvalidated_on_self_check", f["name"], self.unvalidated[f["ordinal"]][:120])
                    self.notes.append(f"{f['name']}: run with SolidCraft's validation off (SMLib's self-check refused it: "
                                      f"{self.unvalidated[f['ordinal']][:120]})")
            except Gap as g:
                del self.cmds[mark:]
                del self.cmd_owner[mark:]
                self.bodies = names_before
                if self.freeze(f, str(g)):
                    self.status[f["id"]] = "imported"
                    return
                if self.infer.enabled("skip_unconvertible") and f.get("_A") and not self.new_slots.get(f["id"]):
                    # it changes bodies that exist: go on without it, so the rest of the timeline
                    # still converts (the result lacks this feature, and the report says so)
                    self.infer.use("skip_unconvertible", f["name"], str(g)[:200])
                    self.notes.append(f"{f['name']}: NOT CONVERTED and left out ({g}); the bodies it changes lack it")
                    self.status[f["id"]] = "skipped"
                    self.skipped.append(f["name"])
                    return
                self.gaps.append({"feature": f["name"], "kind": tag, "reason": str(g), "ordinal": f.get("ordinal")})
                self.status[f["id"]] = "gap"
                self.stopped = True
                return
            self.status[f["id"]] = "converted" if len(self.cmds) > mark else "left out"
            ns = self.ns_of_feature.get(f["id"])
            if ns is not None:
                expect, exact = {}, set()
                for s in sorted(set(f.get("_A", [])) | set(self.new_slots.get(f["id"], []))):
                    if s in ns.names and ns.names[s] in self.bodies and s in f.get("_C", {}):
                        expect[ns.names[s]] = f["_C"][s]
                        if s in f.get("_exact", set()):
                            exact.add(ns.names[s])
                if expect and len(self.cmds) > mark:
                    self.checks.append({"feature": f["name"], "ordinal": f.get("ordinal"), "upto": len(self.cmds), "expect": expect, "exact": exact})


def polyline_range(g, t0, t1, n=64):
    """Points of a sketch curve over the parameter range cadmpeg gives a boundary use: a
    fraction of a line, angles on a circle or arc, knot parameters on a spline."""
    k = g["kind"]
    if k == "line":
        a, b = uv(g["start"]), uv(g["end"])
        return [[a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t] for t in (t0, t1)]
    if k in ("arc", "circle"):
        # cadmpeg gives a circle's or arc's range as angles (radians), in the curve's own direction
        c, r = uv(g["center"]), g["radius"]
        a0, a1 = t0, t1
        while a1 <= a0:
            a1 += 2 * math.pi
        return [[c[0] + r * math.cos(a0 + (a1 - a0) * i / n), c[1] + r * math.sin(a0 + (a1 - a0) * i / n)] for i in range(n + 1)]
    if k == "nurbs":
        # a spline's range is in its knot parameters
        cv = g["curve"]
        P, U, p = nurbs_poles(cv)[0], cv["knots"], cv["degree"]
        return [deboor(P, U, p, t0 + (t1 - t0) * i / 200) for i in range(201)]
    if k == "ellipse":
        raise Gap("ellipse in a region boundary")
    return []


def polyline(g, n_arc=48):
    k = g["kind"]
    if k == "line":
        return [uv(g["start"]), uv(g["end"])]
    if k in ("arc", "circle"):
        c, r = uv(g["center"]), g["radius"]
        t0, t1 = (g["start_angle"], g["end_angle"]) if k == "arc" else (0.0, 2 * math.pi)
        while t1 <= t0:
            t1 += 2 * math.pi
        return [[c[0] + r * math.cos(t0 + (t1 - t0) * i / n_arc), c[1] + r * math.sin(t0 + (t1 - t0) * i / n_arc)] for i in range(n_arc + 1)]
    if k == "nurbs":
        cv = g["curve"]
        P, U, p = nurbs_poles(cv)[0], cv["knots"], cv["degree"]
        a, b = U[p], U[len(U) - p - 1]
        return [deboor(P, U, p, a + (b - a) * i / 200) for i in range(201)]
    return []


def nurbs_poles(cv):
    """Control points and weights of a cadmpeg NURBS curve (rational with all weights 1 is plain)."""
    P = cv["poles"]
    if isinstance(P, dict):
        pts = P.get("points", [])
        return [uv(q["point"] if "point" in q else q) for q in pts], [q.get("weight", 1.0) for q in pts]
    return [uv(q) for q in P], [1.0] * len(P)


def clamped_knots(n_poles, p):
    spans = n_poles - p
    return [0.0] * (p + 1) + [i / spans for i in range(1, spans)] + [1.0] * (p + 1)


def parse_version(v):
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", v or "")
    return tuple(int(x) for x in m.groups()) if m else None


def solidcraft_target(a):
    """The SolidCraft version to write for: --solidcraft, else the CLI's own, else the baseline."""
    if getattr(a, "solidcraft", None):
        v = parse_version(a.solidcraft)
        if v is None:
            sys.exit(f"f3d2sc: --solidcraft {a.solidcraft!r}: give a version such as 0.49.1")
        return v
    cli = getattr(a, "check", None) or getattr(a, "build", None)
    if cli:
        try:
            r = subprocess.run([cli, "--version"], capture_output=True, text=True, timeout=60)
            v = parse_version(r.stdout)
            if v:
                return v
        except (OSError, subprocess.SubprocessError):
            pass
    return SC_BASELINE


def refit_spline(P, U, p, tol, max_poles=200):
    """Least-squares refit of a clamped B-spline (poles P, knots U, degree p) to a clamped
    uniform one of the same degree with as few poles as keep every sample within `tol` (mm),
    endpoints exact. Returns (poles, max deviation) or None. The deviation is measured at
    matching parameters, so it bounds the distance between the curves from above."""
    a, b = U[p], U[len(U) - p - 1]
    M = 600
    ts = [i / M for i in range(M + 1)]
    pts = [deboor(P, U, p, a + (b - a) * t) for t in ts]
    n = p + 1
    while n <= max_poles:
        K = clamped_knots(n, p)
        rows = [basis_row(K, p, n, t) for t in ts]
        # Interior poles by normal equations, end poles fixed to the curve's ends.
        m = n - 2
        A = [[0.0] * m for _ in range(m)]
        rhs = [[0.0, 0.0] for _ in range(m)]
        for r, q in zip(rows, pts):
            res = [q[0] - r[0] * pts[0][0] - r[-1] * pts[-1][0], q[1] - r[0] * pts[0][1] - r[-1] * pts[-1][1]]
            inner = r[1:-1]
            for i in range(m):
                if inner[i] == 0.0:
                    continue
                rhs[i][0] += inner[i] * res[0]
                rhs[i][1] += inner[i] * res[1]
                for j in range(m):
                    A[i][j] += inner[i] * inner[j]
        X = solve(A, rhs) if m else []
        if X is not None:
            Q = [list(pts[0])] + [list(x) for x in X] + [list(pts[-1])]
            dev = max(math.dist(deboor(Q, K, p, t), q) for t, q in zip(ts, pts))
            if dev <= tol:
                return Q, dev
        n += 1 if n < 24 else 4
    return None


def basis_row(K, p, n, t):
    """Values of the n B-spline basis functions of degree p (knots K) at t."""
    row = []
    for i in range(n):
        e = [0.0] * n
        e[i] = 1.0
        row.append(deboor([[x, 0.0] for x in e], K, p, t)[0])
    return row


def solve(A, B):
    """Gaussian elimination with partial pivoting; B has two columns."""
    n = len(A)
    M = [A[i][:] + B[i][:] for i in range(n)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(M[r][c]))
        if abs(M[piv][c]) < 1e-14:
            return None
        M[c], M[piv] = M[piv], M[c]
        for r in range(n):
            if r != c and M[r][c] != 0.0:
                f = M[r][c] / M[c][c]
                M[r] = [x - f * y for x, y in zip(M[r], M[c])]
    return [[M[i][n] / M[i][i], M[i][n + 1] / M[i][i]] for i in range(n)]


def deboor(P, U, p, t):
    n = len(P) - 1
    k = p
    while k < n and U[k + 1] <= t:
        k += 1
    d = [list(P[j + k - p]) for j in range(p + 1)]
    for r in range(1, p + 1):
        for j in range(p, r - 1, -1):
            i = j + k - p
            den = U[i + p - r + 1] - U[i]
            al = 0.0 if den == 0 else (t - U[i]) / den
            d[j] = [(1 - al) * d[j - 1][0] + al * d[j][0], (1 - al) * d[j - 1][1] + al * d[j][1]]
    return d[p]


def crossings(segs, y):
    xs = []
    for (a, b) in segs:
        if (a[1] > y) != (b[1] > y):
            xs.append(a[0] + (y - a[1]) * (b[0] - a[0]) / (b[1] - a[1]))
    return sorted(xs)


def seg_distance(q, a, b):
    """Distance from 2-D point `q` to the segment `a`-`b`."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((q[0] - a[0]) * dx + (q[1] - a[1]) * dy) / L2))
    return math.hypot(q[0] - a[0] - t * dx, q[1] - a[1] - t * dy)


def interior_point(segs, others):
    """A point inside the loop `segs` and outside the other loops of the sketch: on a few
    horizontal lines across the loop, the widest stretch inside it and outside the others."""
    if not segs:
        return None

    def inside(pt, loop):
        return len([x for x in crossings(loop, pt[1]) if x < pt[0]]) % 2 == 1

    key = sorted(map(str, segs))
    # Only loops lying inside this one are holes in it; loops around it or beside it don't matter.
    others = [o for o in others if sorted(map(str, o)) != key and o and inside(midpoint(o[0]), segs)
              and not inside(midpoint(segs[0]), o)]
    ys = [q[1] for s_ in segs for q in s_]
    lo, hi = min(ys), max(ys)
    best, best_w = None, 0.0
    for i in range(1, 40):
        y = lo + (hi - lo) * (i + 0.137) / 40.3
        xs = crossings(segs, y)
        cuts = sorted(x for o in others for x in crossings(o, y))
        for a, b in zip(xs[0::2], xs[1::2]):
            pts = [a] + [c for c in cuts if a < c < b] + [b]
            for u_, v_ in zip(pts, pts[1:]):
                mid = 0.5 * (u_ + v_)
                if any(inside([mid, y], o) for o in others):
                    continue
                if v_ - u_ > best_w:
                    best, best_w = [mid, y], v_ - u_
    return best


def interior_points(segs, others, nested_are_holes=False):
    """Points covering the loop `segs` as Fusion's profile covers it: one inside each piece the
    other loops cut it into. A profile index names a loop's whole interior, loops nested in it
    included (Bottom Panels' Extrude 3 matches the final B-rep only that way); with
    nested_are_holes, nested loops are left out instead."""
    if not segs:
        return []

    def inside(pt, loop):
        return len([x for x in crossings(loop, pt[1]) if x < pt[0]]) % 2 == 1

    key = sorted(map(str, segs))
    others = [o for o in others if o and sorted(map(str, o)) != key]
    holes, crossing = [], []
    for o in others:
        pts = [q for sg in o for q in sg]
        ins = [inside([p[0] + 1e-7, p[1] + 1.3e-7], segs) for p in pts[:: max(1, len(pts) // 24)]]
        if all(ins):
            (holes if nested_are_holes else crossing).append(o)
        elif any(ins) or loops_cross(o, segs):
            crossing.append(o)
    ys = [q[1] for s_ in segs for q in s_]
    lo, hi = min(ys), max(ys)
    best: dict = {}
    for i in range(1, 80):
        y = lo + (hi - lo) * (i + 0.137) / 80.3
        xs = crossings(segs, y)
        cuts = sorted(x for o in holes + crossing for x in crossings(o, y))
        for a, b in zip(xs[0::2], xs[1::2]):
            pts = [a] + [c for c in cuts if a < c < b] + [b]
            for u_, v_ in zip(pts, pts[1:]):
                mid = [0.5 * (u_ + v_), y]
                if any(inside(mid, o) for o in holes):
                    continue
                sig = tuple(inside(mid, o) for o in crossing)
                if v_ - u_ > best.get(sig, (0, None))[0]:
                    best[sig] = (v_ - u_, mid)
    return [m for _, m in best.values()]


def loops_cross(a, b):
    """Does any segment of loop `a` properly cross a segment of loop `b`?"""
    def box(sg):
        (p, q) = sg
        return min(p[0], q[0]), min(p[1], q[1]), max(p[0], q[0]), max(p[1], q[1])

    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    bb = [box(sg) for sg in b]
    for sa in a:
        ba = box(sa)
        for sb, bxb in zip(b, bb):
            if ba[2] < bxb[0] or bxb[2] < ba[0] or ba[3] < bxb[1] or bxb[3] < ba[1]:
                continue
            (p1, p2), (q1, q2) = sa, sb
            d1, d2 = orient(q1, q2, p1), orient(q1, q2, p2)
            d3, d4 = orient(p1, p2, q1), orient(p1, p2, q2)
            if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)) and min(abs(d1), abs(d2), abs(d3), abs(d4)) > 1e-12:
                return True
    return False


def midpoint(seg):
    (a, b) = seg
    return [0.5 * (a[0] + b[0]) + 1e-7, 0.5 * (a[1] + b[1]) + 1.3e-7]


def fail(msg):
    raise Gap(msg)


def deg(rad):
    return f"{round(math.degrees(rad), 9)} deg"


def clamped_uniform(k, p):
    if len(set(k[: p + 1])) != 1 or len(set(k[-p - 1:])) != 1:
        return False
    full = [k[p]] + k[p + 1: len(k) - p - 1] + [k[len(k) - p - 1]]
    ds = [b - a for a, b in zip(full, full[1:])]
    return not ds or max(ds) - min(ds) <= 1e-9 * max(1.0, max(ds))


def uv(p):
    return [p["u"], p["v"]] if "u" in p else [p[0], p[1]]


def dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def rnd(v):
    return [0.0 if abs(x) < 1e-12 else round(x, 12) for x in v]


def final_body_boxes(m):
    """Each final body's box from its vertices (a box that ignores curved faces' bulges)."""
    pts = {p["id"]: p["position"] for p in m.get("points", [])}
    verts = {v["id"]: pts.get(v["point"]) for v in m.get("vertices", [])}
    edges = {e["id"]: e for e in m.get("edges", [])}
    coedges = {c["id"]: c for c in m.get("coedges", [])}
    loops = {l_["id"]: l_ for l_ in m.get("loops", [])}
    faces_by_shell: dict = {}
    for fc in m.get("faces", []):
        faces_by_shell.setdefault(fc.get("shell"), []).append(fc)
    shells = {s_["id"]: s_ for s_ in m.get("shells", [])}
    regions = {r["id"]: r for r in m.get("regions", [])}
    curves = {c_["id"]: c_.get("geometry", c_) for c_ in m.get("curves", [])}
    out = []
    for b in m.get("bodies", []):
        sh_ids = []
        for rid in b.get("regions", []):
            r = regions.get(rid, {})
            sh_ids += r.get("shells", []) or [x for x in r.get("members", []) if isinstance(x, str)]
        if not sh_ids:
            sh_ids = [s_ for s_ in shells]  # fall back below by face ownership
        P = []
        for sid in sh_ids:
            for fc in faces_by_shell.get(sid, []):
                for lid in (fc.get("loops") or {}).get("loops", []):
                    for cid in (loops.get(lid, {}).get("boundary") or {}).get("coedges", []):
                        e = edges.get(coedges.get(cid, {}).get("edge"))
                        for vid in (e or {}).get("start"), (e or {}).get("end"):
                            q = verts.get(vid)
                            if q:
                                P.append((q["x"], q["y"], q["z"]))
                        P += edge_samples(e, curves)
        out.append(([min(p[i] for p in P) for i in range(3)], [max(p[i] for p in P) for i in range(3)]) if P else None)
    return out


def edge_samples(e, curves, n=32):
    """Points along a circle or ellipse edge (lines are covered by their vertices)."""
    if not e:
        return []
    car = e.get("carrier") or {}
    g = curves.get(car.get("curve"))
    if not g or g.get("kind") not in ("circle", "ellipse"):
        return []
    t0, t1 = car.get("param_range") or (0.0, 2 * math.pi)
    c = [g["center"][k] for k in "xyz"]
    z = [g["axis"][k] for k in "xyz"]
    x = [(g.get("ref_direction") or g.get("major_direction"))[k] for k in "xyz"]
    y = [z[1] * x[2] - z[2] * x[1], z[2] * x[0] - z[0] * x[2], z[0] * x[1] - z[1] * x[0]]
    ra = g.get("radius") or g.get("major_radius")
    rb = g.get("radius") or g.get("minor_radius")
    out = []
    for i in range(n + 1):
        t = t0 + (t1 - t0) * i / n
        out.append(tuple(c[k] + ra * math.cos(t) * x[k] + rb * math.sin(t) * y[k] for k in range(3)))
    return out


def grouped(notes):
    out: dict[str, int] = {}
    for n in notes:
        out[n] = out.get(n, 0) + 1
    return list(out.items())


def write_script(path, cmds):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w") as fh:
        fh.write('{"commands": [\n')
        fh.write(",\n".join("  " + json.dumps(c) for c in cmds))
        fh.write("\n]}\n")


def build_probe(conv: Converter, sc_cli: str, workdir: str, cwd: str | None = None):
    """--build without --check: run the whole script once, and record the feature whose step
    SolidCraft refuses as --check would, so the same fallbacks take it."""
    path = os.path.join(workdir, "probe.json")
    write_script(path, conv.cmds)
    r = subprocess.run([sc_cli, "eval", path], capture_output=True, text=True, cwd=cwd)
    m_ = re.search(r"step (\d+) \(([a-z_.]+)\): (.*)", r.stderr.strip(), re.S) if r.returncode != 0 else None
    if m_:
        k = int(m_.group(1)) - 1
        conv.check_results.append({"status": "failed to run", "step_command": m_.group(2), "message": m_.group(3)[:600],
                                   "step_feature": conv.cmd_owner[k] if 0 <= k < len(conv.cmd_owner) else None})


def check(conv: Converter, sc_cli: str, workdir: str, cwd: str | None = None) -> list[str]:
    """Replay the script up to each feature and compare bounding-box centres with Fusion's."""
    problems = []
    for ck in conv.checks:
        path = os.path.join(workdir, "prefix.json")
        write_script(path, conv.cmds[: ck["upto"]])
        r = subprocess.run([sc_cli, "eval", path], capture_output=True, text=True, cwd=cwd)
        if r.returncode != 0:
            problems.append(f"{ck['feature']}: solidcraft-cli failed: {r.stderr.strip()[:400]}")
            print(f"check {ck['feature']}: FAILED TO RUN", file=sys.stderr)
            err = r.stderr.strip()
            m_ = re.search(r"step (\d+) \(([a-z_.]+)\): (.*)", err, re.S)
            res = {"ordinal": ck["ordinal"], "status": "failed to run", "message": err[:600]}
            if m_:
                k = int(m_.group(1)) - 1
                res.update(step_command=m_.group(2), step_feature=conv.cmd_owner[k] if 0 <= k < len(conv.cmd_owner) else None,
                           step_params=sorted(conv.cmds[k]["params"].keys()) if 0 <= k < len(conv.cmds) else None, message=m_.group(3)[:600])
            conv.check_results.append(res)
            break
        out = json.loads(r.stdout)
        errs = [t for t in out.get("timeline", []) if t.get("error")]
        bodies = {b["name"]: b for b in out.get("bodies", [])}
        bad, loose = [], []
        res = {"ordinal": ck["ordinal"], "bodies": [], "errors": [t["error"] for t in errs]}
        conv.check_results.append(res)
        for name, c in ck["expect"].items():
            b = bodies.get(name)
            if b is None:
                bad.append(f"{name} missing")
                res["bodies"].append({"body": name, "missing": True})
                continue
            got = [(lo + hi) / 2 for lo, hi in zip(b["bbox"]["min"], b["bbox"]["max"])]
            res["bodies"].append({"body": name, "delta_mm": [round(g_ - c_, 6) for g_, c_ in zip(got, c)], "fusion_exact": name in ck["exact"]})
            if close(got, c, CENTRE_TOL):
                continue
            # cadmpeg marks the centres it could compute exactly; the others come from loose boxes
            # (control hulls of freeform faces), so a difference there is not a mismatch.
            (bad if name in ck["exact"] or not close(got, c, APPROX_TOL) else loose).append(f"{name} centre {fmt(got)} vs Fusion {fmt(c)}")
        if loose:
            conv.notes.append(f"{ck['feature']}: {len(loose)} body centre(s) differ from Fusion's approximate ones "
                              "(cadmpeg marks them inexact: loose boxes around freeform faces): " + "; ".join(loose))
        lost = [b_["body"] for b_ in res["bodies"] if b_.get("missing")]
        if lost:
            # SolidCraft ran the feature but lost a body Fusion keeps (SolidCraft issue 3: a cut by a
            # threaded tool deletes its target): treat it as a refusal, so the feature becomes a gap
            res.update(status="failed to run", step_feature=ck["ordinal"],
                       message=f"SolidCraft reports success but loses {', '.join(lost)}")
            problems.append(f"{ck['feature']}: SolidCraft loses {', '.join(lost)}")
            print(f"check {ck['feature']}: LOSES {', '.join(lost)}", file=sys.stderr)
            break
        status = "MISMATCH" if bad or errs else ("ok (approximate centres)" if loose else "ok")
        res["status"] = status
        print(f"check {ck['feature']}: {status}" + ("" if not bad else "  " + "; ".join(bad))
              + "".join(f"  [{t['name']}: {t['error']}]" for t in errs), file=sys.stderr)
        if bad or errs:
            problems.append(f"{ck['feature']}: " + "; ".join(bad + [f"{t['name']}: {t['error']}" for t in errs]))
    return problems


def fmt(v):
    return "(" + ", ".join(f"{x:.4f}" for x in v) + ")"


def decode(f3d: str, decoder: str, workdir: str, step: str | None):
    ir_path = os.path.join(workdir, os.path.basename(f3d) + ".ir.json")
    if step:
        os.makedirs(os.path.dirname(os.path.abspath(step)), exist_ok=True)
    r = subprocess.run([decoder, f3d, ir_path] + ([step] if step else []), capture_output=True, text=True)
    if r.returncode != 0:
        raise DecodeError(r.stderr.strip())
    if step:
        thin_step(step)
    return load_ir(ir_path)


def load_ir(ir_path):
    with open(ir_path) as fh:
        ir = ir_compat.upgrade(json.load(fh))
    # The design stream: the BulkStream.dat cadmpeg's native records name (archives differ:
    # FusionAssetName[Active]/FusionDesignSegmentType1/, Design1/, ...).
    root = ir_path + ".streams"
    named = re.findall(r"([A-Za-z0-9_\[\]]+/[A-Za-z0-9_]+/BulkStream\.dat|[A-Za-z0-9_]+/BulkStream\.dat)",
                       json.dumps(ir.get("model", {}).get("features", []))[:200000])
    cands = []
    for dp, _, fs in os.walk(root):
        for fn in fs:
            if fn == "BulkStream.dat":
                rel = os.path.relpath(os.path.join(dp, fn), root).replace(os.sep, "/")
                cands.append(rel)
    pick = next((c for n in named for c in cands if c.endswith(n)), None)
    if pick is None:
        pick = next((c for c in cands if "Design" in c and "Browser" not in c and "ACT" not in c), None)
    bulk = open(os.path.join(root, pick), "rb").read() if pick else None
    ir["_bulk_path"] = os.path.join(root, pick) if pick else None
    return ir, bulk


NEEDS_BODY = {"thread", "cosmetic_thread", "hole", "split_body", "delete_body", "fillet", "chamfer", "pattern", "combine"}


def reassign_owners(ir, infer=None):
    """cadmpeg places a feature in a component by the naming space its records sit in, which
    for features made in the assembly's context can be the wrong one (a Split in the root put
    in the last component). Correct it from the bodies: a feature naming final bodies belongs
    to their component; a body-changing feature with no body evidence to the component of the
    nearest earlier feature that has some; a construction feature to the component of the
    first feature using it. Notes what it moved."""
    m = ir.get("model", {})
    if ir.get("_owners_fixed") or len(m.get("product_definitions", [])) < 2:
        return
    infer = infer or Inference()
    forced = {}  # feature name -> component name, from the overrides file
    for f in m.get("features", []):
        o = infer.override(f["name"], "owner")
        if o:
            forced[f["id"]] = o
    ir["_owners_fixed"] = True
    owner_of = {b: p["id"] for p in m.get("product_definitions", []) for b in p.get("bodies", [])}
    names = {p["id"]: p.get("source_name") or p["id"] for p in m.get("product_definitions", [])}
    feats = sorted(m.get("features", []), key=lambda f: f.get("ordinal", 0))
    notes = ir.setdefault("_owner_notes", [])
    last, has_body = None, set()
    for f in feats:
        d = f.get("definition", {})
        kind = d.get("definition")
        refs = set(re.findall(r'"(f3d:brep/[^"]+/brep:entity#\d+)"', json.dumps(d)))
        pds = {owner_of[b] for b in refs if b in owner_of}
        if len(pds) == 1:
            new = pds.pop()
            if new != f.get("product_definition"):
                notes.append(f"{f['name']} (#{f.get('ordinal')}): moved from {names.get(f.get('product_definition'))} to "
                             f"{names.get(new)}, whose bodies it names")
                f["product_definition"] = new
            last = new
        elif (f.get("source_properties") or {}).get("body_centers"):
            last = f.get("product_definition")
        elif f["id"] in forced:
            want = next((p["id"] for p in m.get("product_definitions", []) if p.get("source_name") == forced[f["id"]]), None)
            if want:
                notes.append(f"{f['name']} (#{f.get('ordinal')}): placed in {forced[f['id']]} by the overrides file")
                f["product_definition"] = want
        elif (kind in NEEDS_BODY or d.get("family") in NEEDS_BODY) and last and last != f.get("product_definition") \
                and f.get("product_definition") not in has_body and infer.enabled("owner_from_body_evidence"):
            notes.append(f"{f['name']} (#{f.get('ordinal')}): changes a body, but {names.get(f.get('product_definition'))} has "
                         f"none yet; moved to {names.get(last)}, the component of the nearest earlier feature with "
                         "body evidence (an assumption)")
            f["product_definition"] = last
            infer.use("owner_from_body_evidence", f["name"], f"moved to {names.get(last)}")
        if (f.get("source_properties") or {}).get("body_centers") or len(pds) == 0 and refs == set() and kind in ("extrude", "revolve", "base_feature"):
            has_body.add(f.get("product_definition"))
    for f in feats:
        if f.get("definition", {}).get("definition") not in ("datum_plane", "datum_axis", "datum_point"):
            continue
        for g in feats:
            if g.get("ordinal", 0) > f.get("ordinal", 0) and (f["id"] in g.get("dependencies", []) or f["id"] in json.dumps(g.get("definition", {}))):
                if g.get("product_definition") != f.get("product_definition"):
                    notes.append(f"{f['name']} (#{f.get('ordinal')}): moved to {names.get(g.get('product_definition'))} with {g['name']}, which uses it")
                    f["product_definition"] = g.get("product_definition")
                break


def components(ir):
    """The design's components that hold features or bodies (Fusion's local components), in
    timeline order of their first feature."""
    m = ir.get("model", {})
    first, makes = {}, set()
    for f in m.get("features", []):
        pd = f.get("product_definition")
        if pd:
            first[pd] = min(first.get(pd, 1 << 30), f.get("ordinal", 0))
            d = f.get("definition", {})
            if (f.get("source_properties") or {}).get("body_centers") or d.get("definition") in ("extrude", "revolve", "base_feature") \
                    or d.get("family") in ("extrude", "revolve"):
                makes.add(pd)
    # components that hold bodies, or features that make them (joints and inserts alone are not geometry)
    pds = [p for p in m.get("product_definitions", []) if p.get("bodies") or p["id"] in makes]
    return sorted(pds, key=lambda p: first.get(p["id"], 1 << 30))


def component_ir(ir, pd):
    """The IR of one component: its features and final bodies (the rest of the model kept for
    lookups); and the index of each of its bodies in the whole model."""
    m = ir["model"]
    keep = set(pd.get("bodies", []))
    idx = [i for i, b in enumerate(m.get("bodies", [])) if b["id"] in keep]
    sub = dict(m, features=[f for f in m.get("features", []) if f.get("product_definition") == pd["id"]],
               bodies=[m["bodies"][i] for i in idx])
    return dict(ir, model=sub), idx


def convert_design(a, src, out, td, ir_path=None, f3d=None, step=None, pre=None, component=None):
    """Convert one design (an .f3d, or an IR dumped earlier), or one component of it, into the
    script `out`. Returns (converter, gaps, check problems, report, ir)."""
    ir, bulk = pre if pre else (load_ir(ir_path) if ir_path else decode(f3d, a.decoder, td, step))
    whole = ir
    body_index = None
    if component is not None:
        ir, body_index = component_ir(whole, component)

    def derive_step(index, name):
        """Write the final body `index` alone as STEP next to the script; its path as the
        script names it (relative to the script's folder, where scripts are run from)."""
        path = re.sub(r"\.json$", "", out) + "." + re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_") + ".step"
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        k = body_index[index] if body_index is not None else index
        r = subprocess.run([a.decoder, f3d, os.path.join(td, "derive.ir.json"), path, str(k)], capture_output=True, text=True)
        if r.returncode != 0:
            raise Gap(f"writing the derived body's STEP failed: {r.stderr.strip()[:300]}")
        thin_step(path)  # dense thread splines SolidCraft cannot import (step_thin.py)
        return os.path.basename(path)

    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    label = component.get("source_name") if component is not None else \
        re.sub(r"\.solidcraft\.json$|\.json$", "", os.path.basename(out))
    infer = a.infer.for_design(label)
    refused, unvalidated, by_cut = {}, {}, set()
    while True:
        conv = Converter(ir, bulk, None if a.no_refit else a.spline_tol, derive_step if f3d else None, infer=infer,
                         refused=refused, unvalidated=unvalidated)
        conv.offset_by_cut = by_cut
        conv.sc_cli, conv.script_dir = a.check or a.build, os.path.dirname(os.path.abspath(out))
        conv.constraints_on = not getattr(a, "no_constraints", False)
        conv.sc_target = a.sc_target
        gaps = conv.run(a.until)
        problems = check(conv, a.check, td, os.path.dirname(os.path.abspath(out))) if a.check else []
        if a.build and not a.check:
            build_probe(conv, a.build, td, os.path.dirname(os.path.abspath(out)))
        # A feature SolidCraft cannot run is treated as a gap, so the import fallback can take it
        # (when the inference allows); convert again without it.
        failed = next((r_ for r_ in conv.check_results if r_.get("status") == "failed to run" and r_.get("step_feature")), None)
        if failed and failed.get("step_command") == "solid.offset_face" and failed["step_feature"] not in by_cut \
                and infer.enabled("bore_widened_by_cut"):
            by_cut.add(failed["step_feature"])
            infer.used.clear()
            print(f"f3d2sc: SolidCraft refused an offset face of feature #{failed['step_feature']}; converting again with "
                  "its bore widened by a cut", file=sys.stderr)
            continue
        if failed and "SMLib's check refuses" in failed.get("message", "") and failed["step_feature"] not in unvalidated \
                and len(unvalidated) < 8 and infer.enabled("unvalidated_on_self_check"):
            unvalidated[failed["step_feature"]] = failed["message"]
            infer.used.clear()
            print(f"f3d2sc: SMLib's self-check refused feature #{failed['step_feature']}; converting again with it "
                  "unvalidated", file=sys.stderr)
            continue
        if not failed or failed["step_feature"] in refused or len(refused) >= 8 \
                or not infer.enabled("freeze_from_final_brep"):
            break
        refused[failed["step_feature"]] = failed.get("message", "")[:200]
        infer.used.clear()
        print(f"f3d2sc: SolidCraft refused feature #{failed['step_feature']}; converting again with it as a gap", file=sys.stderr)
    final = None
    if a.check and f3d and not a.until:
        idx = body_index if body_index is not None else list(range(len(ir["model"].get("bodies", []))))
        final = final_shape_check(conv, a.check, a.decoder, f3d, idx, out, td)
        if final.get("status") == "mismatch" and infer.enabled("chamfer_side_by_final_shape"):
            final = chamfer_sides_by_final_shape(conv, final, a, f3d, idx, out, td, infer)
        print(f"final shape: {final['summary']}", file=sys.stderr)
    report = {"source": os.path.basename(src), "commands": len(conv.cmds), "gaps": gaps, "imported_instead": conv.frozen,
              "skipped": conv.skipped,
              "check_problems": problems, "final_shape": final, "design": label, "assumptions": infer.report(), "notes": [n + (f" (x{k})" if k > 1 else "") for n, k in grouped(conv.notes)],
              "decode_losses": [l_["message"] for l_ in ir.get("decode_losses", []) if l_.get("severity") != "Info"]}
    if component is not None:
        report["component"] = component.get("source_name")
        report["ownership_notes"] = whole.get("_owner_notes", [])
    return conv, gaps, problems, report, ir


def step_volume(sc_cli, path, cwd):
    """The total solid volume of a STEP file as SolidCraft measures it (None if not closed)."""
    with tempfile.TemporaryDirectory() as td:
        sp = os.path.join(td, "v.json")
        write_script(sp, [{"command": "file.insert_step", "params": {"path": os.path.join(cwd or "", path)}}])
        r = subprocess.run([sc_cli, "eval", sp], capture_output=True, text=True)
        if r.returncode != 0:
            return None
        vols = [b.get("volume_mm3") for b in json.loads(r.stdout).get("bodies", [])]
        return sum(vols) if vols and all(v is not None for v in vols) else None


def chamfer_sides_by_final_shape(conv, final, a, f3d, idx, out, td, infer):
    """cadmpeg does not decode which face a distance-angle or two-distance chamfer measures its
    distance along (its DR-341). When the final shape does not match Fusion's, flip one such
    chamfer at a time and keep the first flip that makes it match."""
    sided = [k for k, c in enumerate(conv.cmds) if c["command"] == "solid.chamfer"
             and ("angle" in c["params"] or "distance2" in c["params"])][:4]
    for k in sided:
        p = conv.cmds[k]["params"]
        p["flip"] = not p.get("flip", False)
        res = final_shape_check(conv, a.check, a.decoder, f3d, idx, out, td)
        if res.get("status", "").startswith("match"):
            infer.use("chamfer_side_by_final_shape", p.get("name", "?"), "distance along the other face")
            conv.notes.append(f"{p.get('name', '?')}: chamfer distance along the other face than SolidCraft's default: "
                              "the side that makes the final shape match Fusion's")
            return res
        p["flip"] = not p["flip"]
        if not p["flip"]:
            del p["flip"]
    return final


def final_shape_check(conv, sc_cli, decoder, f3d, body_indices, out, td):
    """Compare the whole script's bodies with the f3d's final bodies (written as STEP by the
    decoder): body count; per body, paired by centre of mass, volume, bounding box and face
    count. Catches what the per-feature box-centre check cannot see (a thread on the wrong
    face, a missing chamfer, a hole that is too small). A reference body cadmpeg could not
    write completely (procedural surfaces such as modeled threads) is reported, not judged."""
    res = {"bodies": [], "status": "not run"}
    if not body_indices:
        res.update(status="no final bodies", summary="no final bodies to compare")
        return res
    ref = os.path.join(td, "final-reference.step")
    r = subprocess.run([decoder, f3d, os.path.join(td, "final.ir.json"), ref, ",".join(str(i) for i in body_indices)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        res.update(status="reference not written", summary="reference STEP not written: " + r.stderr.strip()[:200])
        return res
    thin_step(ref)
    incomplete = any("surface" in l_.lower() and ("not written" in l_ or "not represented" in l_ or "omitted" in l_)
                     for l_ in r.stderr.splitlines())
    script = os.path.join(td, "final-script.json")
    write_script(script, conv.cmds)

    def ev(path, cwd=None):
        q = subprocess.run([sc_cli, "eval", path], capture_output=True, text=True, cwd=cwd)
        if q.returncode != 0:
            return None, q.stderr.strip()[:300]
        return json.loads(q.stdout), None

    A, err = ev(script, os.path.dirname(os.path.abspath(out)))
    if A is None:
        res.update(status="script failed", summary="the script does not run to the end: " + err)
        return res
    refscript = os.path.join(td, "final-reference.json")
    write_script(refscript, [{"command": "file.insert_step", "params": {"path": ref}}])
    B, err = ev(refscript)
    if B is None:
        res.update(status="reference failed", summary="SolidCraft could not load the reference STEP: " + err)
        return res
    a_, b_ = [b for b in A.get("bodies", []) if b["name"] not in conv.hidden], list(B.get("bodies", []))
    res["count"] = [len(a_), len(b_)]
    def centre(b):
        if b.get("center_of_mass"):
            return b["center_of_mass"]
        return [(lo + hi) / 2 for lo, hi in zip(b["bbox"]["min"], b["bbox"]["max"])]

    pairs, left = [], list(a_)
    for y in b_:
        if not left:
            break
        x = min(left, key=lambda x: math.dist(centre(x), centre(y)))
        left.remove(x)
        pairs.append((x, y))
    worst = 0.0
    for x, y in pairs:
        if x.get("volume_mm3") is None or y.get("volume_mm3") is None:
            # SolidCraft gives no volume for a body that is not closed (a reference body whose
            # fitted thread surfaces do not close up): compare the box and faces only
            db = max(abs(p_ - q_) for p_, q_ in zip(x["bbox"]["min"] + x["bbox"]["max"], y["bbox"]["min"] + y["bbox"]["max"]))
            res["bodies"].append({"body": x["name"], "volume": [x.get("volume_mm3"), y.get("volume_mm3")],
                                  "bbox_mm": float(f"{db:.3e}"), "faces": [x["faces"], y["faces"]], "match": None,
                                  "note": "no volume for one side (not a closed solid); not judged"})
            continue
        dv = (x["volume_mm3"] - y["volume_mm3"]) / max(abs(y["volume_mm3"]), 1e-9)
        db = max(abs(p_ - q_) for p_, q_ in zip(x["bbox"]["min"] + x["bbox"]["max"], y["bbox"]["min"] + y["bbox"]["max"]))
        good = abs(dv) < 1e-4 and db < 1e-3
        worst = max(worst, abs(dv))
        entry = {"body": x["name"], "volume": [round(x["volume_mm3"], 4), round(y["volume_mm3"], 4)],
                 "rel_volume": float(f"{dv:.3e}"), "bbox_mm": float(f"{db:.3e}"),
                 "faces": [x["faces"], y["faces"]], "match": good}
        if not good and incomplete and db < 1e-3:
            # the reference lacks procedural surfaces (cadmpeg's fitted threads): its volume is
            # not to be trusted, the box is; report the difference, do not judge it
            entry.update(match=None, note=f"volume differs by {dv:.1e} from a reference that lacks some "
                                          "procedural surfaces; not judged")
        res["bodies"].append(entry)
    nbad = sum(b["match"] is False for b in res["bodies"])
    nun = sum(b["match"] is None for b in res["bodies"])
    if len(a_) != len(b_):
        res["status"] = "body count differs"
    elif nbad:
        res["status"] = "mismatch"
    else:
        res["status"] = "match"
    if incomplete:
        res["reference_incomplete"] = True
    if nun and not nbad and res["status"] == "match":
        res["status"] = "match (some not judged)"
    res["summary"] = (f"{res['status']}: {len(a_)} bodies vs {len(b_)} in Fusion, {len(pairs) - nbad - nun}/{len(pairs)} match, "
                      f"{nun} not judged "
                      f"(volume within 1e-4, box within 1 um; worst volume {worst:.1e})"
                      + ("; the reference lacks some procedural surfaces" if incomplete else ""))
    return res


def is_identity(T):
    return all(abs(T[i][j] - (1.0 if i == j else 0.0)) < 1e-12 for i in range(3) for j in range(3)) \
        and all(abs(T[i][3]) < 1e-12 for i in range(3))


def compose(A, B):
    """A then... the 3x4 rigid transform A·B (B applied first)."""
    R = [[sum(A[i][k] * B[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
    t = [sum(A[i][k] * B[k][3] for k in range(3)) + A[i][3] for i in range(3)]
    return [R[i] + [t[i]] for i in range(3)]


def screw_library_json(bulk: bytes, offset: int) -> dict | None:
    """The library data a Screw scope stores after its header: one JSON object."""
    i = bulk.find(b'{"Filename"', offset, offset + 4096)
    if i < 0:
        return None
    depth, instr, esc = 0, False, False
    for j in range(i, min(len(bulk), i + (1 << 20))):
        c = bulk[j]
        if instr:
            if esc:
                esc = False
            elif c == 0x5C:
                esc = True
            elif c == 0x22:
                instr = False
            continue
        if c == 0x22:
            instr = True
        elif c == 0x7B:
            depth += 1
        elif c == 0x7D:
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(bulk[i:j + 1].decode("utf-8"))
                except ValueError:
                    return None
    return None


def screw_occurrences(ir, names, infer):
    """Occurrences for Screw (fastener) features that cadmpeg leaves without one (inference
    screw_occurrences_from_component_paths). Changes ir["model"]["occurrences"]; returns notes."""
    m = ir["model"]
    nat = (ir.get("native") or {}).get("f3d") or {}
    screws = [f for f in m.get("features", []) if f.get("source_tag") == "Screw" and not f.get("suppressed")]
    if not screws and not any(f.get("source_tag") == "Component Insert" for f in m.get("features", [])):
        return []
    bulk = open(ir["_bulk_path"], "rb").read() if ir.get("_bulk_path") else None
    if bulk is None:
        return ["Screw features: the design stream is missing, so their screws are not placed"]
    carriers = {}
    for c in nat.get("design_component_paths", []):
        el = c.get("elements") or []
        if len(el) == 1 and el[0].get("discriminator") == 0 and c.get("transform"):
            carriers.setdefault(el[0]["record_index"], []).append(c["transform"])
    shown = {p["path"][-1] for p in nat.get("display_poses", []) if (p.get("binding") or {}).get("state") == "bound"}
    notes = insert_poses(m, carriers, infer)
    by_name = {}
    for path, nm in names.items():
        by_name.setdefault(nm, path)
    added = {}
    if not infer.enabled("screw_occurrences_from_component_paths"):
        screws = []
    # occurrences cadmpeg expanded and placed itself (one per Screw feature in newer files)
    placed: dict = {}
    for o in m.get("occurrences", []):
        doc = (o.get("prototype") or {}).get("document", {}).get("path")
        if doc and "-occurrence-" in (o.get("native_ref") or ""):
            placed[doc] = placed.get(doc, 0) + 1
    for f in screws:
        sp = f.get("source_properties") or {}
        try:
            target = int(sp["reference:0"]) - 1
            off = int(re.search(r"#(\d+)$", f.get("native_ref", "")).group(1))
        except (KeyError, ValueError, AttributeError):
            notes.append(f"{f['name']}: no occurrence record named; its screw is not placed")
            continue
        if any(target - 2 <= t <= target for t in shown):
            continue  # an occurrence cadmpeg placed already shows it (its record lies just before)
        lib = screw_library_json(bulk, off) or {}
        path = by_name.get(lib.get("DNAME"))
        T = carriers.get(target)
        if path is not None and not T and placed.get(path, 0) > 0:
            placed[path] -= 1
            continue  # cadmpeg gives this screw an occurrence of its own
        if path is None or not T or len(T) != 1:
            notes.append(f"{f['name']}: screw {lib.get('DNAME', '?')!r}: " +
                         ("no linked design of that name in the archive" if path is None else
                          "no unique stored pose") + "; not placed")
            continue
        T = T[0]
        X = [[T[i][0], T[i][1], T[i][2], T[i][3] * 10.0] for i in range(3)]  # cm -> mm
        added.setdefault(path, []).append({
            "id": f"f3d2sc:screw-occurrence#{target}", "name": f["name"], "parent": {"kind": "root"},
            "prototype": {"document": {"path": path, "resolution": "path"}, "scope": "external"},
            "transform": X, "ordinal": 1000 + f.get("ordinal", 0), "scale": [1.0, 1.0, 1.0]})
        infer.use("screw_occurrences_from_component_paths", f["name"],
                  f"{lib.get('DNAME')} at {[round(X[i][3], 3) for i in range(3)]}")
    if added:
        I = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0]]
        keep = []
        for o in m.get("occurrences", []):
            doc = (o.get("prototype") or {}).get("document", {}).get("path")
            unplaced = re.fullmatch(r"f3d:xref:reference#\d+", o.get("native_ref") or "") and \
                all(abs(a_ - b_) < 1e-12 for r1, r2 in zip(o.get("transform") or I, I) for a_, b_ in zip(r1, r2))
            if doc in added and unplaced:
                notes.append(f"the unplaced occurrence of {names.get(doc, doc)} is replaced by its Screw features' "
                             f"{len(added[doc])} placed screw(s)")
                continue
            keep.append(o)
        m["occurrences"] = keep + [o for v in added.values() for o in v]
    return notes


def insert_occurrences(ir, names, infer):
    """Component Inserts whose occurrence cadmpeg leaves unbound (no document) while the linked
    designs' own occurrences sit unplaced at the origin (inference
    insert_occurrences_from_display_poses). Pairs them in order, moves each linked occurrence to
    the display pose of its insert record and drops the unbound one. Returns notes."""
    if not infer.enabled("insert_occurrences_from_display_poses"):
        return []
    m = ir["model"]
    nat = (ir.get("native") or {}).get("f3d") or {}
    occs = {o["id"]: o for o in m.get("occurrences", [])}
    I = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0]]
    inserts = []
    for f in sorted(m.get("features", []), key=lambda f: f.get("ordinal", 0)):
        o = occs.get((f.get("definition") or {}).get("occurrence"))
        if f.get("source_tag") == "Component Insert" and not f.get("suppressed") and o is not None \
                and (o.get("prototype") or {}).get("scope") == "unresolved":
            inserts.append((f, o))
    linked = []
    for o in m.get("occurrences", []):
        r = re.fullmatch(r"f3d:xref:reference#(\d+)", o.get("native_ref") or "")
        if r and (o.get("prototype") or {}).get("document", {}).get("path") and \
                all(abs(a_ - b_) < 1e-12 for r1, r2 in zip(o.get("transform") or I, I) for a_, b_ in zip(r1, r2)):
            linked.append((int(r.group(1)), o))
    linked.sort(key=lambda x: x[0])
    if not inserts or not linked:
        return []
    if len(inserts) != len(linked):
        return [f"{len(inserts)} unbound Component Insert(s) and {len(linked)} unplaced linked occurrence(s): "
                "not paired, the linked designs stay at the origin"]
    poses = {}
    for p in nat.get("display_poses", []):
        path, T = p.get("path") or [], p.get("transform")
        if len(path) == 2 and T and (p.get("binding") or {}).get("state") != "bound":
            poses.setdefault(path[-1], []).append(T)
    notes, drop = [], set()
    for (f, o_ins), (_, o_lnk) in zip(inserts, linked):
        try:
            T = poses.get(int((f.get("source_properties") or {})["reference:0"]) - 1)
        except (KeyError, ValueError):
            T = None
        doc = o_lnk["prototype"]["document"]["path"]
        if not T or len(T) != 1:
            notes.append(f"{f['name']}: no unique display pose for its record; {names.get(doc, doc)} stays at the origin")
            continue
        o_lnk["transform"] = [list(T[0][i][:4]) for i in range(3)]
        drop.add(o_ins["id"])
        m.setdefault("_insert_links", {})[f["id"]] = o_lnk["id"]
        notes.append(f"{f['name']}: places {names.get(doc, doc)} at its displayed pose "
                     f"(translation {[round(T[0][i][3], 3) for i in range(3)]} mm)")
        infer.use("insert_occurrences_from_display_poses", f["name"], f"{names.get(doc, doc)}")
    m["occurrences"] = [o for o in m.get("occurrences", []) if o["id"] not in drop]
    return notes


def insert_poses(m, carriers, infer):
    """Component Insert occurrences whose current pose cadmpeg misses: the component-path record
    stored for the insert's occurrence record (inference insert_pose_from_component_path)."""
    if not infer.enabled("insert_pose_from_component_path"):
        return []
    occs = {o["id"]: o for o in m.get("occurrences", [])}
    notes = []
    for f in m.get("features", []):
        if f.get("source_tag") != "Component Insert" or f.get("suppressed"):
            continue
        o = occs.get((f.get("definition") or {}).get("occurrence"))
        try:
            T = carriers.get(int((f.get("source_properties") or {})["reference:0"]) - 1)
        except (KeyError, ValueError):
            continue
        if o is None or not T or len(T) != 1:
            continue
        X = [[T[0][i][0], T[0][i][1], T[0][i][2], T[0][i][3] * 10.0] for i in range(3)]  # cm -> mm
        cur = o.get("transform") or [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0]]
        d = max(abs(a_ - b_) for r1, r2 in zip(cur, X) for a_, b_ in zip(r1, r2))
        if d > 1e-6:
            o["transform"] = X
            notes.append(f"{f['name']}: its occurrence moved to the pose its component-path record stores "
                         f"(cadmpeg's differed by up to {d:.3g})")
            infer.use("insert_pose_from_component_path", f["name"], f"translation {[round(X[i][3], 3) for i in range(3)]}")
    return notes


def assembly_commands(ir, sc_of, label, with_joints=False):
    """Commands placing each occurrence of a design: its component's (or linked design's)
    script built as a .sc, inserted and moved to where Fusion has it (nested occurrences are
    flattened to their placement in the design). sc_of maps ("local", component id) or
    ("doc", archive path) to (.sc file, display name). Returns (commands, notes, complete)."""
    m = ir["model"]
    occs = {o["id"]: o for o in m.get("occurrences", [])}
    I = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0]]

    def world(o, depth=0):
        T = o.get("transform") or I
        par = o.get("parent", {})
        if par.get("kind") == "occurrence" and par.get("occurrence") in occs and depth < 64:
            return compose(world(occs[par["occurrence"]], depth + 1), T)
        return T

    def depth(o):
        d, par = 0, o.get("parent", {})
        while par.get("kind") == "occurrence" and par.get("occurrence") in occs and d < 64:
            d += 1
            par = occs[par["occurrence"]].get("parent", {})
        return d

    def hidden(o, depth=0):
        """Hidden in Fusion (CADIR v11 states it), or inside a hidden occurrence."""
        if o.get("visible") is False:
            return True
        par = o.get("parent", {})
        return par.get("kind") == "occurrence" and par.get("occurrence") in occs and depth < 64 \
            and hidden(occs[par["occurrence"]], depth + 1)

    cmds, notes, ok, used, hide = [], [], True, {}, []
    sc_name = {}
    for o in sorted(occs.values(), key=lambda o: (o.get("ordinal", 0), depth(o))):
        pr = o.get("prototype", {})
        key = ("local", pr.get("definition")) if pr.get("scope") == "local" else ("doc", pr.get("document", {}).get("path"))
        if key not in sc_of:
            if key[0] == "local" and key[1] not in {p["id"] for p in components(ir)}:
                continue  # a component with nothing of its own (the root, a grouping component)
            notes.append(f"occurrence of {key[1]}: that design or component was not converted")
            ok = False
            continue
        if any(abs(v - 1) > 1e-12 for v in o.get("scale", [1, 1, 1])):
            notes.append(f"occurrence of {sc_of[key][1]} is scaled: not mapped")
            ok = False
            continue
        path, disp = sc_of[key]
        used[disp] = used.get(disp, 0) + 1
        name = disp if used[disp] == 1 else f"{disp} ({used[disp]})"
        cmds.append({"command": "component.insert_design", "params": {"path": path, "name": name}})
        sc_name[o["id"]] = f"{name}:1"
        if hidden(o):
            hide.append(name)
        T = world(o)
        if not is_identity(T):
            cmds.append({"command": "occurrence.move", "params": {"occurrence": f"{name}:1", "to_matrix": [rnd(r) for r in T],
                                                                  "move_grounded": True}})
    if with_joints and m.get("assembly_joints"):
        jc, _ = joints.joint_commands(m, occs, sc_name, world, notes)
        cmds += jc
    if hide:
        cmds.append({"command": "browser.visibility", "params": {"visible": False, "features": hide}})
        notes.append(f"{len(hide)} occurrence(s) hidden in Fusion are hidden: {', '.join(hide)}")
    if any("placed at the identity without proof" in l_["message"] for l_ in ir.get("decode_losses", [])):
        notes.append("cadmpeg could not decode the occurrences' placements and put them at the origin; the f3d2sc "
                     "checks found no placement records either (parts modelled in place fit that). Check the result.")
    return cmds, notes, ok


def convert_file(a, f3d, outdir, base, td, step=None, ir_path=None, label="design", diags=None):
    """One design file: a part script, or, when it holds local components, a script per
    component. Returns (sc_of entries for its components, reports, failed, ir)."""
    try:
        ir, bulk = load_ir(ir_path) if ir_path else decode(f3d, a.decoder, td, step)
    except DecodeError as e:
        print(f"f3d2sc: {base}: decoding failed: {e}", file=sys.stderr)
        if diags is not None:
            diags.append({"design": label, "decode_error": diag.Redactor().text(e)})
        return {}, {}, True, None
    reassign_owners(ir, a.infer)
    comps = components(ir)
    split = len(comps) > 1
    sc_of, reports, failed = {}, {}, False
    for k, pd in enumerate(comps if split else [None]):
        nm = base if not split else f"{base}.{safe_name(pd.get('source_name') or f'component {k + 1}')}"
        out = os.path.join(outdir, nm + ".solidcraft.json")
        conv, gaps, problems, rep, ir_ = convert_design(a, f3d or ir_path, out, td, f3d=f3d, pre=(ir, bulk), component=pd)
        lab = label if not split else f"{label}/component#{k + 1}"
        if diags is not None and a.diagnose:
            diags.append(diag.design_report(conv, ir_, lab, diag.Redactor()))
        print_outcome(nm, conv, gaps, problems)
        reports[nm] = rep
        if gaps and not a.partial:
            failed = True
            continue
        write_script(out, conv.cmds)
        if a.build and not a.check:
            warn_lost_bodies(a.build, conv, out)
        print(f"f3d2sc: wrote {out} ({len(conv.cmds)} commands)" + (f", stopped at {gaps[0]['feature']}" if gaps else "")
              + (f"; {len(problems)} check problem(s)" if problems else ""), file=sys.stderr)
        failed |= bool(gaps or problems)
        if pd is not None:
            sc_of[("local", pd["id"])] = (nm + ".sc", pd.get("source_name") or nm)
        else:
            sc_of[None] = (nm + ".sc", base)
    return sc_of, reports, failed, ir


def warn_lost_bodies(sc_cli, conv, out):
    """Without --check nothing replays the features, so a body SolidCraft loses silently
    (SolidCraft issue 3) goes unnoticed: evaluate the whole script once and name every body of
    Fusion's final model it lacks."""
    want = []
    for ns in conv.ns:
        gone = set(conv.removed.get(ns.index, []))
        for s_ in conv.final_state.get(ns.index, {}):
            nm = ns.names.get(s_)
            if s_ not in gone and nm in conv.bodies and nm not in conv.hidden:
                want.append(nm)
    if not want:
        return
    r = subprocess.run([sc_cli, "eval", os.path.abspath(out)], capture_output=True, text=True,
                       cwd=os.path.dirname(os.path.abspath(out)))
    if r.returncode != 0:
        return  # the build reports the failure
    have = {b["name"] for b in json.loads(r.stdout).get("bodies", [])}
    lost = [nm for nm in want if nm not in have]
    if lost:
        print(f"build: WARNING: {os.path.basename(out)} ends without {', '.join(lost)}, which Fusion's final model "
              "has: SolidCraft lost it in a feature (issue 3). Run with --check to find that feature and leave it "
              "out.", file=sys.stderr)


def print_outcome(name, conv, gaps, problems):
    for n, k in grouped(conv.notes):
        print(f"note: {name}: {n}" + (f" (x{k})" if k > 1 else ""), file=sys.stderr)
    for g in gaps:
        print(f"gap: {name}: {g['feature']} [{g['kind']}]: {g['reason']}", file=sys.stderr)


def write_diagnosis(path, designs, extra=None):
    rep = {"tool": "f3d2sc --diagnose", "redaction": "names, geometry and values removed; numbers in text are <x>",
           "solidcraft_cli": None, "designs": designs}
    if extra:
        rep.update(extra)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(rep, fh, indent=1)
    print(f"f3d2sc: wrote the redacted report {path}; read it through before sharing", file=sys.stderr)


def safe_name(s):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_") or "design"


def convert_f3z(a, td):
    """An .f3z: a root design and the designs it links. Each linked design becomes its own script
    (built into a .sc design), and the root a script that inserts them as linked components."""
    import zipfile
    outdir = a.out or re.sub(r"\.f3z$", "", a.f3d) + ".solidcraft"
    os.makedirs(outdir, exist_ok=True)
    x = os.path.join(td, "f3z")
    with zipfile.ZipFile(a.f3d) as z:
        for info in z.infolist():
            name = info.filename
            if name.startswith("/") or ".." in name.split("/"):
                continue
            z.extract(info, x)
    root_file = json.load(open(os.path.join(x, "Manifest.json")))["root"]
    names = {}
    try:
        desc = json.load(open(os.path.join(x, "DesignDescription.json")))["designDescription"]
        for g in desc.get("designGraphs", []):
            for o in g.get("designObjects", []):
                names[o["relativePath"]] = o.get("friendlyName") or o["relativePath"]
    except (OSError, KeyError, ValueError):
        pass
    reports, failed = {}, False
    parts = sorted(f for f in os.listdir(x) if f.endswith(".f3d") and f != root_file)
    diags = []
    labels = {f: f"design#{k + 1}" for k, f in enumerate(parts)}
    sc_of, built = {}, []
    for f in parts:
        nm = safe_name(names.get(f, re.sub(r"(\.zip)?\.f3d$", "", f)))
        got, reps, bad, ir_ = convert_file(a, os.path.join(x, f), outdir, nm, td, step=os.path.join(outdir, nm + ".reference.step"),
                                           label=labels[f], diags=diags)
        reports.update(reps)
        failed |= bad
        if None in got:
            sc_of[("doc", f)] = got[None]
            built.append(got[None][0][:-3])
        elif got:
            # a linked design with components of its own: its own assembly script, built as a .sc
            sub_cmds, sub_notes, ok = assembly_commands(ir_, got, nm, with_joints=bool(a.build) and not getattr(a, "no_joints", False))
            write_script(os.path.join(outdir, nm + ".solidcraft.json"), sub_cmds)
            sc_of[("doc", f)] = (nm + ".sc", names.get(f, nm))
            built += [v[0][:-3] for v in got.values()] + [nm]
            failed |= not ok
    # The root: its components (or its own features), then one placed component per occurrence.
    rname = safe_name(names.get(root_file, "root"))
    rgot, reps, bad, rir = convert_file(a, os.path.join(x, root_file), outdir, rname + ".root", td, label="root", diags=diags)
    reports.update(reps)
    failed |= bad
    notes = []
    if rir is None:
        sys.exit(f"f3d2sc: decoding the root failed")
    m = rir["model"]
    cmds = []
    if None in rgot:
        # the root's own features, built as a .sc and inserted like the rest; a root with no solid
        # body of its own (only the occurrences) is not inserted: SolidCraft links no empty design
        if any(b.get("kind") == "solid" for b in m.get("bodies", [])):
            cmds.append({"command": "component.insert_design", "params": {"path": rgot[None][0], "name": rname}})
        built.append(rgot[None][0][:-3])
    else:
        built += [v[0][:-3] for v in rgot.values()]
    notes += screw_occurrences(rir, names, a.infer.for_design("root"))
    notes += insert_occurrences(rir, names, a.infer.for_design("root"))
    acmds, anotes, ok = assembly_commands(rir, {**sc_of, **{k: v for k, v in rgot.items() if k is not None}}, rname,
                                          with_joints=bool(a.build) and not getattr(a, "no_joints", False))
    cmds += acmds
    notes += anotes
    failed |= not ok
    rout = os.path.join(outdir, rname + ".solidcraft.json")
    write_script(rout, cmds)
    for n in notes:
        print(f"note: {rname}: {n}", file=sys.stderr)
    print(f"f3d2sc: wrote {rout} ({len(cmds)} commands, {len(sc_of) + len(rgot)} designs/components)", file=sys.stderr)
    reports[rname] = {"source": root_file, "commands": len(cmds), "notes": notes}
    unproven = any("placed at the identity without proof" in l_["message"] for l_ in rir.get("decode_losses", []))
    if a.diagnose:
        occ = []
        for o in sorted(m.get("occurrences", []), key=lambda o: o.get("ordinal", 0)):
            T = o.get("transform") or [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0]]
            turned = any(abs(T[i][j] - (1.0 if i == j else 0.0)) > 1e-12 for i in range(3) for j in range(3))
            moved = any(abs(T[i][3]) > 1e-12 for i in range(3))
            doc = o.get("prototype", {}).get("document", {}).get("path")
            occ.append({"of": labels.get(doc, "<not in archive>"), "parent": o.get("parent", {}).get("kind"),
                        "placement": "turned" if turned else ("moved" if moved else "origin"),
                        "scope": o.get("prototype", {}).get("scope")})
        red = diag.Redactor()
        for f_, lab in labels.items():
            red.add(names.get(f_), lab)
        root_types: dict = {}
        for f_ in m.get("features", []):
            root_types[f_.get("source_tag")] = root_types.get(f_.get("source_tag"), 0) + 1
        write_diagnosis(a.diagnose, diags, {"assembly": {
            "designs_in_archive": len(parts) + 1, "occurrences": occ, "root_feature_types": root_types,
            "placements_unproven": unproven, "notes": [red.text(n) for n in notes]}})
    if a.report:
        with open(a.report, "w") as fh:
            json.dump(reports, fh, indent=1)
    if a.build:
        build(a.build, outdir, list(dict.fromkeys(built)), rname)
    sys.exit(2 if failed else 0)


def check_decoder(decoder):
    """Warn when the decoder is not built from the cadmpeg checkout beside this repository
    (../cadmpeg): an older build decodes less (unresolved planes and placements), and the
    conversion silently loses it."""
    here = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cadmpeg")
    if not os.path.isfile(decoder):
        sys.exit(f"f3d2sc: no decoder at {decoder}. Build it once (README, Set-up, steps 3-5):\n"
                 "  (cd decoder && cargo build --release) && mkdir -p bin && cp decoder/target/release/f3d2sc-decode bin/")
    try:
        v = subprocess.run([decoder, "--version"], capture_output=True, text=True).stdout.split()
    except OSError as e:
        sys.exit(f"f3d2sc: the decoder {decoder} does not run ({e}); rebuild it (README, Set-up)")
    built = v[2] if len(v) == 3 and v[1] == "cadmpeg" else None
    r = subprocess.run(["git", "-C", here, "rev-parse", "HEAD"], capture_output=True, text=True) \
        if os.path.isdir(os.path.join(here, ".git")) else None
    head = r.stdout.strip() if r is not None and r.returncode == 0 else None
    if built is None:
        print(f"f3d2sc: WARNING: {decoder} predates the version stamp, so it is older than the cadmpeg branch "
              "f3d2sc this converter expects. Update ../cadmpeg and rebuild the decoder (README, Set-up).", file=sys.stderr)
    elif head and built != head:
        print(f"f3d2sc: WARNING: {decoder} is built from cadmpeg {built[:10]}, but ../cadmpeg is at {head[:10]}. "
              "Rebuild the decoder (README, Set-up).", file=sys.stderr)


def convert_components_if_any(a, out, td):
    """A single .f3d whose features sit in several local components: a script per component
    and `out` as the script placing them (like an .f3z's root). Exits when it did so; returns
    when the design has one component (the caller converts it whole)."""
    try:
        ir, _ = decode(a.f3d, a.decoder, td, None)
    except DecodeError:
        return  # the caller reports it
    reassign_owners(ir, a.infer)
    if len(components(ir)) < 2:
        return
    outdir = os.path.dirname(os.path.abspath(out))
    base = re.sub(r"\.solidcraft\.json$|\.json$", "", os.path.basename(out))
    out = os.path.join(outdir, base + ".solidcraft.json")  # the name build() runs
    got, reports, failed, ir_ = convert_file(a, a.f3d, outdir, base, td, step=a.step)
    cmds, notes, ok = assembly_commands(ir_, got, base, with_joints=bool(a.build) and not getattr(a, "no_joints", False))
    write_script(out, cmds)
    for n in notes:
        print(f"note: {base}: {n}", file=sys.stderr)
    print(f"f3d2sc: wrote {out} ({len(cmds)} commands placing {len(got)} components)", file=sys.stderr)
    reports[base] = {"source": os.path.basename(a.f3d), "commands": len(cmds), "notes": notes}
    if a.report:
        os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
        with open(a.report, "w") as fh:
            json.dump(reports, fh, indent=1)
    if a.build:
        build(a.build, outdir, [v[0][:-3] for v in got.values()], base)
    sys.exit(2 if failed or not ok else 0)


def drop_refused_joints(sc_cli, outdir, script, root, tries=40):
    """The root script without the joint steps SolidCraft refuses or reports conflicts for
    (written as <root>.joints-checked.solidcraft.json when any is dropped); `script` as it is
    when it has no joints or fails for another reason."""
    with open(os.path.join(outdir, script)) as fh:
        cmds = json.load(fh)["commands"]
    if not any(c["command"] == "joint.as_built" for c in cmds):
        return script
    dropped = []
    for _ in range(tries):
        with tempfile.TemporaryDirectory() as td:
            sp, out = os.path.join(td, "root.json"), os.path.join(td, "out.json")
            write_script(sp, cmds)
            subprocess.run([sc_cli, "run", sp, "--results", "--json-out", out, "--quiet"],
                           cwd=outdir, capture_output=True, text=True)
            try:
                with open(out) as fh:
                    r = json.load(fh)
            except (OSError, ValueError):
                return script
        bad = None
        if isinstance(r, dict) and "error" in r:
            k = (r["error"].get("step") or 0) - 1
            if 0 <= k < len(cmds) and cmds[k]["command"] in ("joint.as_built", "occurrence.ground"):
                bad = k
            else:
                return script  # fails for another reason: build() reports it
        else:
            for k, (c, res) in enumerate(zip(cmds, r)):
                if c["command"] == "joint.as_built" and isinstance(res, dict) and (res.get("conflicts") or res.get("errors")):
                    bad = k
                    break
        if bad is None:
            break
        dropped.append(f"{cmds[bad]['command']} {cmds[bad]['params'].get('name') or cmds[bad]['params'].get('occurrence')}")
        del cmds[bad]
    if not dropped:
        return script
    print(f"build: {root}: left out {len(dropped)} joint step(s) SolidCraft refused or found in conflict: "
          f"{', '.join(dropped)}", file=sys.stderr)
    checked = root + ".joints-checked.solidcraft.json"
    write_script(os.path.join(outdir, checked), cmds)
    return checked


def build(sc_cli, outdir, parts, root):
    """Run each part script into its .sc design (from the output folder, where the scripts'
    relative paths point), then evaluate the root assembly."""
    failed = set()
    for p in parts:
        r = subprocess.run([sc_cli, "run", p + ".solidcraft.json", "--save", p + ".sc", "--quiet"], cwd=outdir, capture_output=True, text=True)
        print(f"build: {p}.sc " + ("ok" if r.returncode == 0 else "FAILED: " + r.stderr.strip()[:300]), file=sys.stderr)
        if r.returncode != 0:
            failed.add(p + ".sc")
    script = root + ".solidcraft.json"
    if failed:
        # the assembly without the parts that did not build, rather than no assembly at all
        with open(os.path.join(outdir, script)) as fh:
            cmds = json.load(fh)["commands"]
        gone = {c["params"]["name"] for c in cmds if c["command"] == "component.insert_design" and c["params"].get("path") in failed}
        cmds = [c for c in cmds if not (c["command"] == "component.insert_design" and c["params"].get("name") in gone)
                and not (c["command"] == "occurrence.move" and c["params"].get("occurrence", "").rsplit(":", 1)[0] in gone)]
        script = root + ".without-failed.solidcraft.json"
        write_script(os.path.join(outdir, script), cmds)
        print(f"build: {root}: WARNING: built without {', '.join(sorted(gone))} (their .sc failed to build)", file=sys.stderr)
    script = drop_refused_joints(sc_cli, outdir, script, root)
    r = subprocess.run([sc_cli, "run", script, "--save", root + ".sc", "--quiet"], cwd=outdir,
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"build: {root}: FAILED: {r.stderr.strip()[:300]}", file=sys.stderr)
        return
    r = subprocess.run([sc_cli, "eval", script], cwd=outdir, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"build: {root}: FAILED: {r.stderr.strip()[:300]}", file=sys.stderr)
        return
    d = json.loads(r.stdout)
    vol = sum(b.get("volume_mm3") or 0 for b in d.get("bodies", []))
    print(f"build: {root}: {len(d.get('bodies', []))} bodies, {vol:.3f} mm3 in all", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("f3d", nargs="?")
    ap.add_argument("--ir", help="use an IR JSON dumped by f3d2sc-decode instead of decoding")
    ap.add_argument("-o", "--out", help="script to write (default: <input>.solidcraft.json)")
    ap.add_argument("--step", help="also write the f3d's final B-rep as STEP (reference)")
    ap.add_argument("--report", help="write the gap report as JSON")
    ap.add_argument("--check", metavar="SOLIDCRAFT_CLI", help="replay with solidcraft-cli and compare each feature with Fusion's body centres")
    ap.add_argument("--decoder", default=os.environ.get("F3D2SC_DECODER") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin", "f3d2sc-decode"))
    ap.add_argument("--partial", action="store_true", help="write the script up to the first gap")
    ap.add_argument("--source", help="with --ir: the .f3d it came from (derived bodies are written from it as STEP)")
    ap.add_argument("--until", metavar="FEATURE", help="stop before this Fusion feature (by its timeline name)")
    ap.add_argument("--spline-tol", type=float, default=SPLINE_TOL, help=f"mm a refitted dense spline may deviate (default {SPLINE_TOL})")
    ap.add_argument("--no-joints", action="store_true",
                    help="leave out assembly joints (made as built with --build)")
    ap.add_argument("--no-constraints", action="store_true",
                    help="leave out sketch constraints, dimensions and user parameters (carried over with --check or --build)")
    ap.add_argument("--no-refit", action="store_true", help="keep dense splines as they are (SolidCraft may refuse to extrude them)")
    ap.add_argument("--diagnose", metavar="REPORT", help="write a redacted report (no geometry, names or values) to share")
    ap.add_argument("--solidcraft", metavar="VERSION",
                    help="the SolidCraft version the scripts are for, e.g. 0.49.1 (default: the --check/--build CLI's "
                         f"version, else {'.'.join(map(str, SC_BASELINE))}); newer versions get exact splines and no workaround for fixed SolidCraft issues")
    ap.add_argument("--build", metavar="SOLIDCRAFT_CLI", help="with an .f3z: build each linked design into a .sc and evaluate the assembly")
    ap.add_argument("--overrides", metavar="JSON", help="explicit answers for what the file leaves open (see inference.py)")
    ap.add_argument("--no-infer", metavar="NAME", action="append", default=[],
                    help="switch off an inference (repeatable); --list-inference names them")
    ap.add_argument("--list-inference", action="store_true", help="list the inferences the converter can make, and exit")
    a = ap.parse_args()
    if a.list_inference:
        for k, v in HEURISTICS.items():
            print(f"{k}\n    {v}")
        sys.exit(0)
    a.infer = Inference.from_args(a.overrides, a.no_infer)
    a.sc_target = solidcraft_target(a)
    if a.build is None and a.check:
        a.build = a.check  # SolidCraft is at hand: also build each script into a .sc the app can open
    if not a.f3d and not a.ir:
        ap.error("give an .f3d or .f3z file, or --ir")
    if a.f3d and not a.ir:
        check_decoder(a.decoder)
    with tempfile.TemporaryDirectory() as td:
        if a.f3d and a.f3d.lower().endswith(".f3z"):
            convert_f3z(a, td)
        src = a.f3d or a.ir
        out = a.out or re.sub(r"(\.ir)?\.(json|f3d)$", "", src) + ".solidcraft.json"
        if a.f3d and not a.ir and not a.until:
            convert_components_if_any(a, out, td)
        try:
            conv, gaps, problems, report, ir = convert_design(a, src, out, td, ir_path=a.ir, f3d=a.f3d or a.source, step=a.step)
        except DecodeError as e:
            if a.diagnose:
                write_diagnosis(a.diagnose, [{"design": "design#1", "decode_error": diag.Redactor().text(e)}])
            sys.exit(f"f3d2sc: decoding failed: {e}")
        if a.diagnose:
            write_diagnosis(a.diagnose, [diag.design_report(conv, ir, "design#1", diag.Redactor())])
    if a.report:
        os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
        with open(a.report, "w") as fh:
            json.dump(report, fh, indent=1)
    print_outcome(os.path.basename(src), conv, gaps, problems)
    if gaps and not a.partial:
        print(f"f3d2sc: {len(gaps)} feature(s) not converted; nothing written (use --partial)", file=sys.stderr)
        sys.exit(2)
    write_script(out, conv.cmds)
    if a.build and not a.check:
        warn_lost_bodies(a.build, conv, out)
    print(f"f3d2sc: wrote {out} ({len(conv.cmds)} commands)" + (f", stopped at {gaps[0]['feature']}" if gaps else "")
          + (f"; {len(problems)} check problem(s)" if problems else ""), file=sys.stderr)
    if a.build:
        sc_path = re.sub(r"\.solidcraft\.json$|\.json$", "", out) + ".sc"
        r = subprocess.run([a.build, "run", os.path.abspath(out), "--save", os.path.abspath(sc_path), "--quiet"],
                           cwd=os.path.dirname(os.path.abspath(out)), capture_output=True, text=True)
        print(f"build: {sc_path} " + ("ok" if r.returncode == 0 else "FAILED: " + r.stderr.strip()[:300]), file=sys.stderr)
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
