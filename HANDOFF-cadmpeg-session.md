# Hand-off: focused session on the open cadmpeg items (written 15 Nov 2026)

You are picking up the format questions that block the f3d → SolidCraft converter. Everything
you need is in this file and the files it names. Read this file fully before touching code.

## Goal

Settle as many of the open items below as the evidence allows, **in cadmpeg** (the decoder),
as pull-request-ready changes. Where the files at hand cannot settle an item, write the evidence
into cadmpeg's open-items file and say which controlled Fusion save would settle it. Never guess.

## Setup (Linux workstation; paths as on this machine)

- `~/cadmpeg2sc`: this repository. Converter `f3d2sc.py` (Python, stdlib only), named
  inferences `inference.py`, decoder wrapper `decoder/` (Rust), benchmarks `benchmarks/`.
- `~/cadmpeg`: a clone of github.com/cadmpeg/cadmpeg **beside** this repo (the decoder builds
  `../../cadmpeg/crates/*`). Remotes: `origin` = cadmpeg/cadmpeg, `fork` = tommrazek/cadmpeg.
  Branches:
  - `f3d2sc` (checked out): upstream `cd06f603a5` + our fixes. **The decoder builds from this.**
  - `f3d/<topic>` ×8: our open pull requests #1357–#1364, on upstream `29f0038579`.
  - `f3d2sc-next`: upstream `29f0038579` + all eight. The converter is not yet adapted to it
    (see task 6).
- Build the decoder: `(cd decoder && cargo build --release) && cp decoder/target/release/f3d2sc-decode bin/`
  (~2 min incremental). `bin/f3d2sc-decode --version` prints the cadmpeg commit; the converter
  warns if it differs from `../cadmpeg`'s HEAD.
- Decode one file: `bin/f3d2sc-decode in.f3d out.ir.json` (writes `out.ir.json.streams/` with
  the raw `BulkStream.dat` next to it).
- Regression gate for any cadmpeg change: keep the old binary, rebuild, then
  `python3 decode_diff.py OLD_DECODER bin/f3d2sc-decode` must report **0 regressions** (it
  covers `benchmarks/` and `benchmarks/dummies/`).
- Converter benchmark: `python3 bench.py OUTDIR --sc /home/rocm6/opt/solidcraft-0.40.1-linux-x86_64/bin/solidcraft-cli`
  (~20 min), plus `--benchmarks benchmarks/dummies`. Expected rows are in README.md
  ("Benchmarks"). Don't edit `f3d2sc.py` while it runs.
- Disk: the home disk is nearly full and `/tmp` is a 28 GB tmpfs. Put cargo target dirs for
  extra worktrees in one shared place and delete them when done.

## Rules (binding)

1. **cadmpeg's own rules first:** read `~/cadmpeg/CONTRIBUTING.md`, `LEGAL.md`, `AGENTS.md`,
   `docs/formats/f3d.md` (the spec) and `docs/formats/f3d-open-items.md` before
   reverse-engineering. Specs state settled byte semantics only; unknowns go in the open-items
   file (Question / Known / Need / Conflict / Note only; delete an item in the same change that
   settles it). Run `python3 scripts/preflight.py --full` before calling a branch done: 0 FAIL.
2. **Format facts only:** no fix that fits one file, no guessed byte. One topic per branch,
   based on current upstream `main`, one commit (cadmpeg squash-merges).
3. **Privacy:** the chaser and film scanner designs are the user's private files. Never commit
   them or name them in cadmpeg; describe records by structure and offset only. No CAD binary
   goes into cadmpeg.
4. **Do not open pull requests or push** without the user's approval. Prepare the branch and a
   PR text in the style of `CADMPEG-PRS.md` (same provenance block, unticked sign-off box).
5. Converter changes (`f3d2sc.py`, `inference.py`) stay in this repo, each with its own commit
   on `main`, recorded in `ISSUES.md`. When a cadmpeg fix lands, remove the converter inference
   it replaces (named below) and confirm the benchmark rows.
6. Upstream moves fast (dozens of commits a day). Before starting an item, check
   `git log origin/main` and the open PRs (`gh pr list -R cadmpeg/cadmpeg`) for work on it.

## Tasks, in order

Evidence, record offsets and design ids for each are in `CONTRIBUTIONS.md`, section
"Open items (DRs) for a focused session".

**Controlled sample files supplied by the user** (in `benchmarks/dummies/`, made in Fusion for
this session; how each was built is in `CONTRIBUTIONS.md`, "Review for the focused session").
Files of a group differ only in the one setting named, so diff their records:

