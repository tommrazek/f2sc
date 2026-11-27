# Fusion test saves still wanted

Controlled saves that would settle an open question, each made in Fusion and exported to
`benchmarks/` like the `f2sc-test-*` files. Add a row whenever a question cannot be settled
from the files we have; strike it out (with the file name) once the save exists.

| # | Design name (proposed) | What to build | Settles |
|---|---|---|---|
| ~~T1~~ | `f2sc-test-two-sided-signs` (built 27 Nov, Extrudes 1–4) | Four new-body two-sided extrudes of four squares on one sketch: side 1 / side 2 = +20/−5, −20/+5, −20/−5, +20/+5 mm | DR-1080: where the solid lies when the two signs differ (only −/+ is confirmed, by fdi_mount) |
| ~~T2~~ | in `f2sc-test-two-sided-signs` (Extrudes 5–6) | Thin extrudes (wall 1 mm, side 1) of two open L chains: +20/−5 and −20/+5 | DR-1080 for wall-thickness extrudes (fdi_mount Extrude 8 fits neither reading); decoding the thin wall (ISSUES D3) |
| ~~T3~~ | in `f2sc-test-two-sided-signs` (Extrude 7) | A two-sided extrude −20/+5 mm with a 5° taper on side 1 | How a draft combines with same-side ends (DR-1080) |
| T4 | `f2sc-test-surface-patch` | A box with a through hole; Patch the hole's top edge loop; a second Patch over a 3D-sketch loop | Decoding a Surface Patch boundary as an edge selection (ISSUES D4) |
| T5 | `f2sc-test-extrude-patch-faces` | A plate with holes, Patch each hole, then one Extrude (new body) of the plate's face together with the patch faces | Extrude of several faces from different bodies (ISSUES D5) |
| T6 | `f2sc-test-two-sided-thin-closed` | Thin extrudes (wall 1 mm) of a closed rectangle, side 1 / side 2 = +20/−5 and −20/+5, once with each wall location (side 1, side 2, center) | DR-1080: why fdi_mount Extrude 8 (closed profile, +50/−2) spans 0…50 while open-chain thin extrudes follow the slab rule |
| T7 | `f2sc-test-two-sided-draft` | Two-sided extrudes −20/+5 and +20/−5 of a square, with a 5° draft on side 1 only, side 2 only, and both | DR-1080: T3 (draft on side 1, −20/+5) reaches the profile plane (−20…0) instead of the slab −20…−5 |
| T8 | `f2sc-test-joint-motions` | Two components (two boxes) joined seven times in seven copies of the design, or seven component pairs in one design: rigid, revolute, slider, cylindrical, pin-slot, planar, ball; the revolute and slider once more with limits and a rest value | DR-1081: where a version-6 joint record stores its motion type and limits (only one revolute with limits so far) |

Earlier saves still described in `CONTRIBUTIONS.md` ("Save that would settle it"):
- Stack_Main Extrude 7 transition (C8)
- Fillet over edges split by a thread (C10)
- C-Pattern axes (DR-327, DR-338)
- the Revolve stale-region question (DR-340)
