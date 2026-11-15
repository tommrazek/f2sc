#!/usr/bin/env python3
"""Compare `solidcraft-cli eval` of a converted script with eval of the f3d's reference STEP."""
import json, subprocess, sys
sc, script, step = sys.argv[1:4]
def ev(x):
    r = subprocess.run([sc, "eval", x], capture_output=True, text=True)
    if r.returncode: sys.exit(r.stderr)
    return json.loads(r.stdout)
a, b = ev(script), ev(step)
errs = [t for t in a.get("timeline", []) if t.get("error") or t.get("warning")]
for t in errs: print("timeline:", t["name"], t.get("error") or t.get("warning"))
key = lambda bd: (round(bd["center_of_mass"][0], 1), round(bd["center_of_mass"][1], 1), round(bd["center_of_mass"][2], 1))
A, B = sorted(a["bodies"], key=key), sorted(b["bodies"], key=key)
print(f"bodies: converted {len(A)}, reference {len(B)}")
ok = len(A) == len(B)
for x, y in zip(A, B):
    dv = (x["volume_mm3"] - y["volume_mm3"]) / y["volume_mm3"]
    db = max(abs(p - q) for p, q in zip(x["bbox"]["min"] + x["bbox"]["max"], y["bbox"]["min"] + y["bbox"]["max"]))
    dc = max(abs(p - q) for p, q in zip(x["center_of_mass"], y["center_of_mass"]))
    good = abs(dv) < 1e-4 and db < 1e-3 and dc < 1e-3 and x["faces"] == y["faces"]
    ok &= good
    print(f"  {x['name']:>12} vs {y['name']:<12} vol {x['volume_mm3']:.4f} / {y['volume_mm3']:.4f} ({dv:+.2e})  bbox {db:.1e}  com {dc:.1e}  faces {x['faces']}/{y['faces']}  {'OK' if good else 'DIFF'}")
print("MATCH" if ok else "MISMATCH")
sys.exit(0 if ok else 1)
