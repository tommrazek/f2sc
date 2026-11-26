# f2sc: Fusion `.f3d` / `.f3z` to SolidCraft

f2sc converts an Autodesk Fusion design file into a [SolidCraft](https://github.com/tangency-labs/solidcraft)
command script that rebuilds the design **feature by feature**: sketches, extrudes, revolves,
holes, threads, fillets, chamfers, patterns, mirrors, combines. The result opens in SolidCraft
with an editable timeline, not as a dead imported shape. A Fusion assembly archive (`.f3z`)
becomes one script per part plus a root script that places the parts.

It is an experimental project. It runs on Linux and macOS.

**Disclaimer.** This is a personal project. It has no affiliation with Autodesk, Inc. or Fusion 360, and Autodesk does not endorse it. It is also not affiliated with SolidCraft, and the SolidCraft developers do not currently endorse it.

```
  design.f3d ──► bin/f3d2sc-decode ──► design IR (JSON) ──► f3d2sc.py ──► design.solidcraft.json ──► solidcraft-cli ──► design.sc
               (Rust, built on cadmpeg)                      (Python)      (command script)                             (open in the app)
```

1. **Decoder** (`decoder/`, Rust). Reads the `.f3d` with
   [cadmpeg](https://github.com/cadmpeg/cadmpeg), an open-source CAD file decoder, and writes its
   neutral description of the design as JSON. It also writes Fusion's final shape as STEP, which
   the checks compare against.
2. **Converter** (`f3d2sc.py`, Python standard library only). Walks the Fusion timeline and
   writes SolidCraft commands.
3. **SolidCraft CLI** (`solidcraft-cli`). Optionally replays the script to check every feature
   against Fusion and saves a `.sc` design you open in the SolidCraft app.

---

## Contents

1. [Set-up guide](#1-set-up-guide), from an empty folder to a working converter
2. [Your first conversion](#2-your-first-conversion)
3. [Using the converter](#3-using-the-converter)
4. [Keeping cadmpeg up to date](#4-keeping-cadmpeg-up-to-date)
5. [Benchmarks and regression checks](#5-benchmarks-and-regression-checks)
6. [Troubleshooting](#6-troubleshooting)
7. [How it works](#7-how-it-works)
8. [Benchmark results](#8-benchmark-results)
9. [SolidCraft issues found](#9-solidcraft-issues-found)
10. [Further documents](#10-further-documents)

---

## 1. Set-up guide

### 1.1 What you need

| Tool | Version | How to get it |
|---|---|---|
| git | any recent | Linux: your package manager (`sudo apt install git`). macOS: `xcode-select --install`. |
| Python | 3.10 or newer | Linux: usually installed (`python3 --version`). macOS: `xcode-select --install` or python.org. No extra packages are needed. |
| Rust | stable | Install with [rustup](https://rustup.rs): `curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \| sh`, then open a new terminal. Check with `cargo --version`. |
| SolidCraft CLI | 0.40.1 (the benchmarks were measured with it; newer versions should work) | From [the 0.40.1 release](https://github.com/tangency-labs/solidcraft/releases/tag/v0.40.1). Linux: download `solidcraft-0.40.1-linux-x86_64.tar.gz` (or `-aarch64`), unpack it anywhere; the CLI is `bin/solidcraft-cli` inside. macOS: install the SolidCraft app; the CLI is `/Applications/SolidCraft.app/Contents/MacOS/solidcraft-cli`. |
| Disk and memory | about 3 GB free disk, 4 GB RAM | The first decoder build compiles cadmpeg. |
| GitHub CLI (`gh`) | optional | Only for working on cadmpeg pull requests. |

The converter itself runs without SolidCraft. You need SolidCraft only for `--check` and `--build`
and to open the results.

### 1.2 The folder layout (important)

This repository and the cadmpeg clone must sit **side by side in the same parent folder**. The
decoder finds cadmpeg at `../cadmpeg`, relative to this repository:

```
some-folder/
├── f2sc/            ← this repository
│   ├── decoder/     (builds against ../../cadmpeg/crates/*)
│   ├── bin/         (the built decoder goes here)
│   └── f3d2sc.py
└── cadmpeg/         ← the cadmpeg clone, NOT inside f2sc
```

Do not clone cadmpeg inside `f2sc`. If you did, see [Troubleshooting](#6-troubleshooting).

### 1.3 Step by step

Run these from an empty folder, e.g. `~/projects`. Each block is one step.

**Step 1. Clone this repository.**

```bash
git clone https://github.com/tommrazek/f2sc
```

(If you got this repository another way, e.g. as a copy under another name, use that folder
instead of `f2sc` in the commands below.)

**Step 2. Clone cadmpeg next to it**, from the same parent folder (not from inside `f2sc`):

```bash
git clone https://github.com/cadmpeg/cadmpeg
```

This downloads about 220 MB.

**Step 3. Check out the tested cadmpeg commit.** Every fix this converter needed is merged
upstream (see [CADMPEG-PRS.md](CADMPEG-PRS.md)), so plain cadmpeg is enough. Use the commit the
converter was last tested with:

```bash
git -C cadmpeg checkout --detach fd3cdb8c49
```

The `-C cadmpeg` makes git act on the `cadmpeg` folder, so you stay in the parent folder. A newer
`main` usually works too, but cadmpeg changes its output format from time to time (CADIR v11
did, on 24 Nov); run the regression check (section 5) after moving to one.

**Step 4. Build the decoder.** Go into the repository, build, and copy the binary to `bin/`:

```bash
cd f2sc
```
```bash
(cd decoder && cargo build --release) && mkdir -p bin && cp decoder/target/release/f3d2sc-decode bin/
```

The first build takes 2–15 minutes depending on the machine (about 2 minutes on 12 cores);
later builds take one or two. On a machine with little
memory (under 8 GB), build with one job so it does not run out of memory:

```bash
(cd decoder && CARGO_BUILD_JOBS=1 cargo build --release) && mkdir -p bin && cp decoder/target/release/f3d2sc-decode bin/
```

**Step 5. Check the decoder.**

```bash
bin/f3d2sc-decode --version
```

It must print

```
f3d2sc-decode cadmpeg fd3cdb8c49be954dd00451b9b77b1f977225a897
```

The hash is the cadmpeg commit the decoder was built from (upstream `main` as of 25 Nov). One more fix, cadmpeg PR 11 (#2260, [CADMPEG-PRS.md](CADMPEG-PRS.md)), is open; until it is merged, a Component Insert that a joint moved after the insertion also shows as an unresolved extra occurrence (a note in the root's output, nothing placed for it). If it
prints another hash, the clone is on another commit; see [Troubleshooting](#6-troubleshooting).

**Step 6. Tell the commands where SolidCraft is.** The examples below write `SOLIDCRAFT` for the
CLI's path. Set it once per terminal:

```bash
# Linux (adjust to where you unpacked it)
export SOLIDCRAFT=~/opt/solidcraft-0.40.1-linux-x86_64/bin/solidcraft-cli
```
```bash
# macOS
export SOLIDCRAFT=/Applications/SolidCraft.app/Contents/MacOS/solidcraft-cli
```

Check it with `"$SOLIDCRAFT" --help`.

Set-up is complete.

---

## 2. Your first conversion

Run everything from inside `f2sc`. The repository ships sample designs in `benchmarks/`.

### 2.1 A single part, checked

```bash
mkdir -p out && python3 f3d2sc.py benchmarks/steel_test_articles.f3d -o out/steel.solidcraft.json --check "$SOLIDCRAFT"
```

This takes under a minute. It prints one `check <feature>: ok` line per feature and ends with

```
final shape: match: 2 bodies vs 2 in Fusion, 2/2 match, 0 not judged (volume within 1e-4, box within 1 um; ...)
f3d2sc: wrote out/steel.solidcraft.json (14 commands)
build: out/steel.sc ok
```

Both bodies match Fusion's final bodies (volume within 1e-4, bounding box within 1 µm). In `out/`
you get:

| File | What it is |
|---|---|
| `steel.solidcraft.json` | the SolidCraft command script |
| `steel.sc` | the script built into a SolidCraft design: **open this one in the app** |

Designs where some bodies are imported instead of rebuilt (7.3) also get one `.step` file per
imported body next to the script; the script needs them.

`--check` replays the script feature by feature, compares every body with the position Fusion
recorded, compares the end result with Fusion's final bodies, and builds the `.sc`.

### 2.2 A larger design, with components

```bash
python3 f3d2sc.py benchmarks/film_scanner.f3d -o out/film_scanner --build "$SOLIDCRAFT"
```

The film scanner has two components, so this writes one script per component
(`film_scanner.component_1.solidcraft.json`, `film_scanner.component_2.solidcraft.json`), a root
script that places them (`film_scanner.solidcraft.json`), and the built `.sc` files. The output
ends with

```
build: film_scanner.component_1.sc ok
build: film_scanner.component_2.sc ok
build: film_scanner: 7 bodies, 453455.800 mm3 in all
```

Open `out/film_scanner.sc` in the app.

`--build` builds without the slow per-feature check; add `--check "$SOLIDCRAFT"` too for the full
check (several minutes).

### 2.3 An assembly (`.f3z`)

```bash
python3 f3d2sc.py benchmarks/test_asy.f3z -o out/test_asy --build "$SOLIDCRAFT"
```

An `.f3z` gives a folder: one script and `.sc` per linked design (`Side_Panels.sc`,
`Body_Panels.sc`, `Bottom_Panels.sc`), the root design's own `test_asy.root.sc`, and the assembly
`test_asy.sc` that places them. The output ends with `build: test_asy: 28 bodies, ...`. Open
`out/test_asy/test_asy.sc`.

### 2.4 Opening results in the SolidCraft app

Open the `.sc` files. The `*.solidcraft.json` files are command scripts, not designs; the app
refuses them ("missing field `name`"). To build a script by hand, run from the script's own
folder (paths inside scripts are relative):

```bash
cd out && "$SOLIDCRAFT" run steel.solidcraft.json --save steel.sc
```

For an assembly, build the part scripts first, then the root script.

---

## 3. Using the converter

```
python3 f3d2sc.py DESIGN.f3d|DESIGN.f3z [-o OUT] [options]
```

| Option | Meaning |
|---|---|
| `-o, --out PATH` | Script to write (default `<input>.solidcraft.json`). For an `.f3z` or a design with components: the output folder or base name. |
| `--check CLI` | Replay with `solidcraft-cli` feature by feature, compare each feature with Fusion's body positions and the end result with Fusion's final bodies, then build the `.sc`. Slow but thorough. A feature SolidCraft refuses goes through the fallbacks (7.3). |
| `--build CLI` | Build each script into a `.sc` without the per-feature check. Each script is still run once, and a refused feature gets the same fallbacks as under `--check`. A part that fails to build is left out of the root assembly with a warning. |
| `--partial` | Write the script up to the first feature that cannot be converted. Without it, such a gap writes nothing and exits with code 2. |
| `--until NAME` | Stop before the Fusion feature called NAME (its timeline name). |
| `--report FILE` | Write gaps, notes, imports and check results as JSON. |
| `--diagnose FILE` | Write a redacted report you can share for a design you cannot share (7.6). |
| `--step FILE` | Also write Fusion's final shape as STEP. |
| `--solidcraft VERSION` | The SolidCraft version the scripts are for, e.g. `0.49.1`. Default: the version of the `--check`/`--build` CLI, else 0.40.1. From 0.49 the scripts use exact splines and skip the workarounds for SolidCraft issues fixed there (section 9); such scripts need 0.49 or newer. |
| `--spline-tol MM` | How far a refitted dense spline may deviate (default 0.0001 mm). |
| `--no-joints` | Leave out assembly joints (otherwise made with `--build`, 7.4). |
| `--no-constraints` | Leave out sketch constraints, dimensions and user parameters (otherwise carried over with `--check` or `--build`, 7.4). |
| `--no-refit` | Keep dense splines as they are (SolidCraft may refuse to extrude them). |
| `--overrides FILE` | Explicit answers for what the file leaves open (see `inference.py`). |
| `--no-infer NAME` | Switch off one named inference rule (repeatable). |
| `--list-inference` | List the inference rules and exit. |
| `--decoder PATH` | Use another decoder binary (default `bin/f3d2sc-decode`; or set `F3D2SC_DECODER`). |
| `--ir FILE`, `--source FILE` | Convert from an IR JSON dumped by the decoder instead of decoding (`--source` names the `.f3d` it came from). |

Exit codes: 0 when every script was written (and built, with `--build`), 2 when a gap stopped the
conversion or a build failed.

### Inference rules

Where the file is silent, the converter guesses only through **named rules**. Each one is listed
in the report under `assumptions` where it fired, and can be switched off with
`--no-infer NAME`. `python3 f3d2sc.py --list-inference` prints them all with a full description:

| Rule | In short |
|---|---|
| `freeze_from_final_brep` | A feature that cannot be converted: its bodies are imported from Fusion's final shape (non-editable). |
| `skip_unconvertible` | A feature that only changes existing bodies and can be neither converted nor imported is left out. |
| `unvalidated_on_self_check` | A feature SolidCraft's kernel self-check refuses runs with validation off around it. |
| `split_threaded_cut_tool` | A cut by a threaded body cuts by the tool's two halves (works around SolidCraft issue 3). |
| `bore_widened_by_cut` | A bore SolidCraft refuses to widen by offsetting its face is widened by a cylinder cut. |
| `chamfer_side_by_final_shape` | With `--check`: a chamfer is flipped when that makes the final shape match Fusion's. |
| `hide_history_only_bodies` | Kept, hidden Combine tool bodies are hidden at the end, as in Fusion. |
| `region_by_arrangement`, `stale_region_left_out`, `revolve_region_from_initial_loop`, `revolve_regions_by_final_volume`, `profile_ids_by_rank` | Which sketch regions a feature uses, when the stored selection does not say it directly. |
| `revolve_axis_through_body_centre`, `revolve_sketch_from_records` | A Revolve the decoder leaves unresolved. |
| `thread_face_by_radius`, `thread_start_at_axis_end`, `thread_record_by_strings` | Threads the decoder resolves only in part. |
| `primitive_placed_by_body_centre` | Box and cylinder primitives, whose placement the decoder does not give. |
| `insert_occurrences_from_display_poses` | Component Inserts cadmpeg leaves unbound while the linked designs' occurrences sit unplaced at the origin: paired in timeline order with those occurrences in the archive's reference order (only when the counts agree); each goes to the pose Fusion's display scene stores for its insert record, i.e. where its joints put it. |
| `screw_occurrences_from_component_paths` | A Screw (fastener) feature cadmpeg gives no occurrence: one occurrence of the screw design its library data names, at the pose stored for it. |
| `insert_pose_from_component_path` | A Component Insert's occurrence takes the current pose stored for it where cadmpeg's differs. |
| `owner_from_body_evidence`, `split_piece_by_final_side`, `no_geometry_by_absence` | Which component a feature belongs to, which piece a Remove Body removes, features that make no geometry. |

### Converting a design you cannot share

```bash
python3 f3d2sc.py secret.f3d -o out/secret.json --check "$SOLIDCRAFT" --diagnose secret.report.json
```

`--diagnose` writes a redacted JSON report, also when the conversion stops or decoding fails. It
holds structure and failure reasons only: every feature as `F<position>` with its type and status,
counts of sketch entities, the converter's reasons, SolidCraft's errors, centre differences in
mm (never positions), raw record layouts (never values). Names become placeholders, file names
and ids are masked, and every number in free text becomes `<x>`. Read it before sharing.

---

## 4. Keeping cadmpeg up to date

All ten fixes made for this converter are merged into `cadmpeg/cadmpeg`
([CADMPEG-PRS.md](CADMPEG-PRS.md) lists them), the last on 21 Nov. The decoder builds from plain
upstream cadmpeg.

**The stale-decoder warning.** The decoder records the cadmpeg commit it was built from. Before
each conversion the converter compares it with `../cadmpeg`'s current commit and prints

```
f3d2sc: WARNING: bin/f3d2sc-decode is built from cadmpeg <hash>, but ../cadmpeg is at <hash>. Rebuild the decoder (README, Set-up).
```

when they differ. A stale decoder decodes less, so rebuild whenever you change what
`../cadmpeg` has checked out:

```bash
(cd decoder && cargo build --release) && cp decoder/target/release/f3d2sc-decode bin/
```

**Moving to newer upstream work** (run from inside `f2sc`):

```bash
git -C ../cadmpeg fetch origin && git -C ../cadmpeg checkout --detach origin/main
```

then rebuild and run the regression check (section 5) against the decoder you had.

**Output formats.** cadmpeg's JSON changed shape with CADIR v11 (24 Nov): the design and
geometry moved out of `model` into their own sections, some design arenas are left out of the
JSON (the decoder writes them as `wire_omitted`), and many records gained a `{kind, value}`
wrapper. `ir_compat.py` turns a v11 document back into the shape the converter reads, so the
converter takes the output of either. A decoder built before v11 (from the old `f3d2sc-next`
branch in `cadmpeg-f3d2sc.bundle`) still works.

---

## 5. Benchmarks and regression checks

**Benchmarks.** `bench.py` converts every `.f3d`/`.f3z` in `benchmarks/` with `--partial --check`
and writes a table to `OUTDIR/summary.md` (about 20 minutes on 12 cores):

```bash
python3 bench.py /tmp/bench --sc "$SOLIDCRAFT"
```

`--only NAME ...` restricts it to some files (names without extension). The controlled test
designs in `benchmarks/dummies/` run separately:

```bash
python3 bench.py /tmp/bench-dummies --sc "$SOLIDCRAFT" --benchmarks benchmarks/dummies
```

Compare the result with [section 8](#8-benchmark-results).

**Decoder regression check.** After any change in `../cadmpeg`, keep the old binary, rebuild, and
compare what the two builds decode on every benchmark design:

```bash
cp bin/f3d2sc-decode /tmp/f3d2sc-decode.old
```
```bash
(cd decoder && cargo build --release) && cp decoder/target/release/f3d2sc-decode bin/ && python3 decode_diff.py /tmp/f3d2sc-decode.old bin/f3d2sc-decode
```

It must end with `0 regression(s)` (a feature that resolved before and does not now).

---

## 6. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `fatal: refusing to fetch into branch 'refs/heads/...' checked out` | Detach first, then fetch (section 4). |
| `fatal: '../f2sc/cadmpeg-f3d2sc.bundle' does not appear to be a git repository` | You ran the command from the wrong folder, or cadmpeg is cloned inside `f2sc`. If `f2sc/cadmpeg` exists, move it out: from inside `f2sc` run `mv cadmpeg ../cadmpeg`, then `cd ..` and repeat step 3. |
| `error: failed to load manifest for dependency 'cadmpeg-codec-f3d'` ... `failed to read .../cadmpeg/crates/cadmpeg-codec-f3d/Cargo.toml` during the build | The decoder found no `../cadmpeg`: wrong layout (see 1.2). Fix the layout, then rebuild. |
| `bin/f3d2sc-decode --version` prints `cadmpeg unknown` | `../cadmpeg` is not a git clone (e.g. a downloaded zip), so the build cannot record its commit. Clone it with git (step 2). |
| `--version` prints a different hash | `../cadmpeg` is on another commit. `git -C ../cadmpeg rev-parse HEAD` should start with `fd3cdb8c49`; check it out (step 3) and rebuild. |
| `f3d2sc: WARNING: ... predates the version stamp` or `... is built from cadmpeg X, but ../cadmpeg is at Y` | The decoder is out of date; rebuild (section 4). |
| The build is killed, or `signal: 9, SIGKILL` | Out of memory: build with `CARGO_BUILD_JOBS=1`. |
| `error: rustc ... is not supported` or `rust-toolchain` errors | cadmpeg pins its Rust version in `rust-toolchain.toml`; rustup installs it on the first build. Without rustup (a distribution's `cargo`), install rustup. |
| `solidcraft-cli: command not found`, or `--check` does nothing | Give the full path (step 6). On macOS the CLI is inside the app bundle, not on `PATH`. |
| `No such file` for `benchmarks/x.f3d` | Check the extension: assemblies are `.f3z`, single designs `.f3d`. |
| The app says `missing field 'name'` | You opened a `.solidcraft.json` script; open the `.sc` (2.4). |
| The root `.sc` of an assembly has 0 bodies, or parts are missing | A part failed to build: look for `build: ... failed` and the `<root>.without-failed.solidcraft.json` warning. A stale decoder also causes it. |
| `solidcraft-cli run` cannot find a `.step` or `.sc` | Run scripts from their own folder: their paths are relative. |
| The app says a part "couldn't be made into a solid and came in as surfaces", naming faces where "pcurves failed" | A body imported from STEP (a base feature, or a feature that was frozen) whose splines SolidCraft cannot read, usually a modelled thread's (section 9, issue 9). Convert again with the current f2sc: it thins those splines. If it persists, send the message: it names the faces. |

---

## 7. How it works

### 7.1 Decoder

`decoder/` is a small Rust program on cadmpeg's crates. `bin/f3d2sc-decode in.f3d out.ir.json
[out.step [i,j]]` writes cadmpeg's neutral IR as JSON, extracts the design's raw streams into
`out.ir.json.streams/`, and optionally writes Fusion's final bodies as STEP. `build.rs` stamps
the cadmpeg commit into the binary (`--version`).

cadmpeg keeps a written specification of the Fusion format (`docs/formats/f3d.md`) and a
register of open questions (`docs/formats/f3d-open-items.md`). Fixes to the decoding go into
cadmpeg, with specification and tests, as pull requests; see
[CONTRIBUTIONS.md](CONTRIBUTIONS.md). They state format facts only: what the file says, never
guesses and never a fix for one file.

### 7.2 Converter

`f3d2sc.py` walks the Fusion timeline and writes SolidCraft commands, adapting to what SolidCraft
can and cannot do (thread faces grown to the designation's size, dense splines refitted, sketch
regions picked by points). `planar.py` computes sketch regions; `fusion_raw.py` joins the
decoder's parameter tables by feature and role; `inference.py` holds the named rules;
`joints.py` rebuilds assembly joints (7.4); `constraints.py` carries sketch constraints, dimensions and user parameters over (7.4);
`step_thin.py` thins the dense thread splines of the STEP files it imports (section 9, issue 9).

**Bodies** are named after Fusion's per-component body numbers: `Body 3` in component 1,
`C2 Body 3` in component 2. Fusion stores lengths in cm and angles in radians; scripts use mm and
degrees.

### 7.3 When a feature does not convert

In order, the converter:
1. imports the bodies the feature makes or changes from Fusion's final shape, together with the
   later features that change them (`freeze_from_final_brep`), if those bodies import as closed
   solids (`file.insert_step`; such bodies are not editable);
2. otherwise, for a feature that only changes existing bodies, leaves it out
   (`skip_unconvertible`; listed under `skipped` in the report);
3. otherwise stops there (a gap; with `--partial` the script up to that point is written).

A feature SolidCraft refuses to run goes through the same steps. Before that, a feature whose
result SolidCraft's kernel self-check refuses is retried with validation off around it
(`unvalidated_on_self_check`), and a feature after which SolidCraft has lost a body Fusion keeps
counts as refused.

### 7.4 What is mapped

- **Sketches** (planar): lines, arcs, circles, points, construction geometry and splines. For
  SolidCraft 0.49 or newer splines are written exactly (`sketch.spline.control_knots`, degree
  1–9). For older versions only degree-3 or degree-5 clamped uniform splines are taken, and
  dense or non-uniform ones are refitted by least squares (default 0.1 µm; Fusion's involute gear
  flanks go from 245 control points to 8).
- **Sketch constraints, dimensions and user parameters** (`constraints.py`), with `--check` or
  `--build` only, since each sketch is checked in SolidCraft. User parameters come first in the
  script (`parameters.add`). Horizontal, vertical, coincident (also with a curve's end),
  parallel, perpendicular, tangent, equal, concentric, collinear, midpoint, symmetric and fix
  become `sketch.constraint.*`. Distance, horizontal and vertical distance, radius, diameter and
  angle dimensions become `sketch.dimension`, valued by the Fusion expression when it uses only
  user parameters and arithmetic (a screw's `adsk_nomdia * 0.51`), otherwise by the value.
  SolidCraft gives the dimensions its own parameter names (`d1`, `d2`, …). Each sketch is first
  run alone as drawn, then with its constraints. A constraint SolidCraft refuses, or one that
  moves the sketch by more than 0.1 µm, is left out; the notes count them. Fusion's offset,
  polygon and native dimension records are not mapped. `--no-constraints` leaves all of this
  out. The idea, and keeping only constraints that leave the sketch in place, come from
  [f3z-to-solidcraft](https://github.com/queball1999/f3z-to-solidcraft); no code is taken from it.
- **Profiles** are picked by points inside each region, not by curve ids, because Fusion and
  SolidCraft split overlapping curves differently. Nested loops belong to the region around them.
- **Extrude**: one side, two sides or symmetric; blind or through all; start offset; taper;
  new/join/cut/intersect; profiles from body faces. A two-sided extrude with a taper on one side
  becomes two extrudes.
- **Revolve**, **Hole** (simple, counterbore, countersink; blind or through; tip angle),
  **Thread** (ISO metric, modeled, with length; Fusion's tolerance class has no SolidCraft
  field), **Fillet**, **Chamfer** (equal distance, two distances, distance and angle).
- **Combine** (join, cut, intersect, keep tools), **Move**, **Remove Body**, **Split Body**.
- **Mirror** of bodies or features, about a plane from the file or a construction plane.
- **Circular pattern** of features or bodies.
- **Primitives**: box and cylinder (placed by their body's centre), sphere (`solid.sphere`, at
  its decoded centre).
- **Work planes** parallel to a principal plane.
- **Components and assemblies**: one script per component or linked design; the root script
  inserts each occurrence and moves it to its current pose (`occurrence.move` with a matrix).
  Nested occurrences are flattened to their placement in the root. Joints, joint origins and
  component inserts make no geometry and are left out with a note; parts placed by joints or by
  Fusion's Screw (fastener) feature get their current pose from the file. Each Screw feature
  gets its own placed screw (`screw_occurrences_from_component_paths`). A linked design brought in by a Component Insert that cadmpeg leaves unbound gets its
  displayed pose (`insert_occurrences_from_display_poses`). **Joints** (`joints.py`, with
  `--build`): fixed, slider, revolute, cylindrical, ball, planar and pin-slot joints are made where
  the parts stand (`joint.as_built`) at the second connector's frame. A slider's or cylindrical
  joint's linear limits are carried over, shifted by its current value; angular limits are not.
  In each group of joined parts the part most often joined to stays grounded, the others move
  with their joints, and parts in no joint stay grounded. A joint SolidCraft refuses or finds in
  conflict is left out when the assembly is built. Relation joints (distance, angle, parallel, …)
  are not mapped. `--no-joints` leaves joints out. Occurrences hidden in
  Fusion are hidden (`browser.visibility`), so the result shows what Fusion shows.
- **Kept tool bodies** that Fusion hides are hidden at the end of the script.

### 7.5 Checks

`--check` replays the script one feature at a time and compares every affected body's
bounding-box centre with the centre Fusion recorded (centres Fusion marks as approximate may
differ by up to 2 mm). It then compares the whole result with Fusion's final bodies: body count
and, per body, volume (within 1e-4) and bounding box (within 1 µm). "Not judged" means one side
is not a closed solid in SolidCraft (Fusion's modeled thread surfaces come through as fitted,
open surfaces).

### 7.6 The diagnose report

See [3. Converting a design you cannot share](#converting-a-design-you-cannot-share).

---

## 8. Benchmark results

SolidCraft 0.40.1 and 0.49.1, decoder built from upstream cadmpeg `fd3cdb8c49` (CADIR v11) plus PR 11,
25 Nov; rerun 26 Nov with sketch constraints and thinned STEP files, with the same results. Both
versions give the same results (0.49.1 with exact splines and without the threaded-cut
workaround). Every design converts with no gaps. Imported = features brought in from Fusion's final shape instead of rebuilt.

| File | Design | Imported | Skipped | Final shape |
|---|---|---|---|---|
| `steel_test_articles.f3d` | | 0 | 0 | 2/2 match |
| `film_scanner.f3d` | component 1 | 0 | 0 | 2 bodies (cadmpeg now reads the Combines' Keep Tools off), not judged (threaded holes; the reference now closes but lacks thread surfaces, ISSUES F6); frame body 1 is 77,868 mm³ (Fusion: 77,137–78,755 mm³), frame body 4 is 64,813 mm³ (Fusion: 64,300–66,141 mm³) |
| | component 2 | 4 | 0 | match |
| `test_asy.f3z` | Side / Body / Bottom Panels | 0 / 0 / 1 | 0 | 18/18, 4/4, 2/2 match; the assembly builds to 28 bodies |
| `f2sc_screws.f3z` | plate, M3 × 12 screw | 0 / 4 | 0 | 1/1, 1/1 match; the assembly builds to 4 bodies: the plate (moved by a joint after its insertion) at the origin and three screws, one per Screw feature, in its holes |
| `dummies/threads.f3d` | 10 threads, one per Fusion setting | 3 (a turned box primitive and the 2 features on it) | 0 | 7/12 match, 4 not judged (modeled threads), 1 lacks a cosmetic thread with an offset |

`benchmarks/dummies/` also holds the controlled designs `dr340-regions-*.f3d` and
`dr341-chamfer-*.f3d`, used to settle cadmpeg format questions (DR-340, DR-341); they are decoded
by `decode_diff.py`.

---

## 9. SolidCraft issues found

Found with SolidCraft 0.40.1. Reproductions are in `solidcraft-issues/`; run one with
`"$SOLIDCRAFT" eval solidcraft-issues/<file>`. None was reported upstream
([SOLIDCRAFT-REPORTS.md](SOLIDCRAFT-REPORTS.md) is the draft). Rechecked with **0.49.1** on 25 Nov:
issues 1, 2, 3 and 8 are fixed there (each reproduction builds, with the target kept), and the
converter leaves out its workarounds for them when it writes for 0.49 or newer (`--solidcraft`, or the version of
the `--check`/`--build` CLI). Issues 5 and 6 come from a private design and were not rechecked.

1. **Extruding a dense degree-5 control spline fails** (`01-dense-spline-extrude.json`). 245
   control points 0.1–15 µm apart extrude into a surface SolidCraft's self-check refuses
   (`SmSurfOfExtrusion::Test #8 Origin`). The converter refits dense splines. *Fixed in 0.49.1.*
2. **Mirroring a threaded shaft about a plane through its axis and joining it fails the
   self-check** (`02-film-scanner-mirror-join-threaded-shaft.json`; `Edgeuses out of tolerance`).
   The current conversion no longer hits it. *Fixed in 0.49.1.*
3. **Cutting with a threaded shaft silently deletes the target body**
   (`03-minimal-threaded-cut-deletes-target.json`, `03-film-scanner-cut-by-threaded-shaft.json`;
   without the thread: `03b-...`). The combine reports success, but the target is gone; it depends
   on where the thread leaves the target. The converter cuts by the tool's two halves
   (`split_threaded_cut_tool`). *Fixed in 0.49.1; the workaround is used only for older versions.*
4. **No way to make separate bodies of a body's disjoint pieces.** The converter rebuilds such
   mirrors as body mirrors.
5. **Offsetting a small bore's face fails the self-check** (`Loop Orientation`), at any
   distance. Found on a private design; reproduction not included. The converter widens the bore
   with a cylinder cut (`bore_widened_by_cut`).
6. **A cylinder cut whose end cap touches the end of the face it widens deletes the target**,
   with no error. Found on the same private design; reproduction not included. The converter runs
   the cut 1 µm past each end.
7. **A modeled thread larger than its shaft is refused**; Fusion builds the thread at its
   designation's size. The converter offsets the face first.
8. **Scripts take clamped uniform splines only**; non-uniform ones are refitted. *0.49 adds
   `sketch.spline.control_knots` (any knots, degree 1–9); the converter writes splines exactly with it.*
9. **A body with a modelled thread imports from STEP as open surfaces** (0.49.1; found 26 Nov on a
   private design, reproduction not included). cadmpeg writes a helix as cubic Hermite segments and
   doubles their number until the fit is far inside its tolerance: 1024 segments for a 14-turn
   thread. SolidCraft refuses such an edge on a cylinder ("an edge of a face that winds round its
   axis, whose curve has too many knot spans"), or, in another build, the helical flank ("a loop
   that jumps across its spline surface's u range"), and leaves those faces out. This hits a
   thread in a base feature or in any body the converter imports. The converter thins those
   splines in every STEP it writes (`step_thin.py`): it merges neighbouring segments while the
   result stays within 0.1 µm of cadmpeg's curve or surface. In the case found, 1024 segments
   become 512 and the body imports as a closed solid. 0.40.1 cannot import it either way.

---

## 10. Further documents

| File | What it holds |
|---|---|
| [CONTRIBUTIONS.md](CONTRIBUTIONS.md) | The cadmpeg fixes and their branches, and the open cadmpeg format questions with evidence. |
| [CADMPEG-PRS.md](CADMPEG-PRS.md) | The texts of the cadmpeg pull requests and their state. |
| [ISSUES.md](ISSUES.md) | Every issue found on the benchmark designs, where it lies, and the commit that fixed it. |
| [HANDOFF-cadmpeg-session.md](HANDOFF-cadmpeg-session.md) | Brief for a focused session on the open cadmpeg items. |
| [SOLIDCRAFT-REPORTS.md](SOLIDCRAFT-REPORTS.md) | Draft reports of the SolidCraft issues. |
| [TODO.md](TODO.md) | Smaller open items. |
| `REPORT-*.md` | Dated status reports. |
