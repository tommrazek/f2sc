"""Sketch constraints, dimensions and user parameters, carried over from Fusion.

cadmpeg decodes each sketch's constraints (`sketch_constraints`: horizontal, coincident,
symmetric, distance, diameter and so on), naming the sketch entities they tie together and,
for a dimension, the parameter that drives it. It also decodes the document's user
parameters, with their expressions. This module turns them into SolidCraft commands:

* user parameters -> `parameters.add`, at the start of the script;
* constraints -> `sketch.constraint.*`;
* dimensions -> `sketch.dimension`, valued by the Fusion expression when it names only user
  parameters, otherwise by the parameter's value.

A constraint is only worth having if the sketch stays where Fusion drew it. Each sketch is
therefore tried on its own with `solidcraft-cli` (a probe of a few milliseconds): first as
drawn, to learn SolidCraft's point ids and positions, then with the constraints. A constraint
SolidCraft refuses (a conflict) is dropped; when the solved sketch has moved, the constraint
that moved it is found by adding them one by one, dropped, and the rest tried again. What is
dropped is counted in the notes. Without a SolidCraft CLI nothing is added, since nothing
could be checked.
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import tempfile

#: A point is this close (mm) to a SolidCraft point to be that point.
SAME_POINT = 1e-5
#: The solved sketch may move this far (mm) and still count as unmoved.
MOVED = 1e-4
#: Most probe runs spent on one sketch before its constraints are given up.
MAX_PROBES = 80

#: Fusion constraints between two curves.
PAIRED = {"parallel": "sketch.constraint.parallel", "perpendicular": "sketch.constraint.perpendicular",
          "tangent": "sketch.constraint.tangent", "equal": "sketch.constraint.equal",
          "concentric": "sketch.constraint.concentric", "collinear": "sketch.constraint.collinear"}
#: Fusion dimension kinds and SolidCraft's dimension types.
DIMENSIONS = {"distance": "distance", "horizontal_distance": "horizontal", "vertical_distance": "vertical",
              "diameter": "diameter", "radius": "radius", "angle": "angle", "length": "length"}

_IDENT = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")
_UNITS = {"mm", "cm", "m", "in", "ft", "deg", "rad"}


def _is_user(p) -> bool:
    """A user parameter: owned by the document's parameter table, or (decoded from an .f3d of
    an assembly) owned by nothing and made by no feature."""
    own = p.get("owner")
    if own:
        return "document-parameters" in str(own)
    props = p.get("properties") or {}
    return "source_kind" not in props and "owner_record_index" not in props


def user_parameters(model) -> list[dict]:
    """The document's user parameters, each after those its expression names."""
    ps = [p for p in model.get("parameters", []) if _is_user(p)]
    by_name = {p["name"]: p for p in ps}
    done, out = set(), []

    def visit(p, depth=0):
        if p["name"] in done or depth > len(ps):
            return
        for n in _IDENT.findall(p.get("expression") or ""):
            if n in by_name and n != p["name"]:
                visit(by_name[n], depth + 1)
        done.add(p["name"])
        out.append(p)
    for p in ps:
        visit(p)
    return out


def value_text(p) -> str | None:
    """A parameter's value as SolidCraft takes it: mm, degrees or a plain number."""
    v = p.get("value") or {}
    x = v.get("value")
    if not isinstance(x, (int, float)):
        return None
    if v.get("kind") == "length":
        return f"{x:.10g} mm"
    if v.get("kind") == "angle":
        return f"{math.degrees(x):.10g} deg"
    return f"{x:.10g}"


def expression_text(p, user: set[str]) -> str:
    """The Fusion expression when SolidCraft can take it as it is (arithmetic over user
    parameters and numbers with units), else the value."""
    e = (p.get("expression") or "").strip()
    names = set(_IDENT.findall(e)) - _UNITS
    if e and names and names <= user and re.fullmatch(r"[\w\s.+\-*/()]+", e):
        return e
    return value_text(p)


def parameter_commands(model) -> list[dict]:
    """`parameters.add` for each user parameter: its expression when SolidCraft can take it
    (arithmetic over parameters defined before it), else its value."""
    cmds, defined = [], set()
    for p in user_parameters(model):
        expr = expression_text(p, defined) if _IDENT.search(p.get("expression") or "") else None
        names = set(_IDENT.findall(p.get("expression") or "")) - _UNITS
        if not names:
            expr = (p.get("expression") or "").strip() or value_text(p)
        if expr is None:
            continue
        cmds.append({"command": "parameters.add", "params": {"name": p["name"], "expression": expr}})
        defined.add(p["name"])
    return cmds


