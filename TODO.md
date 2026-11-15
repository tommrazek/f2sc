# To do

## Set up on another workstation

Nothing here is pushed to a remote yet: this repo has no `origin`, and the cadmpeg branch exists
only in the local `cadmpeg/` clone. Do these once, from the machine that has the work.

- [ ] Push this repo (branch `fable`, or `main` after merging it) to a remote the workstation can
      reach: `git remote add origin URL && git push -u origin fable`. The benchmark files
      (`benchmarks/`, including `dummies/threads.f3d`) are tracked and travel with it.
- [ ] The cadmpeg branches (`f3d2sc`, `f3d2sc-next` and the `f3d/*` pull-request branches)
      travel as `cadmpeg-f3d2sc.bundle`, tracked in this repo; it needs cadmpeg `main` at
      29f0038579 or later. The `f3d/*` branches are also on the fork `tommrazek/cadmpeg`
      (pull requests #1357–#1364). Recreate the bundle after every new commit in `../cadmpeg`
      (README, Decoder: cadmpeg checkout).

Then on the workstation (Linux x86_64/aarch64 with glibc 2.35+, or macOS):

1. Tools: git, Python 3.10+ (standard library only), a Rust toolchain (rustup, stable), and about
   4 GB of free memory for the build.
2. Clone this repo and, **beside it** (same parent folder), cadmpeg with its branches:

       git clone URL cadmpeg2sc
       git clone https://github.com/cadmpeg/cadmpeg cadmpeg
       git -C cadmpeg fetch ../cadmpeg2sc/cadmpeg-f3d2sc.bundle 'refs/heads/*:refs/heads/*'
       git -C cadmpeg checkout f3d2sc

   A machine with the older layout (`cadmpeg/` inside this repo): move that folder out of the
   repo or delete it, set up `../cadmpeg` as above, then rebuild the decoder (step 3). A stale
   `bin/f3d2sc-decode` shows as "Mirror 4: mirror plane not found" and "0 commands placing 2
   components" on the film scanner, and the converter warns about it.

3. Build the decoder (10 to 15 minutes; one job, or it runs out of memory on small machines)
   and put it where the converter looks for it:

       cd cadmpeg2sc
       (cd decoder && CARGO_BUILD_JOBS=1 cargo build --release)
       mkdir -p bin && cp decoder/target/release/f3d2sc-decode bin/

4. SolidCraft 0.40.1 CLI: download the archive for the platform from
   https://github.com/tangency-labs/solidcraft/releases/tag/v0.40.1
   (`solidcraft-0.40.1-linux-x86_64.tar.gz`, `...-linux-aarch64.tar.gz`, or
   `solidcraft-cli-0.40.1-macos-universal.zip`), unpack it, and note the path of
   `bin/solidcraft-cli`.
5. Check the set-up. The first converts one file; the second runs every benchmark with the
   per-feature and final-shape checks (about 45 minutes on 2 cores) and should print the table in
   the README, with no gaps:

       python3 f3d2sc.py benchmarks/steel_test_articles.f3d -o /tmp/steel.solidcraft.json --check PATH/solidcraft-cli
       python3 bench.py /tmp/bench --sc PATH/solidcraft-cli

6. After any change in `../cadmpeg`: rebuild, then `python3 decode_diff.py OLD_DECODER NEW_DECODER`
   must report 0 regressions (keep a copy of the previous `bin/f3d2sc-decode` as OLD).

Not needed on the workstation: `cadmpeg-fork/` (the earlier fork, unused), `bin/` and
`decoder/target/` (built), `out/`, the other `*.bundle` files, and Fusion itself (only for making
new test designs like `benchmarks/dummies/threads.f3d`).

Done 14 Nov (see REPORT-14nov.md): set up and verified on this machine; cadmpeg rebased on
upstream `main` (64 commits) with every branch commit completed for a pull request; Mirror 4's
plane decoded (cadmpeg: origin classification, #1232 now applies), Mirror 4 and Combine 5
converted; Combine 4 left out because of SolidCraft issue 3; the kept tool bodies are hidden.
Still open from that start: Combine 4 (SolidCraft), and one sketch of component 2's Extrude 3 is
not emitted (imported instead).

## SolidCraft issues (see README)
- [x] Cut issues 2 and 3 down: issue 3 has a 4-command reproduction
      (`solidcraft-issues/03-minimal-threaded-cut-deletes-target.json`, position-dependent);
      issue 2 does not reproduce outside the old design and no longer occurs in the conversion.
- [ ] Report issue 3 upstream (needs your go-ahead: it posts under your account).
- [ ] Report issue 1 (dense degree-5 spline extrude) upstream with `solidcraft-issues/01-...`.
- [ ] Once 2 and 3 are fixed, re-run `--check` on the whole film scanner and compare the frame
      bodies with the reference STEP (they come out open from cadmpeg's STEP today, so the
      comparison needs volumes from SolidCraft's own import or from another source).

## Layers (the plan of 12 Nov)
- [x] Threads: decoded in the cadmpeg branch (record and faces), confirmed on
      `benchmarks/dummies/threads.f3d`; the converter no longer guesses the face by radius.
- [x] Raw-record reading: Moves, parameters by role and CopyPaste now come from the decoder
      (cadmpeg `main` already decoded them; the old fork was cut from the 0.6.11 crate).
- [ ] Still read from raw bytes in `fusion_raw.py`: a circular pattern's axis and count where the
      decoder leaves the pattern unresolved (Chaser's three C-Patterns: cadmpeg DR-327, DR-338
      need controlled saves), and thread strings as a fallback.
- [x] Mirror planes: cadmpeg now decodes the film scanner's four and test_asy Side Panels'
      Mirror 4 (origin classification and the plane-member tie-break). Side Panels' first Mirror
      1 (class-303 scope, a surface mirror the converter leaves out) has no decoded construction.
- [ ] Thread: the u32 after the full-length flag (1 or 2) is read but not understood (DR-142);
      which end a thread of a set length starts from is inferred (`thread_start_at_axis_end`);
      SolidCraft's thread has no offset from the end and no tolerance class, so a modeled thread's
      crest is at the nominal diameter where Fusion's is at the class's (M10x1.5 6g: 9.85 mm).
- [x] Rebase the cadmpeg branch `f3d2sc` on upstream `main` (14 Nov, cd06f603a5, no conflicts).
- [ ] Open pull requests for its topics (each now has spec, layout, open items and synthetic
      tests; the workspace suite, clippy, rustfmt and the source policy pass). Needs the DCO box
      ticked and the provenance statement of CONTRIBUTING.md, and a push to a fork: your call.
- [ ] Hand-off tasks not started (see HANDOFF-fable.md for each one's background and "done when"):
      3. feature ownership in assemblies (features made in the assembly's context land in the
         wrong component; `reassign_owners` patches it afterwards);
      4. extrude from a face and other face-recipe profiles (the fork now states the plane or
         cylinder of a historical face selection, which is the first half of this);
      5. sketch regions bounded by part of a longer curve (`planar.py` into cadmpeg's profile
         builder);
      6. lighter subset STEP (filter geometry carriers to the kept bodies in `keep_bodies`).
      Check cadmpeg `main` and its open-items register first: several may be done or documented.
- [ ] Split f3d2sc.py into a `solidcraft/` package (mapping and SolidCraft adaptations); the
      inference layer is already separate (`inference.py`).
- [ ] Final-shape check: the decoder writes a component's bodies with every other body's
      geometry carriers too (25 MB of STEP for one Chaser body); filter surfaces and curves to the
      kept bodies. Reference bodies with fitted thread surfaces are not closed in SolidCraft, so
      their volume is not judged.

## Chaser stress test (see HANDOFF-fable.md for the hard ones)
- [ ] Stack_Aft_2 is 6.5% off in volume: Extrude 4 (two profiles picked in a sketch with many
      regions) is the suspect; check its region selection against Fusion.
- [ ] Stack_Main is 3.7% off: Thread 2 and C-Pattern 2 are skipped (SolidCraft finds no
      cylinder at the thread's point), Extrude 7's profile is native (skipped).
- [ ] Stack_Front_1: Extrude 2's selection is not decoded (skipped with C-Pattern 1).
- [ ] Piston: Chamfer 2's edge point lands on an edge SolidCraft sees as tangent (imported).
- [ ] Threaded bodies cannot be judged by volume (open fitted thread surfaces); see hand-off 1.

## Fidelity
- [ ] Parameters: every Fusion parameter record carries its name (`d102`) and expression; emit
      `parameters.add` and use the names in feature fields so the scripts stay parametric.
- [ ] Sketch constraints and dimensions (cadmpeg decodes some: 57 of the film scanner's).
- [x] Hidden bodies: Fusion's kept tool bodies (film scanner bodies 2, 3, 5, 6) are history-only
      in cadmpeg (outside the current model, hidden); the converter hides them at the end
      (`hide_history_only_bodies`) and the final-shape check leaves them out.
- [ ] Components: component 2 is flattened into the root with `C2` body names; create a SolidCraft
      component instead (and read its name and occurrence transform).
- [ ] Fusion body names (when renamed from the defaults) and feature names that repeat across
      components (`Extrude 1 (2)`).
- [ ] Thread tolerance class (6g) and threads with an offset from the end.
- [ ] Spline refit: refit through Fusion's fit points instead of sampling the NURBS, so the
      sketch stays editable as a fit point spline.

## Assemblies
- [ ] Hidden bodies: Body Panels' derived body is hidden in Fusion (both previews lack it), but
      cadmpeg reports it visible; find where Fusion records it and hide it in SolidCraft.
- [ ] Occurrence placements: cadmpeg (upstream #1269, #1286) now decodes them from the
      scope-bound Component Insert carriers; test_asy's Side Panels sits 0.01 mm up in z, the
      others at the identity. Still unchecked against Fusion with moved parts. Decode them from
      the root (records around the Component Insert features) with an assembly whose parts are
      moved, then map turns too (`occurrence.move` takes a matrix).
- [ ] Nested sub-assemblies, joints, rigid groups; a part linked twice (the benchmark's root has
      4 Component Inserts but 3 occurrences).
- [ ] Map the thin extrude (SolidCraft has `thin`, `thickness`, `side`) once its profile selection
      decodes: selection ids are design-wide persistent ids, not curve or profile indices.

## Configurations
- [ ] Fusion configured designs (configuration tables): the benchmarks' tables are empty. Today
      only the active configuration's timeline would convert. Map the table (rows, parameter
      values, suppression, swapped components) to SolidCraft configurations, starting from a small
      configured dummy design.

## Coverage
- [ ] Coincident-body ambiguity (Side Panels): instead of "newest wins", try the alternatives and
      keep the one that matches the final B-rep (needs solidcraft-cli at conversion time).
- [ ] Profile-id rule for extrude selections and the "loop interior includes nested loops" rule
      rest on one case each; confirm with more files.
- [x] Try SolidCraft issues 2 and 3 with `--validation off`: issue 2 and the plain-shaft cut are
      the self-check only; issue 3 is a wrong result either way. The converter now retries a
      self-check refusal with validation off around the feature (`unvalidated_on_self_check`).
- [ ] Move with rotation (matrix to axis + angle; check SolidCraft's rotation centre).
- [ ] Construction planes at an angle that features use (only emitted when parallel to a
      principal plane; an unused one is left out).
- [ ] Mirror 2 rule: the converter leaves out a mirrored thread that changes no body in Fusion.
      Detect "lands on no face" geometrically instead of from unchanged body centres.
- [ ] Features not in the benchmarks yet: revolve, sweep, loft, fillet, chamfer, shell,
      rectangular pattern, counterbore and countersink holes, threaded holes, sketches on faces
      that should follow the face, construction axes and points, non-planar sketches.
- [ ] More benchmark files: each new file is a test of the reverse-engineered record layouts
      (fusion_raw.py); run every benchmark with `--check` after a change.

## Tool
- [x] A test runner: `bench.py` (convert every benchmark with `--check`, summary table) and
      `decode_diff.py` (decoder regression check).
- [ ] Rename scripts to `*.script.json` so they are not mistaken for SolidCraft designs (the app
      refuses them); `--check`/`--build` now also write a `.sc` next to each script.
- [ ] `--check` replays the script once per feature (slow on long timelines); replay once with
      `solidcraft-cli run --results` and measure after each feature instead.
- [ ] Single entry point: build the decoder on first use, or move the mapper into the Rust binary.
