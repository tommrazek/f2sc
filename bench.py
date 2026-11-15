#!/usr/bin/env python3
"""Run the converter on every benchmark and write a summary table (Markdown and JSON).

    bench.py OUTDIR [--sc SOLIDCRAFT_CLI] [--only NAME ...]

Benchmarks are the .f3d/.f3z files in benchmarks/ (next to this script) unless
F3D2SC_BENCHMARKS names another folder. Each one is converted with --partial --check; the
table lists, per design or component: commands, gaps, features imported instead of
converted, and check problems."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir")
    ap.add_argument("--sc", default=os.environ.get("SOLIDCRAFT_CLI", "solidcraft-cli"))
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--benchmarks", default=os.environ.get("F3D2SC_BENCHMARKS", os.path.join(HERE, "benchmarks")))
    a = ap.parse_args()
    files = sorted(f for f in os.listdir(a.benchmarks) if f.endswith((".f3d", ".f3z")))
    rows, summary = [], {}
    for f in files:
        name = os.path.splitext(f)[0].replace(" ", "_")
        if a.only and name not in a.only:
            continue
        od = os.path.join(a.outdir, name)
        os.makedirs(od, exist_ok=True)
        src = os.path.join(a.benchmarks, f)
        rep = os.path.join(od, "report.json")
        if f.endswith(".f3z"):
            cmd = [sys.executable, os.path.join(HERE, "f3d2sc.py"), src, "-o", od]
        else:
            cmd = [sys.executable, os.path.join(HERE, "f3d2sc.py"), src, "-o", os.path.join(od, name + ".solidcraft.json"),
                   "--step", os.path.join(od, name + ".reference.step")]
        cmd += ["--partial", "--check", a.sc, "--report", rep]
        t0 = time.time()
        r = subprocess.run(cmd, capture_output=True, text=True)
        dt = time.time() - t0
        open(os.path.join(od, "log.txt"), "w").write(r.stdout + r.stderr)
        crashed = "Traceback" in r.stderr
        try:
            reports = json.load(open(rep))
        except Exception:
            reports = {}
        if "commands" in reports and "gaps" in reports:
            reports = {name: reports}
        summary[name] = {"seconds": round(dt, 1), "crashed": crashed, "exit": r.returncode, "designs": {}}
        if crashed:
            rows.append((name, "-", "CRASH", "", "", "", "", "", r.stderr.strip().splitlines()[-1][:100]))
        for d, v in reports.items():
            if not isinstance(v, dict) or "gaps" not in v:
                continue
            gaps = v.get("gaps") or []
            imp = v.get("imported_instead") or []
            probs = v.get("check_problems") or []
            fs = v.get("final_shape") or {}
            fstat = fs.get("status", "-")
            if fs.get("bodies"):
                fstat += f" {sum(b['match'] is True for b in fs['bodies'])}/{len(fs['bodies'])}"
            if fs.get("reference_incomplete"):
                fstat += " (ref. incomplete)"
            summary[name]["designs"][d] = {"commands": v.get("commands"), "gaps": [g.get("feature") for g in gaps],
                                           "imported": len(imp), "skipped": len(v.get("skipped") or []),
                                           "check_problems": len(probs), "final_shape": fstat}
            rows.append((name, d.split(".")[-1] if "." in d else d, v.get("commands"), len(gaps),
                         len(imp), len(v.get("skipped") or []), len(probs), fstat, (gaps[0].get("feature", "") + ": " + gaps[0].get("reason", ""))[:90] if gaps else ""))
    md = ["| benchmark | design | commands | gaps | imported | skipped | check problems | final shape | first gap |",
          "|---|---|---|---|---|---|---|---|---|"]
    md += [f"| {' | '.join(str(x) for x in r)} |" for r in rows]
    os.makedirs(a.outdir, exist_ok=True)
    open(os.path.join(a.outdir, "summary.md"), "w").write("\n".join(md) + "\n")
    json.dump(summary, open(os.path.join(a.outdir, "summary.json"), "w"), indent=1)
    print("\n".join(md))


if __name__ == "__main__":
    main()
