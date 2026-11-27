# Checklist (current work)

Updated as items close. Longer-term plan: `SIMPLIFY.md`; Fusion saves wanted: `FUSION-TEST-CASES.md`.

## Two-sided extrude PR (cadmpeg)
- [x] Fix on local branch `f3d/two-sided-extrude-same-side` (−/+ signs), tests, spec, DR-1064
- [ ] Build T1–T3 in Fusion (sign combinations, thin, taper); you export them. T1 and the two thin cases are done, in `f2sc-test-two-sided-signs`; T3 (taper) is next
- [ ] Decode T1–T3, settle DR-1064, extend the fix to every sign combination it settles
- [ ] cadmpeg clippy and full test suite on the branch
- [ ] Draft PR text (provenance below) for your sign-off; push only after your OK

## Converter (f2sc)
- [x] Benchmark comparison on SolidCraft 0.50: no regressions; face-extrude now matches, fdi_mount converts fully
- [x] Commit: import Surface Patch outputs, decoded drafts, face-extrude extent, joined regions, decoder API port
- [ ] fdi_mount Extrude 19: extrude the faces, not sketch regions (ISSUES D5)
- [ ] fdi_mount Extrude 20: name new bodies by centre (ISSUES D6)

## Test files still to check in depth
- [ ] revolve-stale: region still chosen by final volume
- [ ] primitives: turned box imported; cylinder placed by body centre
- [ ] split: split by faces imported
- [ ] revolute-limits: component 2 sketch not emitted; angle limits
- [ ] M3 screw (visibility files): extrude whose profile is a body face
- [x] visibility: cadmpeg marks nothing hidden in either save. PR topic; the two saves are the test pair

## Housekeeping
- [x] SolidCraft 0.50.0 installed (`~/opt/sc50`)
- [x] `~/cadmpeg-wt` build cache cleared (folder kept)

## Provenance for PR texts
- Your own saves (fdi_mount, f2sc_screws, earlier test files): created and owned by you, the existing statement.
- Saves Claude built in your Fusion account (`f2sc-test-*`, T1 onward): the same statement, plus that they were created by you with AI assistance.