class SketchProbe:
    """Runs a sketch on its own in SolidCraft, to read it back or to try constraints on it."""

    def __init__(self, sc_cli: str, head: list[dict]):
        self.sc_cli, self.head, self.runs = sc_cli, head, 0
        self.cache: dict[str, tuple] = {}

    def run(self, cmds: list[dict]):
        """(results or None, failing step index into `cmds` or None, error text)."""
        key = json.dumps(cmds, sort_keys=True)
        if key in self.cache:
            return self.cache[key]
        self.runs += 1
        script = self.head + cmds
        with tempfile.TemporaryDirectory() as td:
            sp, out = os.path.join(td, "probe.json"), os.path.join(td, "out.json")
            with open(sp, "w") as fh:
                json.dump(script, fh)
            subprocess.run([self.sc_cli, "run", sp, "--results", "--json-out", out, "--quiet"],
                           capture_output=True, text=True, timeout=120)
            try:
                with open(out) as fh:
                    r = json.load(fh)
            except (OSError, ValueError):
                res = (None, None, "no result")
                self.cache[key] = res
                return res
        if isinstance(r, dict) and "error" in r:
            step = r["error"].get("step")
            k = step - 1 - len(self.head) if isinstance(step, int) else None  # steps count from 1
            res = (None, k, r["error"].get("error", ""))
        else:
            res = (r[len(self.head):], None, "")
        self.cache[key] = res
        return res


def _shape(inspect: dict) -> dict:
    out = {}
    for p in inspect.get("points", []):
        out[p["id"]] = tuple(p["at"])
    for c in inspect.get("curves", []):
        if "radius" in c:
            out[c["id"] + ".r"] = (c["radius"],)
    return out


def _moved(a: dict, b: dict) -> float:
    worst = 0.0
    for k, v in a.items():
        w = b.get(k)
        if w is None:
            continue
        worst = max(worst, math.dist(v, w))
    return worst


