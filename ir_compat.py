"""Read a CADIR v11 document (cadmpeg after #2145) in the shape the converter was written for.

cadmpeg's v11 wire splits the old `model` into sections (`design`, `geometry`, ...), leaves some
design arenas out of the wire altogether (the decoder writes those as `wire_omitted`), and wraps
many records in adjacently tagged enums (`{"kind": k, "value": {...}}`) where the old shape put
the tag beside the fields. `upgrade(ir)` rebuilds the old `model` in place; a document already in
the old shape is returned unchanged.
"""

SECTIONS = ("geometry", "design")


def is_v11(ir):
    return any(isinstance(ir.get(s), dict) for s in SECTIONS) or "wire_omitted" in ir


def upgrade(ir):
    if not is_v11(ir) or ir.get("_v11_upgraded"):
        return ir
    m = dict(ir.get("model") or {})
    for sec in SECTIONS:
        for k, v in (ir.get(sec) or {}).items():
            if isinstance(v, list) and (v or k not in m):
                m[k] = v
    for k, v in (ir.get("wire_omitted") or {}).items():
        if isinstance(v, list) and (v or k not in m):
            m[k] = v
    for arena, fix in ARENAS.items():
        for rec in m.get(arena) or []:
            if isinstance(rec, dict):
                fix(rec)
    _derive_links(m)
    _occurrences(m)
    _single_part(m)
    ir["model"] = m
    ir["_v11_upgraded"] = True
    return ir


# --- helpers ---------------------------------------------------------------------------------

def _flat(d):
    """{"kind": k, "value": {...}} -> {"kind": k, ...}; anything else unchanged."""
    if isinstance(d, dict) and set(d) == {"kind", "value"} and isinstance(d["value"], dict) and "kind" not in d["value"]:
        return {"kind": d["kind"], **d["value"]}
    return d


def _unwrap_value(d):
    """{"kind": k, "value": X} -> X (a wrapper the old shape did not have at all)."""
    if isinstance(d, dict) and set(d) == {"kind", "value"}:
        return d["value"]
    return d


def _geometry(rec):
    g = rec.get("geometry")
    if isinstance(g, dict) and g.get("kind") == "solved" and isinstance(g.get("value"), dict):
        rec["geometry"] = g["value"]
    elif isinstance(g, dict) and isinstance(g.get("value"), dict) and "kind" not in g["value"]:
        rec["geometry"] = {"kind": g["kind"], **g["value"]}
    g = rec.get("geometry")
    if isinstance(g, dict) and isinstance(g.get("poles"), dict) and "kind" in g["poles"] and "form" not in g["poles"]:
        g["poles"]["form"] = g["poles"].pop("kind")


def _target(rec):
    t = rec.get("target")
    if isinstance(t, dict) and isinstance(t.get("entity"), dict):
        e = t.pop("entity")
        t["kind"], t["id"] = e.get("kind"), e.get("value")


def _edge(rec):
    e = rec.pop("ends", None)
    if isinstance(e, dict):
        rec["start"], rec["end"] = e.get("start"), e.get("end")


def _face(rec):
    lp = rec.get("loops")
    if isinstance(lp, dict) and "kind" in lp and "classification" not in lp:
        lp["classification"] = lp.pop("kind")


def _pcurve(rec):
    _geometry(rec)
    md = rec.get("metadata")
    if isinstance(md, dict) and "kind" in md and "source" not in md:
        md["source"] = md.pop("kind")


def _procedural(rec):
    c = (rec.get("definition") or {}).get("cache")
    if isinstance(c, dict) and "kind" in c and "layout" not in c:
        c["layout"] = c.pop("kind")

    def walk(x):
        if isinstance(x, dict):
            p = x.get("poles")
            if isinstance(p, dict) and "kind" in p and "form" not in p:
                p["form"] = p.pop("kind")
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(rec)


def _parameter(rec):
    own = rec.get("owner")
    if isinstance(own, str):
        rec.setdefault("properties", {})


def _entity_ref(x):
    """Sketch-constraint operands: {"kind": k, "value": id} -> {"kind": k, "entity": id}."""
    if isinstance(x, dict) and "value" in x and "entity" not in x and isinstance(x["value"], str):
        x["entity"] = x.pop("value")


