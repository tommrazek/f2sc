# Contributions to cadmpeg

> **16 Nov:** at the user's request the chaser_fluidic design and the reproductions built from it were removed from this repository's current files. The evidence below that refers to it stays as a record.

The converter needs some cadmpeg fixes that upstream does not have yet. They live in the cadmpeg
clone **beside** this repository, `../cadmpeg` (on this machine `~/cadmpeg`):

- `f3d/<topic>`: one branch per pull request, each a single commit on upstream `main`.
- `f3d2sc-next` (checked out; **the decoder builds from this**): upstream `main` at `b60c4a8089`
  (15 Nov, evening) plus every `f3d/<topic>` commit that upstream has not merged.
- `f3d2sc`: the fixes on the older upstream `main` at `cd06f603a5` (the decoder's branch until
  the focused session of 15 Nov; kept for comparison, `bin/f3d2sc-decode.f3d2sc`).
- New topic branches are worked in worktrees under `~/cadmpeg-wt/` (shared cargo target
  `~/cadmpeg-wt/target`, delete it to free space).

`cadmpeg-f3d2sc.bundle` carries all of them until they are on a fork; the README (Decoder:
cadmpeg checkout) shows how to fetch it. Of the eight pull requests (cadmpeg/cadmpeg #1357–#1364, fork `tommrazek/cadmpeg`), upstream merged
#1358, #1359, #1360 and #1363 on 15 Nov; #1357, #1361, #1362 and #1364 are open. Topics 9 and 10
below are ready on their branches and **not pushed** (texts in `CADMPEG-PRS.md`, waiting for your OK).

Every topic is complete for a pull request: code, specification (`f3d.md`), layout table entries
(`f3d.toml`), open items and tests. cadmpeg squash-merges pull requests, so each topic is one
commit.


Changes to the converter (`f3d2sc.py`, `inference.py`) stay in this repository. They are not
cadmpeg contributions; `ISSUES.md` lists them with their commits.

## Pull-request topics

| # | Topic | What it fixes | Branch | cadmpeg item |
|---|---|---|---|---|
| 1 | Revolve direction kind 1 and the 361-byte referenced-angle frame | Revolves with direction byte 1 (full turn) and the 361-byte frame decoded instead of left out. The 361-byte frame is the 88-byte scope tail with a second marked reference; the scope parser now reads that tail's preceding history state for every family. | `f3d/revolve-referenced-angle-frame` | DR-269 (updated), DR-646 (new; was DR-641, which upstream has since used) |
| 2 | Manifest export marker after a display name | `NA_EXPORT` / `NA_OFFLINESAVE` after a terminal display name accepted; a lineage suffix after the marker refused | `f3d/manifest-export-marker` | — |
| 3 | Wide parameter trailer, discriminator 5 | Parameters with the discriminator-5 wide expression trailer decoded | `f3d/wide-expression-trailer-5` | — |
| 4 | Extended Thread scopes | Thread scopes decoded in record order, with hand and face count; trailer owners bound to local ordinals | `f3d/extended-thread-scopes` | issue #448 (Thread has no decode path); DR-142 (partly) |
| 5 | Preceding plane or cylinder of a face support context | The surface a face had before the feature (used to find thread faces) | `f3d/face-support-preceding-surface` | — |
| 6 | Deleted bodies | A body that a later state still holds is not reported as deleted | `f3d/deleted-bodies-held-by-state` | issue #1072 (related) |
| 7 | Origin identities with several components | A stream stores one origin per component. When the complete origin runs match the components, a partial run is not an origin. Upstream #1232's WorkPlane reading then applies (film scanner Mirror 4's plane) | `f3d/origin-identities-per-component` | — |
| 8 | Mirror plane member as an entity selection | A Mirror plane member is read as an entity selection when the face recipe program starts after the selection frame (test_asy Side Panels Mirror 4) | `f3d/mirror-plane-entity-selection` | — |

The earlier formatting commit and the commit that completed topics 5 and 6 are folded into the
topic branches (topic 5 and 6 each take their own half of it).
| 9 | Chamfer flip byte | Byte 22 of a two-distance or distance-angle chamfer set is the flip state. With 0 the (first) distance lies on the face whose coedge runs with the edge, with 1 on the other face. The parser refused a flipped set; `flip_direction` is now decoded | `f3d/chamfer-set-flip` | DR-341 (narrowed to tangent chains and split faces) |
| 10 | Current pose from a component path with a legacy target | An inserted part's current pose is stored in a component-path record. Older files name the part there by a legacy target reference, which cadmpeg did not read, so the occurrence kept an earlier matrix. Fixes the three M2 screws and the Spring of the chaser | `f3d/legacy-root-component-path-pose` | — |

## PR 11 (#2260, opened 25 Nov): Component Insert moved by a joint

Branch `f3d/component-insert-binds-external-occurrence` (`~/cadmpeg`, one commit on upstream
`fd3cdb8c49`, on the fork): a 400-byte Component Insert scope binds to the attributed carrier its
relation names even when the carrier's matrix differs (the occurrence moved after the
insertion), and the carrier's matrix keeps precedence. CC0 test file
`corpus/f3d/component_insert_moved_by_joint.f3z` (= `benchmarks/f2sc_screws.f3z`). cadmpeg's
library tests (6364) pass, clippy is clean, preflight passes except the golden tests of the other
codec crates, left to CI (they need a workspace build that does not fit this machine's free disk).
Text in `CADMPEG-PRS.md`.

## Next cadmpeg topics (25 Nov, upstream `61d47445e0`)

All ten earlier topics are merged. Two new ones, found on a private test assembly (ISSUES A1, A2), each now worked
around in the converter from cadmpeg's native records; they belong in cadmpeg with a CC0 Fusion
save (an assembly with one linked screw design inserted by several Screw features, and a linked
sub-assembly moved by a joint after insertion):

- **One occurrence per Screw feature.** A Screw scope inserts one occurrence of the fastener
  design its library JSON names; its current world pose is the component-path record (one
  element, discriminator 0) naming the record just before the scope's first reference, in
  centimetres. cadmpeg reports these records as "component-path carriers name no unique expanded
  local occurrence" and keeps one occurrence of the design at the origin.
- **Current pose of an external Component Insert.** The same component-path record exists for
  a Component Insert's occurrence; it holds the current pose (Fusion's display pose agrees),
  while cadmpeg keeps the insert's decoded placement.

