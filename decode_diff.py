#!/usr/bin/env python3
"""Regression check for the decoder: decode every benchmark design with two decoder builds and
compare what each resolves.

    decode_diff.py OLD_DECODER NEW_DECODER [--benchmarks DIR] [--json OUT]

For each design (an .f3d, or each .f3d inside an .f3z): whether it decodes, how many
features of each kind are resolved / unresolved / native, the bodies, and the decode-loss
codes. Prints the designs that changed; a feature that was resolved before and is not now
is a regression."""
from __future__ import annotations

import argparse
import collections
import json
import os
import subprocess
import sys
import tempfile
import zipfile

import ir_compat

HERE = os.path.dirname(os.path.abspath(__file__))


def designs(bench, prefix=""):
    for f in sorted(os.listdir(bench)):
        p = os.path.join(bench, f)
        if os.path.isdir(p):
            yield from designs(p, prefix + f + "/")  # controlled test designs (benchmarks/dummies)
            continue
        f = prefix + f
        if f.endswith(".f3d"):
            yield f, p
        elif f.endswith(".f3z"):
            td = tempfile.mkdtemp(prefix="f3z-")
            with zipfile.ZipFile(p) as z:
                for n in z.namelist():
                    if n.endswith(".f3d"):
                        out = os.path.join(td, os.path.basename(n))
                        open(out, "wb").write(z.read(n))
                        yield f"{f}/{os.path.basename(n)[:24]}", out


def summarise(decoder, path, td):
    out = os.path.join(td, "d.ir.json")
    r = subprocess.run([decoder, path, out], capture_output=True, text=True)
    if r.returncode != 0:
        return {"error": (r.stderr.strip().splitlines() or ["?"])[-1][:200]}
    ir = ir_compat.upgrade(json.load(open(out)))
    m = ir["model"]
    kinds = collections.Counter()
    feats = {}
    for f in m.get("features", []):
        d = f.get("definition", {})
        k = d.get("definition")
        state = "unresolved" if k == "unresolved" else "native" if k == "native" else "resolved"
        name = d.get("family") or d.get("kind") if state != "resolved" else k
        kinds[f"{state}:{name}"] += 1
        # keyed by timeline ordinal: a decoder that learns a feature's own name renames it
        key = f"{f.get('ordinal')}"
        if key in feats or f.get("ordinal") is None:
            key = f"{f.get('ordinal')}:{f['name']}"
        feats[key] = f"{state}:{name}"
    losses = collections.Counter(l_.get("code", l_.get("message", "")[:40]) for l_ in ir.get("decode_losses", []))
    return {"kinds": dict(kinds), "features": feats, "bodies": len(m.get("bodies", [])), "losses": dict(losses)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("old")
    ap.add_argument("new")
    ap.add_argument("--benchmarks", default=os.path.join(HERE, "benchmarks"))
    ap.add_argument("--json")
    a = ap.parse_args()
    td = tempfile.mkdtemp()
    res, regress = {}, 0
    for name, path in designs(a.benchmarks):
        o, n = summarise(a.old, path, td), summarise(a.new, path, td)
        res[name] = {"old": o, "new": n}
        if o == n:
            print(f"= {name}")
            continue
        print(f"~ {name}")
        if "error" in o or "error" in n:
            print(f"    decodes: {'no: ' + o['error'] if 'error' in o else 'yes'} -> {'no: ' + n['error'] if 'error' in n else 'yes'}")
            if "error" in n and "error" not in o:
                regress += 1
            continue
        for k in sorted(set(o["features"]) | set(n["features"])):
            a_, b_ = o["features"].get(k), n["features"].get(k)
            if a_ != b_:
                worse = a_ and a_.startswith("resolved") and not (b_ or "").startswith("resolved")
                regress += bool(worse)
                print(f"    {'REGRESSION ' if worse else ''}{k}: {a_} -> {b_}")
        if o["bodies"] != n["bodies"]:
            print(f"    bodies: {o['bodies']} -> {n['bodies']}")
        for k in sorted(set(o["losses"]) | set(n["losses"])):
            if o["losses"].get(k) != n["losses"].get(k):
                print(f"    loss {k}: {o['losses'].get(k, 0)} -> {n['losses'].get(k, 0)}")
    if a.json:
        json.dump(res, open(a.json, "w"), indent=1)
    print(f"{regress} regression(s)")
    sys.exit(1 if regress else 0)


if __name__ == "__main__":
    main()
