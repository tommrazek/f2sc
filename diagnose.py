"""Redacted diagnostics: what the converter did with a design, without the design.

The report holds structure and failure reasons only:
- features by Fusion type and timeline position (F<n>), with the shape of their definition
  (enum values such as `one_sided` or `cut`, never numbers or names);
- gaps, SolidCraft errors and check results (centre differences in mm, not positions);
- the rules resting on few benchmark cases that fired (`assumptions`);
- for features that could not be converted, the layout of their raw records: class tags, sizes,
  where numbers sit, and Fusion's role words (AlongDistance), never the values.

Names (features, sketches, bodies, designs, parameters, files) become placeholders, GUIDs and
file names are masked, and every decimal number in free text becomes <x>.
"""
from __future__ import annotations

import math
import re
import struct

# Notes the converter raises -> the assumption or loss they stand for.
NOTE_TAGS = [
    (r"refitted with", "spline-refit"),
    (r"inferred from the bodies it makes \(Fusion records no count", "circular-pattern-count-angle-inferred"),
    (r"mirror plane not in its records", "mirror-plane-inferred-from-bodies"),
    (r"found by mirroring centres", "mirror-seeds-found-geometrically"),
    (r"coincident bodies", "coincident-bodies-newest-chosen"),
    (r"read as profiles", "selection-ids-read-as-profile-ids"),
    (r"includes the loop\(s\) nested", "profile-includes-nested-loops"),
    (r"sketch origin moved", "sketch-origin-projected"),
    (r"exist before it with no decoded feature", "derived-bodies-imported"),
    (r"imported from the f3d's final B-rep \(no later", "feature-imported-from-final-brep"),
    (r"the bodies the final model no longer has", "remove-body-from-final-model"),
    (r"of faces that changes no solid", "face-mirror-left-out"),
    (r"thread that changes no body", "thread-mirror-left-out"),
    (r"feature mirror whose copies are new bodies", "feature-mirror-rebuilt-as-body-mirror"),
    (r"construction axis left out", "work-axis-left-out"),
    (r"tolerance class is not carried", "thread-class-dropped"),
    (r"differ from Fusion's approximate", "approximate-centres-differ"),
    (r"suppressed in Fusion", "suppressed-left-out"),
]

GUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
FILES = re.compile(r"[^\s'\"(),;]+\.(?:f3d|f3z|step|stp|smb|smbh|sc|json|dat|protein|png)\b", re.I)
REFS = re.compile(r"f3d:[^\s'\",;)]+")
DECIMAL = re.compile(r"-?\d+\.\d+(?:e[-+]?\d+)?", re.I)
BIGINT = re.compile(r"(?<![\w#@])-?\d{4,}(?![\w.])")
UNIT = re.compile(r"<x>\s*(mm|cm|m|in|deg|rad|um|µm)\b")


class Redactor:
    def __init__(self):
        self.names: dict[str, str] = {}

    def add(self, name, placeholder):
        if name and name not in self.names:
            self.names[name] = placeholder

    def text(self, s) -> str:
        s = str(s)
        s = REFS.sub(lambda m: "<ref:" + (re.search(r"([a-z-]+)#?[^#]*$", m.group()).group(1) if re.search(r"([a-z-]+)#?[^#]*$", m.group()) else "") + ">", s)
        s = GUID.sub("<guid>", s)
        s = FILES.sub("<file>", s)
        for name in sorted(self.names, key=len, reverse=True):
            s = re.sub(r"(?<![\w])" + re.escape(name) + r"(?![\w])", self.names[name], s)
        s = DECIMAL.sub("<x>", s)
        s = BIGINT.sub("<n>", s)
        s = UNIT.sub(r"<x> \1", s)
        return s


def shape(v, depth=0):
    """The structure of a definition: enum-like strings kept, numbers and references masked."""
    if depth > 5:
        return "…"
    if isinstance(v, dict):
        out, named = {}, []
        for k, x in v.items():
            if k.endswith("_offset") or k in ("id", "native_ref"):
                continue
            if re.fullmatch(r"[a-z][a-z0-9_]{0,40}", k) and not re.fullmatch(r"d\d+", k):
                out[k] = shape(x, depth + 1)
            else:
                named.append(shape(x, depth + 1))  # a key that is a name (parameter, entity): drop it
        if named:
            out["<named entries>"] = {"count": len(named), "first": named[0]}
        return out
    if isinstance(v, list):
        if not v:
            return []
        return {"count": len(v), "first": shape(v[0], depth + 1)}
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, (int, float)):
        return "<num>"
    if isinstance(v, str):
        return v if re.fullmatch(r"[a-z][a-z0-9_]{0,40}", v) else "<ref>"
    return "<?>"


def record_layout(raw, ri):
    """Where numbers and words sit in a raw record, without their values."""
    a, b = raw.span(ri)
    if a is None:
        return None
    B = raw.B
    nums = []
    for o in range(a, min(b, a + 4096) - 7):
        x = struct.unpack_from("<d", B, o)[0]
        if math.isfinite(x) and (x == 0.0 or 1e-6 < abs(x) < 1e5) and (o - a) % 1 == 0:
            if abs(x) in (0.0, 1.0):
                continue
            if abs(x * 1e6 - round(x * 1e6)) < 1e-3:
                nums.append(o - a)
    # keep offsets that don't overlap the previous one (an 8-byte value)
    kept = []
    for o in nums:
        if not kept or o >= kept[-1] + 8:
            kept.append(o)
    words = [m.group().decode("utf-16le") for m in re.finditer(rb"(?:[\x20-\x7e]\x00){3,}", B[max(0, a - 300):b])]
    roles = [w for w in words if re.fullmatch(r"[A-Z][a-z]+(?:[A-Z][a-z0-9]+)+|[A-Z][a-z]{2,}", w)]
    return {"record": ri, "class": raw.cls(ri), "size": b - a, "number_offsets": kept[:40], "role_words": roles[:12]}