## Results of the focused session (15 Nov, evening)

Offsets are in the design's `BulkStream.dat`. The chaser files are private: describe them in a
cadmpeg pull request without naming them.

### Settled

- **Screw placements (task 0, ISSUES C12).** A Screw scope does not need decoding for the pose.
  Each inserted part's current world pose is in a component-path record (`CommonData` type
  `3B6814F4…`) beside its placement record: records 18291, 19052 and 19061 for the placements
  18190, 19051 and 19060. Their path names the part's root component by a *legacy target
  reference* (no link name), which cadmpeg refused. With topic 10 the screws sit at
  (6.93, 10, 4), (0, 10, −8) and (−6.93, 10, 4) mm, and the Spring moves from (0, 22.4, 18.6) to
  (0, 61, 0). The file's saved display poses (`OGS…/world`) give the same four poses.
  For the record, the Screw scope itself: after its header a JSON blob (library data; it holds a
  local file path, never quote it), LP-ASCII `Bolt`, a marked reference to its 58-byte relation
  record (which names the placement, as for Component Insert), and the pose before the Screw
  moved the part. Record N+4 after the scope holds that pose again and the hole frame.
- **DR-341, chamfer side (task 2).** Topic 9. Evidence: the four `dr341-*` samples and the
  Piston. The converter passes `flip_direction` on, but still needs `chamfer_side_by_final_shape`
  under `--check` (it fires for the Piston and for `dr341-chamfer-cyl-flip`): SolidCraft's own
  default face is not Fusion's, and cadmpeg's IR does not yet say which face of the edge has the
  coedge that runs with it. Upstream #1145 (open, stale) would add those faces to the input
  edge; with them the converter can choose the side without `--check`.
- **Converter on current upstream (task 6, ISSUES N1).** Done; see ISSUES.md.

### DR-340 (task 1): the Piston does not contradict upstream's rule; the open question is another one

