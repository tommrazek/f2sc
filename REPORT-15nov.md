# Report: film scanner and chaser_fluidic diagnosis (15 Nov)

## Summary

- **Film scanner, uncut frames and the empty root `.sc`:** the main cause is the Mac's
  decoder. It was built before the 14 Nov cadmpeg rebase. I reproduced your output line for
  line with the old decoder kept here, and the current decoder does not produce it.
- With the current decoder, one real defect remained: SolidCraft deletes frame body 1 when the
  threaded shaft cuts it (SolidCraft issue 3). The converter now works around it. **Both frames
  are cut**: body 1 is 77,868 mm³ (Fusion lies between 77,137 and 78,755) and body 4 is
  64,813 mm³ (Fusion lies between 64,300 and 66,141).
- **chaser_fluidic:** both build failures and the root failure are fixed. All 10 parts and the
  root build. Piston now converts with every feature and matches Fusion. Stack_Main imports 4
  features from Fusion's final shape instead of 6.
- 8 commits on `main`, one per problem, all pushed. There are 16 issues in all, listed in
  `ISSUES.md` with the owner and the commit for each. `CONTRIBUTIONS.md` lists the cadmpeg pull-request topics and the cadmpeg
  open items, with evidence, for a focused session.
- Benchmarks: three rows improved, nothing got worse; the dummy designs are unchanged.

## What to do on the Mac

```
git pull
git -C cadmpeg checkout --detach
git -C cadmpeg fetch origin
git -C cadmpeg fetch ../cadmpeg-f3d2sc.bundle +f3d2sc:f3d2sc
git -C cadmpeg checkout f3d2sc
(cd decoder && cargo build --release) && cp decoder/target/release/f3d2sc-decode bin/
bin/f3d2sc-decode --version
```

The last line should print `f3d2sc-decode cadmpeg d190f3e4c3…`. From now on the converter
warns when the decoder is older than `cadmpeg/` or has no stamp.

## 1. Film scanner

### Why nothing was subtracted

Your run's notes say Mirror 4 had no mirror plane, and that Combine 4 and Combine 5 had "no
SolidCraft body". Those notes come from the pre-rebase decoder:
- Mirror 4's plane is decoded since the cadmpeg fix `d53b936766` (14 Nov), so the old decoder
  leaves Mirror 4 out.
- Without Mirror 4, tool body 6 is never made, and both Combines are left out. So frames 1 and
  4 stay uncut, and tool bodies 5 and 6 stay visible. Only bodies 2 and 3 are hidden, as in
  your screenshot.
- Occurrence placements are also decoded only since the rebase. So the root script placed
  nothing ("0 commands placing 2 components"), and the root `.sc` has 0 bodies.

I reran your command with `bin/f3d2sc-decode.828fd1ea50`, the pre-rebase build. The output
matched yours line for line, including "0 bodies, 0.000 mm3". With the current decoder, the
root places both components and the assembly has 7 bodies.

### What was still wrong with the current decoder (SolidCraft issue 3)

Combine 4 cuts frame body 1 with the threaded shaft (body 5). SolidCraft 0.40.1 reports
success but deletes body 1. Under `--check` the converter left Combine 4 out (frame uncut).
Under `--build` alone the frame disappeared.

I tested variants on the 4-command reproduction and on the real script:

| Variant | Result |
|---|---|
| validation off | body lost |
| move both bodies 100 mm and back | body lost |
| shaft moved 20 mm | works; moved 40 mm: body lost (depends on position) |
| tool split at its middle, across the axis | cut does nothing (wrong) |
| **tool split lengthwise, through the thread axis** | **works**, with the same result as a design-specific split |

On the film scanner, the lengthwise split gives body 1 = 77,868.3 mm³, inside Fusion's range.
It also gives body 4 = 64,813.0 mm³, identical to the direct cut, so the method does not change
the geometry. The converter now cuts with both halves of a copy of a threaded tool
(`split_threaded_cut_tool`). It tracks thread axes per body and carries them through mirrors.

## 2. chaser_fluidic