def design_report(conv, ir, label: str, red: Redactor) -> dict:
    feats = conv.feats
    for f in feats:
        red.add(f["name"], f"F{f.get('ordinal')}")
    for ns in conv.ns:
        for slot, nm in list(ns.names.items()):
            red.add(nm, f"body:c{ns.index + 1}s{slot}")
        for slot in range(1, 200):
            red.add(ns.name(slot), f"body:c{ns.index + 1}s{slot}")
    for p in ir.get("model", {}).get("parameters", []) or []:
        red.add(p.get("name"), "<param>")
    if conv.raw:
        for roles in conv.raw.roles.values():
            for r in roles.values():
                red.add(r.get("name"), "<param>")

    timeline, assumptions = [], {}
    by_id = {f["id"]: f for f in feats}
    for f in feats:
        d = f.get("definition", {})
        tags = []
        for n in conv.feature_notes.get(f["id"], []):
            for rx, tag in NOTE_TAGS:
                if re.search(rx, n):
                    tags.append(tag)
                    assumptions.setdefault(tag, []).append(f"F{f.get('ordinal')}")
        entry = {"feature": f"F{f.get('ordinal')}", "type": f.get("source_tag"), "definition": d.get("definition"),
                 "status": conv.status.get(f["id"], "not reached"), "shape": shape(d)}
        if d.get("definition") == "sketch":
            sk = (d.get("sketch") or {}).get("sketch")
            ents = conv.entities_by_sketch.get(sk, [])
            kinds: dict = {}
            for e in ents:
                k = e["geometry"]["kind"]
                kinds[k] = kinds.get(k, 0) + 1
            entry["sketch"] = {"entities": kinds, "profiles": len(conv.sketch_by_id.get(sk, {}).get("profiles", []))}
        sp = f.get("source_properties") or {}
        if f.get("_A") is not None:
            entry["bodies_affected"] = len(f.get("_A", []))
            entry["bodies_new"] = len(conv.new_slots.get(f["id"], []))
            entry["centres_exact"] = len(f.get("_exact", ()))
        if tags:
            entry["assumptions"] = sorted(set(tags))
        if entry["status"] in ("gap", "imported") and conv.raw:
            sc = conv.raw.scope_of(f)
            if sc:
                entry["records"] = {
                    "scope": {"class": sc.get("class_tag"), "kind": sc.get("kind"), "size": sc.get("frame_length"),
                              "decoded_fields": sorted(k for k in sc if not k.endswith("_offset") and k not in ("id", "byte_offset", "record_index", "reference_members", "reference_member_offsets"))},
                    "references": [record_layout(conv.raw, ri) for ri in sc.get("reference_members", [])][:24],
                    "roles": sorted({r["role"] + (f" [{r['unit']}]" if r["unit"] else "") for r in conv.raw.roles.get(sc["record_index"], {}).values()}),
                }
            entry["source_property_keys"] = sorted(sp.keys())
        timeline.append(entry)

    gaps = [{"feature": f"F{g.get('ordinal')}" if g.get("ordinal") is not None else red.text(g["feature"]),
             "type": g.get("kind"), "reason": red.text(g["reason"])} for g in conv.gaps if g["reason"] != "after an earlier gap"]
    checks = []
    for c in conv.check_results:
        e = {"feature": f"F{c.get('ordinal')}", "status": c.get("status")}
        if c.get("bodies"):
            e["bodies"] = [{**{k: v for k, v in b.items() if k != "body"}, "body": red.text(b["body"])} for b in c["bodies"]
                           if b.get("missing") or any(abs(x) > 1e-3 for x in b.get("delta_mm", []))]
        if c.get("errors"):
            e["errors"] = [red.text(x) for x in c["errors"]]
        if c.get("message"):
            e["solidcraft_error"] = {"command": c.get("step_command"), "from_feature": f"F{c.get('step_feature')}",
                                     "params": c.get("step_params"), "message": red.text(c["message"])}
        if e["status"] != "ok" or e.get("bodies"):
            checks.append(e)
    losses: dict = {}
    for l_ in ir.get("decode_losses", []):
        if l_.get("severity") == "Info":
            continue
        t = red.text(l_.get("message", ""))
        t = re.sub(r"#\d+", "#<n>", t)
        losses[t] = losses.get(t, 0) + 1
    counts: dict = {}
    for f in feats:
        k = f.get("source_tag")
        st = conv.status.get(f["id"], "not reached")
        counts.setdefault(k, {}).setdefault(st, 0)
        counts[k][st] += 1
    return {
        "design": label,
        "summary": {
            "features": len(feats),
            "converted": sum(1 for v in conv.status.values() if v == "converted"),
            "imported_from_final_brep": sum(1 for v in conv.status.values() if v == "imported"),
            "left_out": sum(1 for v in conv.status.values() if v == "left out"),
            "first_gap": gaps[0] if gaps else None,
            "check_problems": sum(1 for c in checks if c["status"] not in ("ok", "ok (approximate centres)")),
            "components": len(conv.ns),
            "bodies_tracked": sum(len(ns.names) for ns in conv.ns),
        },
        "by_type": counts,
        "assumptions": {k: sorted(set(v), key=lambda x: int(x[1:]) if x[1:].isdigit() else 0) for k, v in assumptions.items()},
        "gaps": gaps,
        "checks": checks,
        "decode_warnings": [{"message": k, "count": v} for k, v in sorted(losses.items(), key=lambda kv: -kv[1])][:60],
        "timeline": timeline,
    }
