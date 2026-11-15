# Overnight report, 12–13 Nov 2026

## In short

- Most of the planned night was lost. At about 00:45 one of my own commands stopped on an approval prompt that nobody was there to answer. Work only resumed at 06:00, when the wrap-up task fired. So this report covers about 1.5 hours of real work, not seven.
- **What got done:**
  - the cadmpeg fork is set up, with two genuine format fixes;
  - a harness that compares two decoder builds on every benchmark design;
  - a one-command benchmark run;
  - several general converter fixes.
- **The functioning-version target is met:** all four benchmarks run end to end with no crashes. test_asy is back to fully passing.
- **Not committed to git:** git on your Mac needs file-deletion permission in the folder, and I didn't want a second unanswered prompt to stall the run. All files are on your Mac; the commit commands are at the end.
- **Not started:** steps 3–6 of the plan, meaning the layer restructure, the comparison against Fusion's final shape, and the remaining Chaser items.

## Benchmarks, before and after

Each benchmark run with `bench.py`, which uses `--partial --check`. "Before" is the converter as it stood at 23:30; "after" is the converter at 06:45, using the decoder built from the fork.

| benchmark | design | commands | gaps | imported | check problems | change |
|---|---|---|---|---|---|---|
| steel_test_articles | — | 14 → 14 | 0 → 0 | 0 → 0 | 0 → 0 | unchanged, exact |
| film_scanner | — | 449 → 449 | 0 → 0 | 0 → 0 | 1 → 1 | unchanged (Combine 4 fails on SolidCraft issue 3, already known) |
| test_asy | Body_Panels | 129 → 129 | 0 | 0 | 0 → 0 | unchanged |
| test_asy | Bottom_Panels | 56 → 56 | 0 | 1 | 0 → 0 | unchanged |
| test_asy | Side_Panels | 142 → 142 | 0 | 0 | **7 → 0** | regression from the evening fixed (pattern-axis record) |
| test_asy | root | **0 → 0, 4 gaps → 0** | | | | regression fixed (Component Insert, root counted as a component) |
| Chaser | Hexagon screw (6_Plain) | 28 | 0 | 4 | 0 | **both revolves now decoded by the fork**; still imported from Extrude 2 on |
| Chaser | M3CB5H-16 | 2 | 0 | 0 | 0 | **decodes directly through the fork**, no manifest patch |
| Chaser | Spring | 2 | 0 | 0 | 0 | unchanged |
| Chaser | Stack_Main | 85 | 0 | 3 | 1 | unchanged (SolidCraft finds no cylinder at one thread's point) |
| Chaser | Stack_Aft_1 | 58 | 2 | 0 | 0 | unchanged |
| Chaser | Stack_Aft_2 | 48 | 0 | 0 | 0 | unchanged, all checks pass |
| Chaser | Stack_Front_1 | 14 | 8 | 0 | 0 | unchanged (revolve not decoded) |
| Chaser | Seal_Spacer | 11 | 2 | 0 | 0 | unchanged (Copy/Paste not mapped) |
| Chaser | Radial_Seal | 12 | 8 | 0 | 0 | unchanged (revolve not decoded) |
| Chaser | Piston | 20 → 31 | **18 → 5** | 0 | 0 → 1 | Screw/Component Insert recognised as no-geometry; now stops at an angled work plane; one chamfer lands on a tangent edge |

The raw runs are in `bench/base/summary.md` and `bench/after/summary.md` in the cloud workspace. Regenerate them with `python3 bench.py <outdir> --sc <solidcraft-cli>`.

## What went where

### cadmpeg fork (`cadmpeg-fork/`, its own git repo, 3 commits)

1. **The published crate:** `cadmpeg-codec-f3d` 0.6.11, imported unchanged from crates.io (Apache-2.0).
2. **Revolve direction byte:** the referenced-angle revolve form now accepts direction byte 1 at offset 33, but only for a full turn.
   - Fusion's fastener-library parts write this byte. What it selects isn't established, so partial revolves with it stay undecoded.
   - The Hexagon screw's two revolves now decode completely, axis and profile included. The axis matches what the converter's guess had produced.
3. **Offline-saved files:** the manifest now accepts the `NA_OFFLINESAVE` / `NA_EXPORT` marker after terminal byte 0, as it already did after terminal byte 1.
   - The M3 library part now decodes directly. The Python zip-rewrite workaround is deleted.

`decode_diff.py old-decoder new-decoder` checked both against every benchmark design (the parts inside the .f3z files included). Only those three features changed, and there were **0 regressions**.

`decoder/Cargo.toml` now points at the fork through `[patch.crates-io]`, at `../cadmpeg-fork/codec-f3d`.

### Not moved yet (still in Python, `fusion_raw.py`)

These are still read from raw bytes in Python, as originally planned for the fork:
- record types by type ID;
- the circular pattern count;
- thread designation, length and offset;
- parameter roles;
- sketch regions bounded by part of a longer line (`planar.py`);
- component ownership.

All are general rather than file-specific, but none is in the fork yet.

### SolidCraft layer and inference layer

Not restructured yet. The general fixes from tonight are in `f3d2sc.py`:
- **Pattern axis:** the axis record type (`F2A7590D`) is a point plus a vector, not a segment. Reading it as a segment caused the Side_Panels regression.
- **Components:** only components that hold bodies, or features that make them, count as components. A root that only places parts no longer becomes an empty design.
- **No-geometry features:** Component Insert, Screw, Joint, As-built Joint and Rigid Group are left out, with a note. For "Screw", that's an assumption based on it having no parameters and no body data.
- **Decoding:** the folder for a derived body's STEP file is created when missing, and decoding goes straight through the fork with no retries.

## Opening results in the SolidCraft app

The `*.solidcraft.json` files are command scripts, not saved designs, which is why the app reports "missing field `name`". Build one first:

    cd out/<benchmark>
    solidcraft-cli run <name>.solidcraft.json --save <name>.sc

Then open the `.sc` file. Run from the script's own folder, because imported STEP files are found relative to it. For an assembly, build each part script first, then the root script (the root inserts the parts' `.sc` files). `f3d2sc.py ... --build <solidcraft-cli>` does that for you.