def _sketch_constraint(rec):
    d = rec.get("definition")
    if not isinstance(d, dict):
        return
    for k in ("first", "second", "third", "entity", "line", "curve", "point", "center", "mirror"):
        _entity_ref(d.get(k))
    for k in ("loci", "entities", "operands"):
        for x in d.get(k) or []:
            _entity_ref(x)


def _body_ref(x):
    """v11 names a body by the feature that made it: {"feature": f, "local_id": n}; the old shape
    kept the body's id string."""
    if isinstance(x, dict) and set(x) >= {"feature", "local_id"} and "id" in x:
        return x["id"]
    return x


def _feature(rec):
    d = rec.get("definition")
    if not isinstance(d, dict) or "definition" in d:
        return
    kind, val = d.get("kind"), d.get("value")
    if kind == "operation" and isinstance(val, dict):
        kind, val = val.get("kind"), val.get("value")
    new = {"definition": kind}
    if isinstance(val, dict):
        new.update(val)
    elif val is not None:
        new["value"] = val
    for k, v in d.items():
        if k not in ("kind", "value"):
            new.setdefault(k, v)
    _OPS.get(kind, lambda _d: None)(new)
    _walk_selections(new)
    _body_selections(new)
    rec["definition"] = new


def _walk_selections(x):
    """Selections v11 resolves further than the old shape did, put back in the old form."""
    if isinstance(x, dict):
        if x.get("kind") == "generated" and isinstance(x.get("value"), dict) and "native" in x["value"]:
            # bodies named by the feature that made them and their local id: the old shape's
            # historical selection (whose body ids end in that local id)
            v = x["value"]
            x["kind"] = "historical"
            x["value"] = {"bodies": [f"{b.get('feature')}:{b.get('local_id')}" for b in v.get("bodies") or []],
                          "native": v.get("native")}
        if "kind" in x and "region" not in x and isinstance(x.get("outer_boundary"), list):
            x["region"] = x.pop("kind")
        for v in x.values():
            _walk_selections(v)
    elif isinstance(x, list):
        for v in x:
            _walk_selections(v)


def _op_sketch(d):
    s = d.get("sketch")
    if isinstance(s, dict) and "kind" in s and "space" not in s:
        s["space"] = s.pop("kind")


def _op_extrude(d):
    p = d.get("profile")
    if isinstance(p, dict) and p.get("kind") in ("planar",) and isinstance(p.get("value"), dict):
        d["profile"] = p["value"]


def _op_revolve(d):
    c = d.get("construction")
    if isinstance(c, dict) and set(c) == {"kind", "value"} and isinstance(c["value"], dict):
        d["construction"] = {**c["value"], "state": c["kind"]}
    p = d.get("profile")
    if isinstance(p, dict) and p.get("kind") in ("planar",) and isinstance(p.get("value"), dict):
        d["profile"] = p["value"]


def _body_selections(d):
    """Body selections v11 resolves to final B-rep bodies: the old shape kept the native operand."""
    def fix(sel):
        if isinstance(sel, dict) and sel.get("kind") == "resolved" and isinstance(sel.get("value"), dict) \
                and sel["value"].get("native") and any("brep" in b for b in sel["value"].get("bodies") or []):
            sel["kind"], sel["value"] = "native", sel["value"]["native"]
        elif isinstance(sel, dict) and sel.get("kind") == "resolved_set" and isinstance(sel.get("value"), dict):
            mem = sel["value"].get("members") or []
            if mem and all(m_.get("native") for m_ in mem):
                sel["kind"], sel["value"] = "native_set", [m_["native"] for m_ in mem]
    ops = d.get("operands")
    if isinstance(ops, dict):
        fix(ops.get("target"))
        fix(ops.get("tools"))
    for sd in d.get("seeds") or []:
        if isinstance(sd, dict):
            fix(sd.get("value"))


def _op_hole(d):
    f = d.get("face")
    if isinstance(f, dict) and f.get("kind") == "resolved" and isinstance(f.get("value"), dict) and f["value"].get("native"):
        d["face"] = {"kind": "native", "value": f["value"]["native"]}
    s = d.get("shape")
    if isinstance(s, dict):
        c = s.get("construction")
        if isinstance(c, dict) and set(c) == {"kind", "value"} and isinstance(c["value"], dict):
            s["construction"] = {"construction": c["kind"], **c["value"]}


