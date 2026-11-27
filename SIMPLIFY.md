# Plan: f2sc as the last step only (27 Nov, draft)

**Goal.** cadmpeg reads everything the file states. f2sc only maps cadmpeg's IR onto SolidCraft
commands. Every place where f2sc patches a cadmpeg gap moves into cadmpeg as a pull request
with a CC0 Fusion save as its test. After that, f2sc keeps three things:

1. the IR → SolidCraft mapping;
2. workarounds for SolidCraft's own limits, each reported upstream;
3. the policy fallbacks for anything still undecoded, so one bad feature does not lose a part
   (freeze from the final B-rep, skip, and the final-shape check).

Everything is measured by the existing benchmarks. Each removal must give 0 regressions in
`bench.py` and `decode_diff.py`, and the same `--check` outcome.

## The test designs (made 26–27 Nov in Fusion web, Default Project)

These are for export as `.f3d`/`.f3z` and, once you agree, for the cadmpeg corpus under CC0.

| Case | Design | What it pins down |
|---|---|---|
| 1 | `f2sc-test-insert-part`, `f2sc-test-insert-joint` | A linked part placed by a Component Insert, then moved by a joint |
| 2 | `f2sc-test-revolute-limits` | A revolute joint with angular limits, at a known current angle |
| 3 | `f2sc-test-visibility-hidden`, `f2sc-test-visibility-two-hidden` | Visibility of a body vs. an occurrence, in two saves that differ by one toggle |
| 4 | `f2sc-test-revolve-stale` | A Revolve whose sketch was edited after it picked its region |
| 5 | `f2sc-test-cpattern` | Circular pattern about a known axis, with count and angle |
| 6 | `f2sc-test-threads` | Modeled and cosmetic threads, full length and set length |
| 7 | `f2sc-ttest-primitives` (name typo) | Box, cylinder and sphere primitives off the origin |
| 8 | `f2sc-test-shared-sketc` (name cut short) | Several features sharing one sketch; a region bounded by part of a longer line |
| 9 | `f2sc-test-split` | Split Body, then Remove Body of one piece |
| 10 | `f2sc-test-fillet-chain` | Fillet picked by one edge, grown to a 4-edge tangent chain |
| 11 | `f2sc-test-face-extrude` | Extrude (Join, 10° taper) whose profile is a body face, not a sketch |
| 12 | `f2sc-test-combine-keep` | Combine Cut with Keep Tools, with the kept tool body hidden afterwards |

## Workarounds and where each one goes

### A. Into cadmpeg (one PR per row, in this order)

