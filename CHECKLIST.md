# Checklist (current work)

Updated as items close. Longer-term plan: `SIMPLIFY.md`; Fusion saves wanted: `FUSION-TEST-CASES.md`;
PR texts: `CADMPEG-PRS.md` (Drafts of 28 Nov).

## cadmpeg PRs (opened 28 Nov)
- [x] PR 12 = #2440 `f3d/two-sided-extrude-same-side`: **merged**. Opposite-sign two-sided Extrudes. Settled by T1 (all four sign pairs) and 9 solid Extrudes in 4 files with no exception. Drafted and thin forms stay native (DR-1080).
- [x] PR 13 = #2441 `f3d/assemble-tail-link-run`: Assemble scopes with associated tail references. Every joint in every benchmark now decodes; decode diff changes only the 5 joint designs. Joint motion of v6 joint records is still open (DR-1081, T8).
- [x] PR 11 (#2260, open) follow-up: the reviewer's blocking point (validator) and three non-blocking points fixed; rebased (it conflicted).
- [x] Clippy (clean), full tests (6585–6588 pass) and preflight (no FAIL) on all three after the rebase
- [x] Signed off; #2440 and #2441 opened, #2260 force-pushed and the reply posted
- [x] #2441 review (28 Nov): two blocking points (v4/v5 span formulas lacked A and C; DR-1081 described saves instead of the stored form, limit offsets 75/83 not 76) and five non-blocking ones; all but the layout-table offsets fixed, rebased, pushed (003aa5624f), reply posted
- [x] #2260: rebased on main again (conflicted in the manifest); clippy clean, 6665 tests pass, pushed (e76deb7bbd). No new review comments.
- [ ] Watch review on #2441 and #2260

## New cadmpeg branches (local, not pushed; PR texts in CADMPEG-PRS.md for your sign-off)
- [x] PR 14 `f3d/occurrence-carrier-property-block`: occurrence carriers whose source placement follows the three gating attributes. Fixes all four carriers of `f2sc-test-visibility-hidden` (three screws placed). Question for you: the corpus .f3z embeds Fusion's library screw (as #2260's does).
- [x] PR 15 `f3d/cylinder-489-frame`: the prefixed 489-byte Cylinder primitive. The primitives save's cylinder was silently at the origin; now at (-40, -10, 0) mm, a new body.
- [ ] Still open in visibility: carriers with a doubled path element (the two hidden screws of the two-hidden save), and occurrence visibility itself

## Fusion saves (FUSION-TEST-CASES.md)
- [x] T4+T5 `f2sc-test-surface-patch` saved
- [x] T6+T7 `f2sc-test-two-sided-more` saved (thin A, C, E; drafted B, D)
- [ ] T8 `f2sc-test-joint-motions`: hybrid design open, sketch started, not saved. Paused while you are away.
- [ ] Export the saves into benchmarks/ (you)

## Converter (f2sc)
- [x] Surface Patch outputs, decoded drafts, face-extrude extent, joined regions (committed fe53ab3)
- [x] Turned box primitives built from cadmpeg's placement (no inference); primitives case matches
- [x] Turned-box change committed (3f6b02c)
- [ ] fdi_mount Extrude 19 (ISSUES D5): diagnosed further; needs face points or a cadmpeg matcher fix (faces span six bodies)
- [x] fdi_mount Extrude 20: new bodies named by recorded centre via `body_names` (ISSUES D6); check now ok
- [ ] A component's feature using another component's sketch (revolute-limits component 2; today imported, shape matches)

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
- [x] Two stale stashes in `~/cadmpeg` dropped

## Provenance for PR texts
- Your own saves (fdi_mount, f2sc_screws, earlier test files): created and owned by you, the existing statement.
- Saves Claude built in your Fusion account (`f2sc-test-*`, T1 onward): the same statement, plus that they were created by you with AI assistance.
