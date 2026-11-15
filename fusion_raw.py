"""Read parameters cadmpeg leaves native from Fusion's design stream (BulkStream.dat).

Everything here was worked out from the two benchmark files, and each reading is checked
against cadmpeg's per-feature body centres or the final B-rep when the script runs:

- Records start with a class-tag header; cadmpeg lists them (design_record_headers) with their
  byte offsets, so a record runs to the next header.
- Class tags are numbered per file: what a tag means comes from the MetaStream's type table
  (design_types: a type GUID and the records of that type). Kinds below are by type GUID; the
  class numbers given are the ones in film_scanner.f3d.
- Lengths are stored in centimetres, angles in radians.
- Feature parameters are not read here any more: cadmpeg decodes them (design_parameter_owners
  joined to design_parameters: feature scope, slot, evaluated value, role, unit, name).
- A circular pattern's axis is a point and a vector at +25 of an axis record (type F2A7590D or
  493A89BA), in cm; its count is a u32 at +41 of its settings record (class 404,
  after a marker 1 at +40) or a double in a class-281 record, and its angle
  the TotalAngle parameter, when the feature has them (otherwise the converter infers both from
  the bodies the pattern makes).
- A thread's designation, class and profile are UTF-16 strings in its scope record.
"""
from __future__ import annotations

import bisect
import math
import re
import struct

CM = 10.0  # mm per Fusion internal length unit

# Record kinds by the first 8 hex digits of their type GUID (stable across files).
KINDS = {
    "D91D429C": "param",          # 274 in film_scanner: a parameter
    "2E3AC990": "param",          # 272 in film_scanner: a thin extrude's parameter
    "84E6245F": "matrix",         # 419: a Move's transform
    "F2A7590D": "ray",            # 373: a point and a vector (cm; checked on film_scanner and Side_Panels)
    "493A89BA": "ray",            # a point and a vector (Body_Panels' pattern axis; its check passes)
    "5A1BF548": "entity_ref",     # 281: a reference to model entities (a pattern's count is in it)
    "A085449A": "pattern_setup",  # 404: a pattern's settings: marker 1 at +40, its count (u32) at +41
}
# Film-scanner numbering, used only when a file has no type table.
LEGACY = {"274": "param", "272": "param", "419": "matrix", "373": "ray", "367": "ray", "281": "entity_ref"}