- The three `dr340-regions-*` samples decode correctly on current upstream (rectangle minus
  notch, notch, both).
- Piston Revolve 1's record (selection record 23524 at 760279, identity record 760637) is the
  *nested* form, not the compact one. Its selected region (9 curves, curve 103 as piece 1 of 2)
  **is the union** of its two atomic regions (the outline 107, 109, 103, 112, 111, 105 and the
  strip 130, 128, 132, 103 piece 2 of 2), exactly upstream's rule. The earlier note "initial =
  further 1 − further 2" was wrong; no Conflict is to be filed.
- The real cause: the sketch was edited after the Revolve picked its profile. Curve 103 now ends
  at v = 4.6 (it has no second piece), 105 starts at u = −50.46, and three curves (138, 140, 142:
  a 0.1 mm × 31 mm groove on the v = 6 edge) were added that the stored selection never names.
  The atomic regions no longer close in the sketch; the selected region's own loop still does.
- What Fusion revolved: the state after Revolve 1 has 11 faces, which is the 9-curve region
  **minus the groove** (the whole region would give 8). So of the two arrangement faces inside
  the stored loop Fusion took one. The file states no reason that I could find.
- **Still open:** which faces of the current sketch a selection stored before a sketch edit
  names. A fix in cadmpeg would be a Revolve transition certificate (as upstream #1345 did for
  Extrude): pick the arrangement faces whose revolved boundary equals the inserted faces.
- **Save that would settle it:** sketch a 20 × 10 rectangle, full-turn Revolve it about one
  edge, then edit the sketch and add a small closed notch on an outer edge; save
  (`dr340-edited-a.f3d`). The same with the added lines splitting the rectangle in two halves
  (`dr340-edited-b.f3d`). Note for each what the body looks like after the edit.
- **Converter today:** inference `revolve_region_from_initial_loop` (kept, description
  corrected): the face that runs along every curve of the stored selected region.

### Stack_Main Extrude 7 (task 3): the transition holds three features

- The failing condition is the third: inserted faces whose surface has no geometry rows, 70 of
  the 141 faces state 1706 inserts (it deletes 23). No face has several bindings or plane rows.
- They are no stored alternatives. The states of Thread 2 (1685) and C-Pattern 2 (1705), and
  1695 and 1696 between them, store **empty** deltas; state 1706 (Extrude 7) carries all their
  faces, the spline flanks of the modeled thread among them. A cut of three faces does not
  make 141 faces, so this transition cannot certify Extrude 7 alone.
- Extrude 7's profile is three body-face operands (group 1016111, members 49221, 49225, 49229),
  each with 26 candidate faces.
- **Save that would settle it:** a block with a hole, a *modeled* thread on it, a circular
  pattern of the thread, then an Extrude cut started from a picked body face. It shows whether
  Fusion always stores the thread's and pattern's faces in the next feature's state.

### M2 screw Extrude 2 (task 4): the profile is a body face; c240 is unrelated

- Curve 240 belongs to Sketch 7 (5619), the sketch of Revolve 3: it is the line on the revolve
  axis. It has nothing to do with Extrude 2.
- Extrude 2 (scope 55101; a 0.1 mm cut with an 85° taper, the cone at the bottom of the hex
  socket) takes one body-face operand (group 65291, member 5331, a bounded-face recipe): the
  bottom face of the socket that Extrude 1 cut from Sketch 6's hexagon. cadmpeg does not bind
  that recipe to one face. Upstream #1312 (open) works on such recipes; check after it merges.
- No save needed yet.

### Stack_Front_1 Fillet 3 (task 5): 20 operands, 36 deleted edges

- The group (1250121) has 20 edge operands, none of which resolves alone (5 to 9 candidate
  faces each). The transition 800 → 803 deletes 36 edges and inserts 37 faces, so cadmpeg's
  rule for a treatment group (deleted edges = members, DR-361) cannot apply. Four edges of the
  state have no interval samples (helix edges of the modeled thread).
- So the fillet took more edges than it names: a tangent chain, or edges the thread split.
- **Save that would settle it:** a body where a fillet with tangent chain on, picked on one
  edge, rounds a chain of several; and one where the picked edge was split by a later-edited
  earlier feature.