`benchmarks/chaser_fluidic.f3d` does not exist; the design is the `.f3z` (your second
command). Your `.f3z` run reproduced here exactly with the current decoder, so these were real
defects:

| Failure | Cause | Fix |
|---|---|---|
| Stack_Main `.sc`: step 82 `solid.offset_face` refused | Thread 2 resizes two r = 0.5 mm bores to the thread's minor diameter. SolidCraft refuses to offset them at any distance, also with validation off (new SolidCraft issue 5). `--build` without `--check` had no fallback. | `--build` now runs each script once and applies the `--check` fallbacks. The bore is widened by a coaxial cylinder cut instead. That cut first deleted the whole body too (new SolidCraft issue 6) unless it ran 1 µm past each end. Thread 2 and C-Pattern 2 now convert. |
| Piston `.sc`: step 21 chamfer "edge is smooth" | Revolve 1 revolved two sketch regions; Fusion revolves one. The second, a 0.1 mm groove ring, filled the groove, so Chamfer 2's edges did not exist. Fusion's choice is stored in a selection form cadmpeg keeps native (DR-340). | The converter reads the selection's initial region. Then Chamfer 1 came out 0.24 mm³ off Fusion: which face takes its distance is not decoded (DR-341). Under `--check`, a mismatch now tries the other side. Piston matches Fusion with all features. |
| Root: "Stack_Main.sc: not found" | One failed part stopped the root build. | The root builds without failed parts, with a warning. |

## 3. Commits (all on `main`, pushed)

| Commit | Fix |
|---|---|
| `aa05ce9` | Decoder stamps its cadmpeg commit; the converter warns on a stale decoder |
| `6d9bcf3` | Cut by a threaded tool's halves (SolidCraft issue 3) |
| `d46c7bc` | `--build` runs each script once and falls back on a refused feature |
| `026fa7b` | Root assembly builds without the parts that failed |
| `e5657ba` | Revolve reads the initial region of a native compact selection (DR-340) |
| `fb6cbe5` | `--check` flips an undecoded chamfer side on a final-shape mismatch (DR-341) |
| `75f55bb` | Bore widened by a cut when SolidCraft refuses the offset face |
| `4f5b941`, `b4a6dc1` | `CONTRIBUTIONS.md`, `ISSUES.md`, SolidCraft reproductions 5 and 6, README |

All of these are converter changes. None needed a cadmpeg code change. Where cadmpeg was the
cause (DR-340, DR-341, two native extrude profiles, Fillet 3's edges), its open items are
unsettled format questions. I documented the evidence in `CONTRIBUTIONS.md` instead of guessing
in cadmpeg. A focused session can work from that.

Converter inferences added (each can be switched off with `--no-infer NAME`):
- `split_threaded_cut_tool`
- `revolve_region_from_initial_loop`
- `chamfer_side_by_final_shape`
- `bore_widened_by_cut`

## 4. Benchmarks (15 Nov)

Full suite, about 20 minutes. Every design converts with 0 gaps.

| Design | Before → now |
|---|---|
| Film scanner component 1 | skipped 1 → 0 (Combine 4 now converts) |
| Chaser Piston | imported 2 → 0, match |
| Chaser Stack Main | imported 6 → 4, match |

Every other row and every dummy design is unchanged.

## 5. Still open

- **cadmpeg (for the focused session):**
  - DR-340: compact region selections with further regions.
  - DR-341: which face takes a chamfer's distance.
  - Stack_Main Extrude 7 and the M2 screw's Extrude 2: profiles kept native.
  - Stack_Front_1 Fillet 3: edges that do not bind.
  - DR-641, and DR-327 / DR-338 (C-Pattern axes; these need saves made in Fusion).
- **Converter:** film scanner component 2, Extrude 3's sketch is not emitted (imported instead).
- **Your go-ahead:**
  - Report SolidCraft issues 1, 3, 5 and 6 upstream (this posts under your account).
  - Open the cadmpeg pull requests (needs a fork, the DCO box and the provenance statement).
- **Not checked:** the Mac itself. The fixes are verified here on Linux with SolidCraft
  0.40.1 and the current decoder.