class Raw:
    def __init__(self, native: dict, bulk: bytes):
        self.n = native
        self.B = bulk
        hdr = sorted(native.get("design_record_headers", []), key=lambda r: r["byte_offset"])
        self.hdr = hdr
        self.offs = [r["byte_offset"] for r in hdr]
        self.rec = {r["record_index"]: r for r in hdr}
        self.scopes = {p["record_index"]: p for p in native.get("design_parameter_scopes", [])}
        self.scope_by_offset = {p["byte_offset"]: p for p in self.scopes.values()}
        # class tag -> kind, from the type table (most of a tag's records are of one type).
        by_rec = {}
        for t in native.get("design_types", []):
            for e in t.get("entity_ids", []):
                by_rec[e] = t.get("type_guid", "").upper()[:8]
        votes: dict[str, dict[str, int]] = {}
        for r in hdr:
            g = by_rec.get(r["record_index"])
            if g:
                votes.setdefault(r["class_tag"], {}).setdefault(g, 0)
                votes[r["class_tag"]][g] += 1
        self.kind_of_tag = {t: KINDS.get(max(v, key=v.get)) for t, v in votes.items()} if votes else dict(LEGACY)
        # Feature parameters as the decoder states them: each owner record names its feature
        # scope, its slot there and its evaluated value; the parameter record it owns carries the
        # role (source kind), unit, name and expression.
        self.params: dict[int, dict[int, float]] = {}
        self.roles: dict[int, dict[str, dict]] = {}
        prm = {p["record_index"]: p for p in native.get("design_parameters", [])}
        for o in native.get("design_parameter_owners", []):
            owner, slot, val = o.get("scope_record_index"), o.get("local_ordinal"), o.get("evaluated_value")
            if owner is None or slot is None or val is None:
                continue
            self.params.setdefault(owner, {})[slot] = val
            q = prm.get(o.get("parameter_record_index"))
            if q and q.get("source_kind"):
                self.roles.setdefault(owner, {})[q["source_kind"]] = {
                    "role": q["source_kind"], "unit": q.get("unit") or "", "name": q.get("name"),
                    "expression": q.get("expression"), "value": val, "slot": slot}

    def role(self, scope, role):
        """A feature parameter by role: its value in mm (lengths) or radians (angles), or None."""
        r = self.roles.get(scope["record_index"], {}).get(role)
        if r is None:
            return None
        return r["value"] * CM if r["unit"] in ("mm", "cm", "m", "in") else r["value"]

    def span(self, ri):
        r = self.rec.get(ri)
        if r is None:
            return None, None
        o = r["byte_offset"]
        j = bisect.bisect_right(self.offs, o)
        return o, (self.offs[j] if j < len(self.offs) else len(self.B))

    def dbl(self, o):
        return struct.unpack_from("<d", self.B, o)[0]

    def cls(self, ri):
        r = self.rec.get(ri)
        return r["class_tag"] if r else None

    def kind(self, ri):
        return self.kind_of_tag.get(self.cls(ri))

    def scope_of(self, feature: dict):
        ref = feature.get("native_ref", "")
        m = re.search(r"design-parameter-scope#(\d+)$", ref)
        return self.scope_by_offset.get(int(m.group(1))) if m else None

    def feature_params(self, scope) -> dict[int, float]:
        return self.params.get(scope["record_index"], {})

    def refs(self, scope):
        return scope.get("reference_members", [])

    # ---- Circular pattern ----
    def circular_pattern(self, scope):
        axis = count = angle = setup_count = None
        for ri in self.refs(scope):
            c = self.kind(ri)
            a, b = self.span(ri)
            if c in ("segment", "ray") and axis is None and b - a >= 25 + 48:
                p = struct.unpack_from("<6d", self.B, a + 25)
                d = [p[3] - p[0], p[4] - p[1], p[5] - p[2]] if c == "segment" else list(p[3:6])
                L = norm(d)
                if L > 1e-9:
                    axis = {"origin": [x * CM for x in p[0:3]], "dir": [x / L for x in d]}
            if c == "pattern_setup" and b - a > 45 and self.B[a + 40] == 1:
                n = struct.unpack_from("<I", self.B, a + 41)[0]
                if 2 <= n <= 10000:
                    setup_count = n
            if c == "entity_ref" and count is None:
                for o in range(a, b - 7):
                    x = self.dbl(o)
                    if math.isfinite(x) and 2 <= x <= 1000 and x == int(x):
                        count = int(x)
                        break
        angle = self.role(scope, "TotalAngle")
        return axis, setup_count or count, angle

    # ---- Thread ----
    def thread(self, scope):
        a = scope["byte_offset"]
        b = scope.get("paired_byte_offset") or (a + scope.get("frame_length", 0))
        seg = self.B[a:b]
        strs = [m.group().decode("utf-16le") for m in re.finditer(rb"(?:[\x20-\x7e]\x00){2,}", seg)]
        out = {"strings": strs}
        for s in strs:
            if re.fullmatch(r"M\d+(\.\d+)?(x\d+(\.\d+)?)?", s):
                out.setdefault("designation", s)
            elif re.fullmatch(r"\d[gGhH](\d[gGhH])?", s):
                out.setdefault("class", s)
            elif "profile" in s.lower():
                out.setdefault("profile", s)
        out["offset"] = self.role(scope, "ThreadOffset")
        out["length"] = self.role(scope, "ThreadLength")
        return out


def norm(v):
    return math.sqrt(sum(x * x for x in v))


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]
