# Report: set-up, cadmpeg update and TODO work (14–15 Nov)

## Summary

- The repository is set up on this machine and verified with the full benchmark suite.
- cadmpeg is rebased on the latest upstream `main` (64 new commits, no textual conflicts).
- The 7 code-only branch commits are now complete for pull requests. Each has specification,
  layout-table entries, open items and tests. Five further commits fix what the update and the
  film scanner exposed.
- cadmpeg's whole workspace test suite, clippy, rustfmt and the source-policy check pass. The
  branch started with 6 failing unit tests; it has none now.
- **Film scanner volume (your observation is right):** tool bodies stayed visible and two
  threaded-hole cuts were missing. Mirror 4 and Combine 5 now convert, and the kept tool bodies
  are hidden as in Fusion. Combine 4 is still missing because of a SolidCraft bug (issue 3),
  which now has a 4-command reproduction.
- Benchmarks: every design converts with 0 gaps. Two rows improved and none got worse.

Nothing is committed in this repository, and nothing is pushed anywhere. The cadmpeg commits
are local on the branch `f3d2sc` and in `cadmpeg-f3d2sc.bundle`.

## 1. Set-up (TODO steps 1–6)

| Step | Result |
|---|---|
| Tools | git, Python 3.12, Rust 1.96, 54 GB RAM, 12 cores (already installed) |
| cadmpeg + branch | cloned; `f3d2sc` fetched from the bundle |
| Decoder build | about 2 minutes with the default job count (memory was not a constraint) |
| SolidCraft 0.40.1 CLI | `/home/rocm6/opt/solidcraft-0.40.1-linux-x86_64/bin/solidcraft-cli`; the archive's SHA-256 matches the digest GitHub publishes |
| Single-file check | `steel_test_articles`: 2/2 bodies match |
| Full benchmarks | matched the README table exactly; 17 minutes |

`bench.py` does not scan `benchmarks/dummies/`; run it there with `--benchmarks benchmarks/dummies`.

## 2. cadmpeg update and pull-request readiness

The rebase from `828fd1ea50` onto `cd06f603a5` applied cleanly. Things to know:

- **Revolve profiles (converter):** upstream #1246 states Revolve profiles as
  `sketch_selection`, `sketch_profiles` or `sketch_regions`. The converter only accepted
  `sketch`, so every Revolve would have stopped conversion. Fixed in the converter.
- **Assembly joints:** 13 Chaser joints now decode as native `Assemble` instead of
  `assembly_joint`. This comes from upstream's assembly-relation work (#1219, #1225, #1249,
  #1260) and relates to cadmpeg DR-249. The converter does not use joints, so no output changed.