### DR-646, DR-327 / DR-338
Unchanged (no samples).

## Review for the focused session (15 Nov): what would help, item by item

Upstream moved 25 commits since the branch was written. Two changes bear on these items:
- #1308 ("read every selected region and its atomic regions in a nested profile-region
  selection") added to DR-340's Note that the compact loop group "can follow the same rule": each
  selected region is followed by the atomic regions that make it up. **Our Piston data does not
  fit that rule.** Its further region 1 is the whole outline, larger than the initial region, so
  it cannot be one of the initial region's atomic regions. Instead initial = further 1 − further 2.
  That belongs in DR-340 as a **Conflict**, with the record offsets above, in a cadmpeg PR.
- The new DR-644 (atomic regions with holes) is the nested form's version of the same question.

Below, "Fusion save" means a small design you build in Fusion and save as `.f3d` into
`benchmarks/dummies/`, like `threads.f3d`. These are files you made, so cadmpeg's clean-room
rules allow them, and each could later go to cadmpeg's corpus through its donation pipeline if
you want. Saves that differ only in the setting in question are what settle a byte: the session
diffs them. Please keep everything else identical between the saves (same sketch, same order of
operations), and name each file after its variant.

| Item | Fusion saves would help? | What to build |
|---|---|---|
| **DR-340** compact region selection (Piston Revolve 1) | **Yes: 3 files** (`benchmarks/dummies/dr340-regions-{a,b,c}.f3d`). | Sketch on XY: a 20 × 10 mm rectangle, corner at the origin, with a notch on its bottom edge from x = 8 to 12, 0.5 mm deep, drawn as lines starting and ending on that edge. Full-turn Revolve about the rectangle's left edge. Saves differ only in the profile picked: (a) the rectangle minus the notch, (b) the notch only, (c) both. |
| **DR-341** chamfer distance side (Piston Chamfer 1) | **Yes: 4 files** (`benchmarks/dummies/dr341-chamfer-*.f3d`). | (1) A cylinder r = 5 mm, h = 20 mm, distance-angle chamfer 1 mm at 20° on its top circular edge: `dr341-chamfer-cyl.f3d`, and with the flip button pressed `dr341-chamfer-cyl-flip.f3d`. (2) A 20 mm cube, two-distance chamfer 1 mm / 3 mm on one top edge: `dr341-chamfer-box.f3d` and `dr341-chamfer-box-flip.f3d`. |
| **DR-646** Revolve direction kind 1, second tail reference | Not requested for now. Would need saves, but kind 1 has only been seen in fastener-library parts, so which dialog setting writes it is unknown. | One sketch with a 10 × 5 mm rectangle; Revolve by 90° about a sketch line, one save per direction setting (one side, flipped, symmetric, two sides), then the same about an origin axis. If none writes kind 1, a library fastener inserted into a design and saved, with its revolve's direction changed. |
| **DR-327 / DR-338** circular-pattern axes | Not requested for now (saves listed for later). | A 40 mm disc with one off-centre 5 mm boss. Circular pattern of the boss (feature pattern) 3 instances over 90°, about: the Z origin axis, a linear edge, a circular edge, a construction axis, a sketch line; for one of them also the flipped direction and a full turn. Then the same as a body pattern. |
| Stack_Main Extrude 7 (transition proof) | **Not yet.** The existing file should first show which of the three conditions in `face_profile_sets.rs` fails; a save is only useful once that is known. | — |
| M2 screw Extrude 2 ("no unique neutral profile", curve c240 in no loop) | **Not yet**; first see whether c240 is a projected or construction curve the profile ignores. If so: a sketch with a closed rectangle plus one dangling projected edge, extruded. | — |
| Stack_Front_1 Fillet 3 (edges do not bind) | **Not yet**; first see whether the unbound edges are the ones a thread or a pattern rewrote earlier. If so: a fillet on a threaded or patterned body's edges. | — |

Order for the session: DR-340 and DR-341 first, with the 7 files above (both feed converter inferences that would then go away), then the three "not yet" diagnoses on the files we have. DR-646 and the pattern items wait for saves.