Building a `.sc` next to every script automatically, and renaming the scripts to `*.script.json`, wasn't done tonight.

## Open gaps (Chaser)

- **Stack_Front_1, Radial_Seal:** their first revolve (scope frame length 375) is still undecoded. The axis is probably a face or edge reference that cadmpeg doesn't resolve. This is a fork item, not inference.
- **Seal_Spacer, Radial_Seal:** Copy/Paste isn't mapped (cadmpeg leaves it native).
- **Piston:** stops at an angled work plane, because only planes parallel to XY, XZ or YZ are emitted. Chamfer 2's edge point lands on a tangent edge.
- **Stack_Main:**
  - one thread's face point misses its cylinder;
  - Extrude 7's profile is still native;
  - three features are imported instead of converted.
- **Stack_Aft_1:** Thread 2 (M2) finds no matching cylinder. Its holes come from extrudes whose circles the converter doesn't recognise as threadable faces.
- **Root assembly:** not built tonight.

## SolidCraft issues (additions to README issues 1–4)

5. **Modeled threads larger than their shaft:** SolidCraft refuses a thread whose designation is bigger than the shaft. Fusion builds a modeled thread at its designation's size, for example an M14 thread on a 12 mm spigot. The converter grows the face first with `solid.offset_face`.
6. **Uneven splines:** scripts only take splines with evenly spaced knots. Splines with uneven knots, including Fusion's double knots, are refitted to evenly spaced ones within 0.1 µm.

## Next steps, in order

1. **Commit both repos** (commands below).
2. **Move format facts into the fork:**
   - record types by type ID;
   - the pattern count (u32 at +41 of the settings record);
   - thread designation, length and offset;
   - component ownership by the bodies a feature produces;
   - the 375-length revolve form.
3. **Compare against Fusion's final shape** (volume, face count, bounding box per body) as a standard part of `--check`. This catches wrong thread faces and chamfers that today's check can't see.
4. **Restructure the Python:** `solidcraft/` for mapping and SolidCraft workarounds, `inference/` for named, switchable guesses, plus a per-file overrides JSON.
5. **Chaser:** Copy/Paste, angled planes, the remaining threads, then build the assembly.
6. **Build `.sc` files automatically**, and rename the scripts.

## Committing (needs file deletion allowed in the folder, for git's lock files)

    cd ~/Documents/gitrepositories/f3d-to-json
    rm -f .git/*.lock .git/stale-*; rm -rf _to_delete
    git clone cadmpeg-fork.bundle cadmpeg-fork          # the fork, with its 3 commits
    git add -A && git commit -m "Benchmark harness, decoder via cadmpeg fork, overnight fixes and report"

`_to_delete/` holds a partial clone of the fork that failed because of the same lock-file restriction.