| File | Same model, varied setting |
|---|---|
| `dr340-regions-a.f3d` | sketch rectangle with a notch on its bottom edge, full-turn Revolve of the rectangle minus the notch |
| `dr340-regions-b.f3d` | the same, revolving the notch only |
| `dr340-regions-c.f3d` | the same, revolving both regions |
| `dr341-chamfer-cyl.f3d` | cylinder r 5, h 20; distance-angle chamfer 1 mm at 20° on the top edge |
| `dr341-chamfer-cyl-flip.f3d` | the same, flip pressed |
| `dr341-chamfer-box.f3d` | 20 mm cube; two-distance chamfer 1 / 3 mm on a top edge |
| `dr341-chamfer-box-flip.f3d` | the same, flip pressed |

If a file is missing, work from the Piston evidence alone and say which file would have
settled the rest. These are the user's own controlled designs: they may be described in a
cadmpeg PR (and offered for cadmpeg's corpus only with the user's OK).

0. **Screw feature placements** (chaser root assembly; `ISSUES.md` C12). The three M2 screws
   are placed by Fusion Screw features, which cadmpeg keeps native, so the converter uses their
   stale stored placements and the screws float outside their holes. Each Screw scope (Design
   records 662770, 682205, 686033 of the root `e8c9db61…f3d`) stores row-major 4×4 frames in
   cm: the screw's stored placement and frames at the hole axes (6.93, 10, 4), (0, 10, −8),
   (−6.93, 10, 4) mm, rotation x→x, y→−z, z→y. The final B-rep confirms those holes (clearance
   bore y 10–17, thread y 17–27). Find which frame a Screw scope makes current, and whether the
   scope names its hole; then let cadmpeg give the occurrence that pose (as it does for a
   Component Insert carrier). Done: the converted root places all three screws in holes.
1. **DR-340, compact region selection with further regions** (Piston Revolve 1).
   Converter stopgap: inference `revolve_region_from_initial_loop`. Samples: `dr340-regions-{a,b,c}.f3d`.
   - First check upstream #1346 (merged 15 Nov, "match profile entries to split curve pieces")
     and #1308: they may already bind the entry operands `(0,1,2)` / `(1,2,2)` that our evidence
     reads as (reversed, portion index, portion count).
   - Our data contradicts the rule upstream added to DR-340's Note (further regions = atomic
     regions of the selected region): here initial = further 1 − further 2. At minimum, add
     that as the item's **Conflict**, with structure and offsets.
   - Done: Piston converts with the inference switched off (`--no-infer revolve_region_from_initial_loop`)
     and still matches Fusion, or the open item states exactly what is missing.
2. **DR-341, which face takes a chamfer's distance** (Piston Chamfer 1).
   Converter stopgap: `chamfer_side_by_final_shape` (only under `--check`). Samples:
   `dr341-chamfer-{cyl,box}[-flip].f3d`; the field that differs within each pair is the side.
   Lead: the exact ASM transition keeps the inserted chamfer surface; its cone half-angle
   (20° from the axis here, not 70°) fixes the side for this case. Generalise only as far as the
   Need in DR-341 allows. Done: `flip_direction` is decoded (or proven undecidable from these
   files) and Piston matches without `--check`.
3. **Stack_Main Extrude 7**: the profile stays native because the transition proof in
   `crates/cadmpeg-codec-f3d/src/design/profile_select/face_profile_sets.rs` fails. Find which
   of its three conditions fails and whether the extra binding or row is a stored alternative.
   Extrude 7, Split 1, RemoveBody and Hole 1 are imported today.
4. **M2 screw (library part) Extrude 2**: "no unique neutral profile"; curve c240 is in no
   closed loop. Find out whether c240 is a projected or construction curve the profile should
   ignore. Extrude 2, Thread 3, Fillet 4 and Revolve 3 are imported today.
5. **Stack_Front_1 Fillet 3**: ~20 edge operands decode but bind to no historical edge; check
   whether they are edges an earlier thread or pattern rewrote. Fillet 3 is left out today.
6. **Converter on newer upstream** (`ISSUES.md`, N1; converter work, not cadmpeg): with
   `f3d2sc-next` the converter imports more (Piston Chamfer 2, Stack Main from Extrude 1, test_asy
   Body Panels Hole 13 and Side Panels Mirror 6). Likely causes: upstream #1313 (Box frame
   origin is now the corner) and #1308/#1298 (nested profile-region selections). Adapt the
   converter until the benchmark rows equal README's, then switch `~/cadmpeg` to `f3d2sc-next`
   and rebuild. Doing this early makes tasks 1–5 easier, since cadmpeg PRs target current main.
7. **DR-646, DR-327, DR-338** (Revolve direction kind 1; circular-pattern axes): no samples
   were made for these; leave the items as they are.

## When you finish

Update `CONTRIBUTIONS.md` (topics and open items), `ISSUES.md` (status and commits) and the
README benchmark table if rows changed. Report to the user: what was settled, which cadmpeg
branches are ready for a PR (with texts), which Fusion saves would settle the rest.
