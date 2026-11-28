# Checklist (current work)

Updated as items close. Longer-term plan: `SIMPLIFY.md`; Fusion saves wanted: `FUSION-TEST-CASES.md`;
PR texts: `CADMPEG-PRS.md` (Drafts of 28 Nov).

**Final version for 28 Nov.** No new items may be added today without the user's explicit permission; only tick or annotate the items below.

Order today: T9, T10, T11 in Fusion first; then the remaining items.

## cadmpeg PRs (opened 28 Nov)
- [x] PR 12 = #2440 `f3d/two-sided-extrude-same-side`: **merged**. Opposite-sign two-sided Extrudes. Settled by T1 (all four sign pairs) and 9 solid Extrudes in 4 files with no exception. Drafted and thin forms stay native (DR-1080).
- [x] PR 13 = #2441 `f3d/assemble-tail-link-run`: Assemble scopes with associated tail references. Every joint in every benchmark now decodes; decode diff changes only the 5 joint designs. Joint motion of v6 joint records is still open (DR-1081, T8).
- [x] PR 11 (#2260, open) follow-up: the reviewer's blocking point (validator) and three non-blocking points fixed; rebased (it conflicted).
- [x] Clippy (clean), full tests (6585–6588 pass) and preflight (no FAIL) on all three after the rebase
- [x] Signed off; #2440 and #2441 opened, #2260 force-pushed and the reply posted
- [x] #2441 review (28 Nov): two blocking points (v4/v5 span formulas lacked A and C; DR-1081 described saves instead of the stored form, limit offsets 75/83 not 76) and five non-blocking ones; all but the layout-table offsets fixed, rebased, pushed (003aa5624f), reply posted
- [x] #2260: rebased on main again (conflicted in the manifest); clippy clean, 6665 tests pass, pushed (e76deb7bbd). No new review comments.
- [ ] Watch review on #2441, #2260, #2467, #2468 and #2479. 28 Nov: #2441 merged; #2467 anchors fixed and pushed (f5636e19e6); #2479 review fixes and rebase pushed (c1db6a491f), reply posted; the axis extension is PR 18 (local branch `f3d/joint-record-lane-axes`, 7e520733b9), to open after #2479 merges

## cadmpeg PRs (opened 28 Nov, morning)
- [x] PR 14 = #2467 `f3d/occurrence-carrier-property-block` (rebased on main, 6705 tests pass, opened): occurrence carriers whose source placement follows the three gating attributes. Fixes all four carriers of `f2sc-test-visibility-hidden` (three screws placed).
- [x] PR 15 = #2468 `f3d/cylinder-489-frame` (rebased; test updated for upstream #2449's feature names "CylinderPrimitive1"; 6705 tests pass, opened): the prefixed 489-byte Cylinder primitive. The primitives save's cylinder was silently at the origin; now at (-40, -10, 0) mm, a new body.
- [ ] Still open in visibility: carriers with a doubled path element (the two hidden screws of the two-hidden save), and occurrence visibility itself

## Fusion saves (FUSION-TEST-CASES.md)
- [x] T4+T5 `f2sc-test-surface-patch` saved
- [x] T6+T7 `f2sc-test-two-sided-more` saved (thin A, C, E; drafted B, D)
- [x] T8 `f2sc-test-joint-motions` saved (28 Nov): ten box components, nine joints (rigid, revolute, slider, cylindrical, pin-slot, planar, ball; revolute −30°/60°/rest 15° and slider −5/20/rest 8 mm). The overnight draft was lost when the Fusion session expired; rebuilt.
- [x] Saves exported into benchmarks/ (surface-patch, two-sided-more, joint-motions)
- [x] T9 `f2sc-test-two-sided-rest` exported and decoded 28 Nov (DR-1080): drafts settled (see ISSUES D3 note); thin wall code 2 kept the slab, so the wall field at 157 is not settled. Spec was: −5/+20 with a 5° draft on side 1 (side 1 short); +20/−5 with drafts on both sides; thin closed +20/−5 with wall code 2
- [x] T10 `f2sc-test-joint-axes` exported and decoded 28 Nov: codes 0/1/2 rotate about x/y/z, 6/7/8 slide; committed on PR 16's branch (177e9cfd29, not pushed). Spec was: revolute X, revolute Y, slider X, slider Y, planar normal X, planar normal Y (Z cases are in T8)
- [ ] T12 `f2sc-test-visibility-toggles` (added with the user's permission, 28 Nov; ISSUES D11): four inserts of `f2sc-test-insert-part`; A untouched, B hidden once, C hidden then shown, D hidden, shown, hidden; one session, one save. Which carriers store the doubled path element tells whether toggling, the final hidden state, or a repeated toggle causes it
- [x] T11 `f2sc-test-patch-loops` exported 28 Nov (PR 17): a plate with a rectangular and a round hole; Patch 1 over the rectangular hole's top loop (4 edges), Patch 2 over both holes' top loops in one feature

## Analysis of the 28 Nov saves
- [x] joint-motions (DR-1081): a version-6 joint record holds one 51-byte lane per freedom (`01 00 00 00`, self-reference marker, max/min/rest flags at +11, values at +23/+31/+39, u32 code at +47). Codes: 2 rotate Z, 6/7/8 slide X/Y/Z, 3/4/5 the ball's rotations. Holds for every v6 joint in the benchmarks (joint-motions, revolute-limits, insert-joint, screws, visibility). cadmpeg reads all nine as fixed today.
- [x] cadmpeg PR 16 = #2479 `f3d/joint-record-lanes` (opened 28 Nov): v6 joints project from their lanes; clippy clean, 6711 tests pass, new test fails without the change
- [x] two-sided-more (DR-1080): a centred wall (code 1) and fdi Extrude 8's wall (code 2) reach the profile plane; code 0 follows the slab rule. A draft on side 1 reaches the profile plane (twice, side 1 the longer side both times); on side 2 it follows the slab rule. Field: u32 at scope offset 157 (after a presence byte). Needs T9 to settle.
- [x] surface-patch (D4): boundary is an `edge_recipe_data` member that the path binder does not take (ISSUES D4)
- [ ] cadmpeg PR 17 (draft): Surface Patch boundaries. 28 Nov with T11: (a) a patch over one rectangular hole (T11 Patch 1) already resolves to its four edges; (b) the surface-patch save's single-edge boundary is a one-side edge recipe naming only the hole wall (side `(0,[1])`), whose two circles both remain candidates; the recipe's second reference (the top face) would pick the top circle, but that rule rests on one recipe; (c) a patch over two loops in one feature (T11 Patch 2, like fdi_mount's patches) fails before edge resolution (the boundary stays the scope reference). Needs more saves or a deeper look at (c)

## Converter (f2sc)
- [x] Surface Patch outputs, decoded drafts, face-extrude extent, joined regions (committed fe53ab3)
- [x] Turned box primitives built from cadmpeg's placement (no inference); primitives case matches
- [x] Turned-box change committed (3f6b02c)
- [x] fdi_mount Extrude 19 (ISSUES D5), worked around 28 Nov (check ok; README §9 item 10): diagnosed further; needs face points or a cadmpeg matcher fix (faces span six bodies). Converter works around SolidCraft's one-body `faces` limit (one face extrude per body, joined); note it in README.md
- [x] fdi_mount Extrude 20: new bodies named by recorded centre via `body_names` (ISSUES D6); check now ok
- [x] A component's feature using another component's sketch: the sketch is drawn again in the using component (ISSUES F5). revolute-limits component 2 Extrude 1 and film_scanner Extrude 3 (+ Moves 3–5) now convert and match; the only two cases in the benchmarks

## Test files checked (diagnosis done; each needs a cadmpeg change or a Fusion save)
- [x] revolute-limits: joint fixed by PR 13; motion type and limits of v6 joints open (DR-1081, T8)
- [x] visibility: the hidden-occurrence record names a Screw-made occurrence carrier. **28 Nov:** in the newer save all four occurrence carriers fail cadmpeg's placement grammar (two-element path and a property block); f2sc_screws' older spelling reads. The gating-attribute form is fixed on PR 14's branch; the doubled path element stays open (ISSUES D11)
- [x] primitives: turned box fixed in the converter; cylinder fixed on PR 15's branch
- [x] split: the split tool's face selection stays native in cadmpeg
- [x] revolve-stale: the revolve's region selection stays native (DR-340)
- [x] M3 screw: the extrude's face profile stays native (same as the M2 screw, C9)
- [x] T1–T3 decoded and used for PR 12

## Housekeeping
- [x] SolidCraft 0.50.0 installed; `~/cadmpeg-wt` build cache cleared
- [x] Two stale stashes in `~/cadmpeg` dropped; the #2441 stash dropped (28 Nov)

## Provenance for PR texts
- Your own saves (fdi_mount, f2sc_screws, earlier test files): created and owned by you, the existing statement.
- Saves Claude built in your Fusion account (`f2sc-test-*`, T1 onward): the same statement, plus that they were created by you with AI assistance.