- **Gains:** test_asy's Component Inserts resolve, Move losses in the film scanner drop from
  10 to 3, tapped holes are recognized, and occurrence placements are decoded (#1269, #1286).
- **6 failing unit tests:** these came with the bundled branch, not with the update. Two branch
  commits changed decoding rules without updating the tests. All 6 are fixed, with expectations
  taken from the specification.

Branch `f3d2sc`: 17 commits, 35 files, +1,012/−239. Pull requests are squash-merged, so each
completing commit belongs with the commit it completes:

| Topic | Original commit | Completed by |
|---|---|---|
| Revolve direction kind 1 (full turn) and the 361-byte frame | `66a0660996`, `218adc09b4` | `bbfa03ede2`, `773c58f4d0`. The 361-byte frame is the **88-byte scope tail** with a second marked reference. The scope parser now reads that tail's preceding history state for every family, which restores the preceding state of two Chaser Revolves. The original commit matched `02 00 00 00 F5`, which was a history-state value, not a format constant. New open item DR-641. |
| Manifest `NA_EXPORT` / `NA_OFFLINESAVE` after a display name | `4a0154eb48` | `a38d04d3d1`. Grammar and test; a lineage suffix after a marker is now refused. |
| Parameter wide trailer, discriminator 5 | `62e7c2ff79` | `9c91514974`. Specification, layout table, tests. |
| Extended Thread scopes in record order | `1fb8b4501a` | `953a12cd45`. Trailer owners must match local ordinals 1/0; tests follow the specification. |
| Preceding plane/cylinder of a face support context | `076dadefb2` | `65bdac7335` (move to `history/face_support.rs`; `history.rs` was over the 10,000-line limit), `772e498787` (specification, test). |
| A body a state still holds is not deleted | `26872f5433` | `772e498787`. `deleted_bodies` defined in the specification; the test fails without the fix. |
| Formatting of the branch's code | | `25be8989cb` |
| **New:** identities beside every listed origin are constructed | | `d53b936766`. A stream stores one origin per component; with as many complete origin runs as components, a partial run cannot be an origin. Upstream #1232's WorkPlane reading then applies. The invariant holds on all 11 benchmark designs. All four film scanner Mirror planes resolve. |
| **New:** Mirror plane member, entity selection over a later face recipe | | `d190f3e4c3`. Resolves test_asy Side Panels' Mirror 4: the YZ origin plane, which the converter had inferred. |

Gates run on the final branch: `cargo test --workspace --all-targets` (no failures),
`cargo clippy -p cadmpeg-codec-f3d --all-targets`, `cargo fmt --check`,
`scripts/check-source-policy.py`, and `layout_tables`. `decode_diff.py` between the builds:
0 regressions at every step.

Still needed for the pull requests (your call): a GitHub fork to push to, the DCO box, and the
provenance statement of CONTRIBUTING.md.

## 3. Film scanner volume check

Fusion's reference frames are open STEP bodies, because cadmpeg cannot write their fitted
thread surfaces, so the volume can't be read directly. The bounds come from cutting the frames
with plain bores at the thread's crest and root radii (r = 32.382 and r = 30.662, read from
Fusion's B-rep). The real threaded hole lies between the two.

| Frame | Before | Now | Fusion lies between |
|---|---|---|---|
| Body 1 (Combine 4) | 85,529.8 mm³ | 85,529.8 mm³ (uncut, SolidCraft issue 3) | 77,136.9 and 78,755.3 mm³ |
| Body 4 (Combine 5) | 73,785.0 mm³ | **64,813.0 mm³** | 64,300.4 and 66,141.3 mm³ |

What was wrong, and what changed:
1. **Mirror 4's plane was not decoded.** Fixed in cadmpeg (`d53b936766`): it is WorkPlane 2,
   at z = 25.00000037 through the shaft axis.
2. **Combine 4 and 5 were left out because of Mirror 4.**
   - Combine 5 now converts.
   - Combine 4 triggers SolidCraft issue 3, which silently deletes frame body 1. The converter
     now catches a body that SolidCraft loses and leaves the feature out instead of deleting
     the frame.
3. **Kept tool bodies (2, 3, 5, 6) were visible.** cadmpeg marks them history-only: outside
   Fusion's current model and hidden there. The converter now hides them
   (`browser.visibility`; the hidden state persists into the `.sc`). The body count matches
   (2 vs 2).
4. **A bit-exact volume match is not possible here.** Fusion builds the thread to its 6g class
   (crest r = 32.382), while SolidCraft's thread has no tolerance class (crest r = 32.5).

## 4. TODO items

| Item | Status |
|---|---|
| Film scanner: Mirror 4, Combine 4 / 5 | Mirror 4 and Combine 5 done; Combine 4 blocked by SolidCraft issue 3 |
| Film scanner component 2: Extrude 3's sketch not emitted | open (imported instead) |
| SolidCraft issues 2 and 3: minimal reproductions | issue 3 done (`solidcraft-issues/03-minimal-threaded-cut-deletes-target.json`); issue 2 does not reproduce minimally and no longer occurs in the conversion |
| Report SolidCraft issues upstream | not done: it posts publicly under your account |
| Try issues 2 and 3 with `--validation off` | done: issue 2 is only the self-check; issue 3 is a wrong result either way. The converter now retries a self-check refusal with validation off around that feature. |
| Mirror planes inferred from bodies | film scanner (4) and Side Panels Mirror 4 now decoded; Side Panels' first Mirror 1 (class-303 surface mirror) has no decoded construction |
| C-Pattern axes (Chaser) | open: cadmpeg DR-327 / DR-338 need controlled saves made in Fusion |
| Rebase the cadmpeg branch | done; pull requests pending (your call) |
| Hand-off 5: regions bounded by part of a longer curve | converter fallback (`region_by_arrangement`): Stack Front 1's Extrude 2 and C-Pattern 1 convert. The cadmpeg-side profile builder is still open (upstream #1262 keeps such selections native). |
| Hidden bodies (film scanner) | done |
| Occurrence placements | decoded upstream; test_asy Side Panels at +0.01 mm z, the others at the identity; not checked against a design with moved parts |
| Chaser: Stack Aft 2 6.5% off, Stack Main 3.7% off | stale: both match exactly today (Stack Main via 6 imports) |
| Chaser: Stack Front 1 Fillet 3 | open: about 20 edge operands decode but do not bind to historical edges |
| Parameters by name (`parameters.add`) | not started: the benchmarks have 14 parameter references in all (13 of them library parameters in the M2 screw) |
| Hand-offs 3, 4, 6; configurations; joints; rename to `*.script.json`; single replay for `--check` | not started |

## 5. Matching against cadmpeg's issues and open items

| Topic | cadmpeg |
|---|---|
| Thread decoding upstream | issue #448 ("Thread/Assemble have no decode path"): the branch's thread commits are the answer for Thread |
| Kept tools history-only | issue #1072 (removed bodies stay in `model.bodies`); `history_only` bindings are what the converter now uses |
| Threaded bodies "not judged" | issue #1161 (approximations that change volume) |
| Per-feature check speed | issue #944 (per-feature result measures) would replace the converter's per-feature replay |
| C-Pattern axes | DR-327, DR-338 |
| Thread option bytes | DR-142 (partly answered by the branch) |
| Revolve direction kind 1, 361-byte frame | DR-641 (new), DR-269 updated |
| Joint activation (13 Chaser joints native) | DR-249 |
| Combine tools from other components (hand-off 3) | DR-562, DR-563; upstream #1287 |
| Configurations | DR-204 |

## 6. Converter changes (not for pull requests; documented in README and TODO)

- Revolve: accepts the new profile kinds.
- `unvalidated_on_self_check`: retries a feature with SolidCraft validation off when SMLib's
  self-check refuses it.
- A body lost by SolidCraft counts as a refusal.
- `cmd_owner` bug fixed: after a rolled-back feature, refusals were attributed to the wrong
  feature.
- `hide_history_only_bodies`: hides the kept tool bodies; the final-shape check skips hidden
  bodies.
- `region_by_arrangement`: picks regions bounded by part of a longer curve.
- `inference.py` lists each new rule; `--no-infer NAME` switches it off.

## 7. Benchmarks (15 Nov)

Identical to the 14 Nov baseline except two improved rows:

| Design | Skipped before → now | Final shape before → now |
|---|---|---|
| film scanner component 1 | 3 → 1 | body count differs (6 vs 2) → match, not judged (threaded holes) |
| Chaser Stack Front 1 | 3 → 1 | not judged → not judged |

## 8. Files and locations

- Repository changes (uncommitted): `f3d2sc.py`, `inference.py`, `README.md`, `TODO.md`,
  `decoder/Cargo.lock`, `cadmpeg-f3d2sc.bundle`, `solidcraft-issues/03-minimal-threaded-cut-deletes-target.json`,
  and this report.
- Decoder builds kept in `bin/`: the starting decoder `f3d2sc-decode.828fd1ea50`, the first
  rebased build `f3d2sc-decode.rebased`, and the build before the Mirror tie-break
  `f3d2sc-decode.prev`. `bin/f3d2sc-decode` is the final build.
- cadmpeg backup of the original branch: `f3d2sc-base-828fd1ea50`.