def _op_datum_axis(d):
    a = d.pop("axis", None)
    if isinstance(a, dict):
        for k in ("origin", "direction"):
            if k in a:
                d.setdefault(k, a[k])


_OPS = {"datum_axis": _op_datum_axis, "sketch": _op_sketch, "extrude": _op_extrude, "revolve": _op_revolve, "hole": _op_hole}

ARENAS = {
    "curves": _geometry,
    "surfaces": _geometry,
    "pcurves": _pcurve,
    "procedural_curves": _procedural,
    "procedural_surfaces": _procedural,
    "attributes": _target,
    "appearance_bindings": _target,
    "edges": _edge,
    "faces": _face,
    "parameters": _parameter,
    "sketch_constraints": _sketch_constraint,
    "features": _feature,
}


def _derive_links(m):
    """Back-links v11 no longer stores: body -> regions, shell -> member faces."""
    regions = {}
    for r in m.get("regions") or []:
        regions.setdefault(r.get("body"), []).append(r.get("id"))
    for b in m.get("bodies") or []:
        if "regions" not in b:
            b["regions"] = regions.get(b.get("id"), [])
    members = {}
    for f in m.get("faces") or []:
        members.setdefault(f.get("shell"), []).append({"id": f.get("id"), "kind": "face"})
    for s in m.get("shells") or []:
        if not s.get("members"):
            s["members"] = members.get(s.get("id"), [])


def _occurrences(m):
    """v11 occurrences name their prototype by product-definition id and leave the parent to the
    product definition that lists them; the old shape had both on the occurrence."""
    pds = {p.get("id"): p for p in m.get("product_definitions") or []}
    occs = m.get("occurrences") or []
    if not occs or all(isinstance(o.get("prototype"), dict) for o in occs):
        return
    inst = {}  # product definition -> occurrences of it
    for o in occs:
        if isinstance(o.get("prototype"), str):
            inst.setdefault(o["prototype"], []).append(o["id"])
    owner = {}
    for p in pds.values():
        for oid in p.get("occurrences") or []:
            owner[oid] = p.get("id")
    # The old shape had an occurrence of the root component (the one whose definition lists
    # occurrences but that no occurrence instantiates); its children named it as parent.
    for p in list(pds.values()):
        pid = p.get("id", "")
        if "component#" in pid and p.get("occurrences") and pid not in inst:
            rid = "f3d:model:occurrence#root-" + pid.rsplit("#", 1)[1]
            occs.insert(0, {"id": rid, "parent": {"kind": "root"}, "prototype": pid,
                            "transform": [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0]]})
            inst[pid] = [rid]
    m["occurrences"] = occs
    for i, o in enumerate(occs):
        if o.get("prototype") is None:
            o["prototype"] = {"scope": "unresolved"}
        pr = o.get("prototype")
        if isinstance(pr, str):
            st = (pds.get(pr) or {}).get("storage") or {}
            if st.get("kind") == "external" and not st.get("path"):
                o["prototype"] = {"scope": "unresolved"}
            elif st.get("kind") == "external":
                o["prototype"] = {"document": {"path": st.get("path"), "resolution": "path"}, "scope": "external"}
            else:
                o["prototype"] = {"definition": pr, "scope": "local"}
        if "parent" not in o:
            up = inst.get(owner.get(o["id"])) or []
            o["parent"] = {"kind": "occurrence", "occurrence": up[0]} if up else {"kind": "root"}
        o.setdefault("ordinal", i)
        o.setdefault("scale", [1.0, 1.0, 1.0])
    # external documents are not components of this design
    m["product_definitions"] = [p for p in pds.values() if ((p.get("storage") or {}).get("kind") != "external")]


def _single_part(m):
    """v11 gives a design with no components of its own one product definition (its root); the
    old shape gave it none, and the converter takes a design with product definitions as one to
    split into components."""
    pds = m.get("product_definitions") or []
    if m.get("occurrences"):
        return
    named = {f.get("product_definition") for f in m.get("features") or []}
    # the document root's own definition, which no feature names: the old shape had none
    keep = [p for p in pds if not (str(p.get("id", "")).endswith("product-definition#root") and p.get("id") not in named)]
    m["product_definitions"] = [] if len(pds) == 1 and not pds[0].get("occurrences") else keep
