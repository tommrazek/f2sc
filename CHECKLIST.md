# Checklist (current work)

Updated as items close. Longer-term plan: `SIMPLIFY.md`; Fusion saves wanted: `FUSION-TEST-CASES.md`;
PR texts: `CADMPEG-PRS.md` (Drafts of 28 Nov).

## cadmpeg PRs (opened 28 Nov)
- [x] PR 12 = #2440 `f3d/two-sided-extrude-same-side`: opposite-sign two-sided Extrudes. Settled by T1 (all four sign pairs) and 9 solid Extrudes in 4 files with no exception. Drafted and thin forms stay native (DR-1080).
- [x] PR 13 = #2441 `f3d/assemble-tail-link-run`: Assemble scopes with associated tail references. Every joint in every benchmark now decodes; decode diff changes only the 5 joint designs. Joint motion of v6 joint records is still open (DR-1081, T8).
- [x] PR 11 (#2260, open) follow-up: the reviewer's blocking point (validator) and three non-blocking points fixed; rebased (it conflicted).
- [x] Clippy (clean), full tests (6585–6588 pass) and preflight (no FAIL) on all three after the rebase
- [x] Signed off; #2440 and #2441 opened, #2260 force-pushed and the reply posted
- [ ] Watch CI and review on #2440, #2441, #2260

## Converter (f2sc)
- [x] Surface Patch outputs, decoded drafts, face-extrude extent, joined regions (committed fe53ab3)
- [x] Turned box primitives built from cadmpeg's placement (no inference); primitives case matches
- [x] Turned-box change committed (3f6b02c)
- [ ] fdi_mount Extrude 19: extrude the faces, not sketch regions (ISSUES D5)
- [ ] fdi_mount Extrude 20: name new bodies by centre (ISSUES D6)
- [ ] A component's feature using another component's sketch (revolute-limits component 2; today imported, shape matches)

## Test files checked (diagnosis done; each needs a cadmpeg change or a Fusion save)
- [x] revolute-limits: joint fixed by PR 13; motion type and limits of v6 joints open (DR-1081, T8)
- [x] visibility: the hidden-occurrence record cadmpeg already reads names a Screw-made occurrence, and cadmpeg decodes no Screw-made occurrences (same root cause as A1). Next cadmpeg topic.
- [x] primitives: turned box fixed in the converter; cylinder placement is not decoded by cadmpeg (identity placement)
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