| PR | Converter code removed | cadmpeg change | Test case | Issue |
|---|---|---|---|---|
| A1 | `insert_pose_from_component_path`, `insert_occurrences_from_display_poses`, `screw_occurrences_from_component_paths`, `screw_library_json`, `insert_occurrences`, `insert_poses` | Bind every Component Insert to its linked occurrence at its current pose. Give each Screw feature its own occurrence, with the pose taken from the component-path record. (#2260 does the joint-moved half.) | 1, `f2sc_screws` | A1, A2, A7, A9 |
| A2 | visibility patching in `assembly_commands` | Decode body-level and occurrence-level visibility in the newer Assembly Design layout | 3 | A8 |
| A3 | `hide_history_only_bodies` | Mark a kept Combine tool body and its later visibility, so it does not have to be guessed from final-model matching | 12 | F4 |
| A4 | `thread_face_by_radius`, `thread_start_at_axis_end`, `thread_record_by_strings`, the thread part of `fusion_raw.py` | Decode the Thread face selection, the start end of a set-length thread, and the designation, class and profile | 6 | C6 (part), DR-142 |
| A5 | `fusion_raw.py` pattern axis and count; inferred pattern axes | Decode the C-Pattern axis, count and angle | 5 | C11, DR-327, DR-338 |
| A6 | `revolve_axis_through_body_centre`, `revolve_sketch_from_records`, `revolve_regions_by_final_volume`, `revolve_region_from_initial_loop`, `stale_region_left_out` | Decode the Revolve axis and sketch in every frame. Resolve a stale stored region against the edited sketch the way Fusion does. | 4 | C4, DR-340, DR-646 |
| A7 | `profile_ids_by_rank`, `region_by_arrangement`, most of `planar.py` | Build profiles from the sketch's curve arrangement (curves split where they meet), with stable profile ids | 8 | N1 |
| A8 | `primitive_placed_by_body_centre` | Decode the placement frame of the Box, Cylinder and Sphere primitives | 7 | — |
| A9 | `split_piece_by_final_side` | State which Split piece a Remove Body removes | 9 | — |
| A10 | (today the feature is imported) | Bind an Extrude profile that is a body face | 11 | C9 |
| A11 | (today the feature is left out) | Bind Fillet edges grown by a tangent chain | 10 | C10, DR-341 (rest) |
| A12 | `owner_from_body_evidence`, `reassign_owners` | Put a feature in the component that holds its body | existing benchmarks | — |
| A13 | `no_geometry_by_absence` | Say that a Screw feature makes no geometry of its own (comes with A1) | `f2sc_screws` | — |
| A14 | angular limits not carried over (`joints.py`) | State a revolute joint's current angle and its sign convention | 2 | A10 |
| A15 | `step_thin.py` | Have the STEP writer write a helix as few Bezier segments within a tolerance, and close fitted thread surfaces correctly | 6 | F6 |

When a row lands upstream, its rule leaves `inference.py`. The converter then reads the decoded
field. Between the merge and our decoder rebuild, the old rule stays as a fallback, used only
when the field is absent.

### B. Stay in f2sc: SolidCraft limits (report upstream, drop when fixed)

- `bore_widened_by_cut` and the 1 µm overrun (SolidCraft issues 5, 6)
- `split_threaded_cut_tool` (SolidCraft issue 3)
- `unvalidated_on_self_check`
- `refit_spline` for dense sketch splines (SolidCraft issue 1)
- `chamfer_side_by_final_shape`: cadmpeg now decodes the flip (merged PR 9). Only the mapping of
  Fusion's side to SolidCraft's default side is left, and that belongs in the mapper.
- `drop_refused_joints`

### C. Stay in f2sc: policy

`freeze_from_final_brep`, `skip_unconvertible`, the final-shape check, and the per-sketch
constraint probe in `constraints.py`.

### D. Converter-only clean-up (no cadmpeg PR)

- **`ir_compat.py`:** port the converter to read CADIR v11 directly, then delete the
  old-shape upgrade.
- **`fusion_raw.py`:** gone once A4 and A5 are merged. f2sc then never reads `BulkStream.dat`.
- **`inference.py`:** shrinks to the B and C entries. The overrides file stays for policy.

## Steps

1. You export the 15 designs above. I decode each with the current decoder and write down
   what cadmpeg gives against what Fusion shows. That tells which rows are real decoder gaps
   and which are already decoded.
2. Per A row, in the order above: a branch in `~/cadmpeg` off upstream `main`, the decoder
   change, a spec note (DR entry), tests, and the CC0 save. I draft the PR text for your
   approval. You tick the sign-off. Nothing is pushed or opened without your go-ahead.
3. After each merge: rebuild the decoder, switch the rule to fallback-only, run the benchmarks,
   then delete the rule once a release decoder has the field.
4. Last: port to v11 natively (D) and drop `fusion_raw.py` and `ir_compat.py`.

## Decisions for you

- **Provenance of the test saves.** I built them in your Fusion account and you exported them.
  The PR template's statement says the files were "created by me". Whether that covers designs
  made by Claude in your account is your call. The PR text can say so explicitly, as the
  existing "with AI assistance" sentence does.
- **Name typos.** Cases 7 and 8 have typos in their names. Rename them in Fusion before export,
  or let the corpus file names fix them.
- **Order.** A1–A3 (assemblies and visibility) come first because they change what you see
  most. A15 could go earlier if the thread import errors matter more.