def carry(conv, sketch_ref: str, sketch_cmds: list[dict], curve_ids: dict[str, str]) -> tuple[list[dict], str]:
    """The constraint and dimension commands that hold the sketch where it is drawn, and a note.
    `sketch_cmds` run the sketch from `sketch.create` up to (not including) `sketch.finish`."""
    m = conv.m
    recs = [c for c in m.get("sketch_constraints", []) if c.get("sketch") == sketch_ref]
    if not recs:
        return [], ""
    probe = conv.sketch_probe
    base, k, err = probe.run(sketch_cmds + [{"command": "sketch.inspect", "params": {}}])
    if base is None:
        return [], f"constraints not checked: the sketch does not run alone ({err[:120]})"
    shape0 = _shape(base[-1])
    points = [(pid, at) for pid, at in shape0.items() if not pid.endswith(".r")]
    ents = {e["id"]: e for e in conv.entities_by_sketch.get(sketch_ref, [])}
    params = {p["id"]: p for p in m.get("parameters", [])}
    user = {p["name"] for p in user_parameters(m)}

    def point_ref(eid):
        e = ents.get(eid)
        if e is None or e["geometry"]["kind"] != "point":
            return None
        q = e["geometry"]["position"]
        q = (q["u"], q["v"])
        hits = [pid for pid, at in points if math.dist(at, q) < SAME_POINT]
        if not hits:
            return None
        return "origin" if "origin" in hits else hits[0]

    def ref(x):
        """A SolidCraft curve or point id for a constraint operand."""
        if isinstance(x, dict):
            if x.get("kind") not in (None, "entity"):
                return None
            x = x.get("entity") or x.get("value")
        if not isinstance(x, str):
            return None
        return curve_ids.get(x) or point_ref(x)

    cands: list[tuple[dict, str]] = []
    unknown: dict[str, int] = {}
    for rec in recs:
        d = rec.get("definition") or {}
        kind = d.get("kind")
        cmd = None
        if kind in ("horizontal", "vertical"):
            a = ref(d.get("entity"))
            if a and a[0] == "l":
                cmd = {"command": "sketch.constraint.horizontal_vertical", "params": {"line": a, "mode": kind}}
        elif kind in PAIRED:
            a, b = ref(d.get("first")), ref(d.get("second"))
            if a and b:
                cmd = {"command": PAIRED[kind], "params": {"a": a, "b": b}}
        elif kind in ("coincident", "coincident_loci"):
            ops = d.get("entities") or d.get("loci") or []
            rs = [ref(x) for x in ops]
            # an operand that is a curve's end: the end of it nearest the other operand
            for i_, x in enumerate(ops):
                if rs[i_] is None and isinstance(x, dict) and x.get("kind") in ("start", "end") and len(ops) == 2:
                    c = curve_ids.get(x.get("entity") or x.get("value"))
                    other = shape0.get(rs[1 - i_] or "")
                    ends = [(c + s_, shape0[c + s_]) for s_ in (".start", ".end") if c and c + s_ in shape0]
                    if other and ends:
                        rs[i_] = min(ends, key=lambda e_: math.dist(e_[1], other))[0]
            if len(rs) == 2 and all(rs) and shape0.get(rs[0]) and shape0.get(rs[0]) == shape0.get(rs[1]):
                continue  # drawn on the same point, which SolidCraft joins already
            if len(rs) == 2 and all(rs) and rs[0] != rs[1]:
                cmd = {"command": "sketch.constraint.coincident", "params": {"a": rs[0], "b": rs[1]}}
        elif kind == "midpoint":
            a, b = ref(d.get("point") or d.get("first")), ref(d.get("line") or d.get("second"))
            if a and b:
                cmd = {"command": "sketch.constraint.midpoint", "params": {"point": a, "line": b}}
        elif kind == "symmetric":
            a, b, ax = ref(d.get("first")), ref(d.get("second")), ref(d.get("axis"))
            if a and b and ax:
                cmd = {"command": "sketch.constraint.symmetry", "params": {"a": a, "b": b, "line": ax}}
        elif kind in ("fix", "fixed"):
            a = ref(d.get("entity"))
            if a:
                cmd = {"command": "sketch.constraint.fix", "params": {"entity": a, "fixed": True}}
        elif kind in DIMENSIONS:
            p = params.get(d.get("parameter"))
            ops = d.get("entities") or [d.get(k_) for k_ in ("first", "second") if d.get(k_)] or [d.get("entity")]
            rs = [ref(x) for x in ops]
            val = expression_text(p, user) if p else None
            if p and val and rs and all(rs):
                typ = DIMENSIONS[kind]
                if typ == "distance" and len(rs) == 1:
                    typ = "length"
                cmd = {"command": "sketch.dimension", "params": {"entities": rs, "type": typ, "value": val,
                                                                 "if_redundant": "driven"}}
        if cmd is None:
            unknown[kind] = unknown.get(kind, 0) + 1
            continue
        cands.append((cmd, kind))

    kept: list[tuple[dict, str]] = []
    rest = list(cands)
    dropped = 0
    # add the constraints a batch at a time; a refused one is dropped, a batch that moves the
    # sketch is added one by one to find the one that moves it
    start = probe.runs
    while rest and probe.runs - start < MAX_PROBES:
        batch, rest = rest, []
        while batch and probe.runs - start < MAX_PROBES:
            cmds = [c for c, _ in kept] + [c for c, _ in batch]
            res, k, err = probe.run(sketch_cmds + cmds + [{"command": "sketch.inspect", "params": {}}])
            if res is None:
                j = (k - len(sketch_cmds) - len(kept)) if k is not None else None
                if j is None or not 0 <= j < len(batch):
                    return [], f"constraints left out: SolidCraft refused the sketch with them ({err[:120]})"
                kept += batch[:j]
                batch = batch[j + 1:]
                dropped += 1
                continue
            if _moved(shape0, _shape(res[-1])) <= MOVED:
                kept += batch
                batch = []
                continue
            if len(batch) == 1:
                dropped += 1
                batch = []
                continue
            half = len(batch) // 2
            batch, rest = batch[:half], batch[half:] + rest
    note = f"{len(kept)} of {len(recs)} constraints and dimensions carried over"
    if rest or probe.runs - start >= MAX_PROBES:
        note += f"; checking stopped after {MAX_PROBES} probes"
    if dropped:
        note += f"; {dropped} left out (SolidCraft refused them or they moved the sketch)"
    if unknown:
        note += "; not mapped: " + ", ".join(f"{n} {k_}" for k_, n in sorted(unknown.items(), key=str))
    return [c for c, _ in kept], note
