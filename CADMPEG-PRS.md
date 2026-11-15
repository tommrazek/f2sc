# cadmpeg pull requests (opened 15 Nov)

Eight pull requests, one per contribution, against `cadmpeg/cadmpeg` `main`, opened 15 Nov from
the fork `tommrazek/cadmpeg` with the texts below (approved, sign-off boxes ticked by you). Each is one commit on
its own branch in `~/cadmpeg`, based on upstream `main` at `29f0038579` (rebased 15 Nov, no
conflicts):

| # | PR | Branch (`~/cadmpeg`) | Title |
|---|---|---|---|
| 1 | [#1357](https://github.com/cadmpeg/cadmpeg/pull/1357) | `f3d/revolve-referenced-angle-frame` | f3d: read the 361-byte referenced-angle Revolve frame and direction kind 1 |
| 2 | [#1358](https://github.com/cadmpeg/cadmpeg/pull/1358) | `f3d/manifest-export-marker` | f3d: accept the export marker after a terminal display name |
| 3 | [#1359](https://github.com/cadmpeg/cadmpeg/pull/1359) | `f3d/wide-expression-trailer-5` | f3d: read the wide expression trailer with discriminator 5 |
| 4 | [#1360](https://github.com/cadmpeg/cadmpeg/pull/1360) | `f3d/extended-thread-scopes` | f3d: decode extended Thread scopes in record order, with hand and face count |
| 5 | [#1361](https://github.com/cadmpeg/cadmpeg/pull/1361) | `f3d/face-support-preceding-surface` | f3d: state the preceding plane or cylinder of a face support context |
| 6 | [#1362](https://github.com/cadmpeg/cadmpeg/pull/1362) | `f3d/deleted-bodies-held-by-state` | f3d: do not report a body a state still holds as deleted |
| 7 | [#1363](https://github.com/cadmpeg/cadmpeg/pull/1363) | `f3d/origin-identities-per-component` | f3d: classify identities beside every listed origin as constructed |
| 8 | [#1364](https://github.com/cadmpeg/cadmpeg/pull/1364) | `f3d/mirror-plane-entity-selection` | f3d: read a Mirror plane member as its entity selection over a later face recipe |

The eight are independent: each applies to `main` alone, and together they give the
integration branch `f3d2sc-next`. They can be opened and merged in any
order. Checks per branch are listed under "Checks run" at the end.

## Decisions taken before opening

1. **Provenance.** Confirmed by you on 15 Nov. Each description carries the template's
   sentence, CONTRIBUTING.md's verbatim sentence, and a specific statement: the CAD files were
   created and are owned exclusively by you, contain no NDA-covered material, and the format
   knowledge was derived from them with AI assistance (Claude Opus 5.5 and Claude Fable 5.2).
   Texts approved by you on 15 Nov.
2. **DCO.** You ticked the `Signed off` box of each text yourself; the PRs carry it.
3. **Fork.** `tommrazek/cadmpeg`, remote `fork` in `~/cadmpeg`. After a change to a topic
   branch: `git -C ~/cadmpeg push --force-with-lease fork f3d/<topic>`.
4. **Private designs.** The descriptions name no design. The chaser and film scanner files
   stay out of the PRs; no CAD file is committed.
5. **DR number.** Upstream took DR-641 for another item after our branch was written, so the
   Revolve open item is now **DR-646**. If another open PR claims 646 first, preflight will
   flag it and it gets the next free number.

---

## PR 1 (#1357): f3d: read the 361-byte referenced-angle Revolve frame and direction kind 1

Branch `f3d/revolve-referenced-angle-frame`

### Summary

Referenced-angle `Revolve` scopes occur in two forms the decoder did not read:

- **The 361-byte frame.** It ends in the 88-byte fixed scope tail: the 78-byte tail with a
  second marked reference at tail offset 30, and its preceding history-state identity at tail
  offset 41, as in the 87-byte form. The scope parser now reads that identity for every
  family, so these Revolve scopes keep their preceding state. The Revolve decoder recognises
  the frame by its tail, not by the bytes after it (those hold the preceding state).
- **Direction kind 1** at prologue offset 33 (seen in fastener-library parts). What the byte
  selects is not established, so it is admitted only for a full turn, where the swept solid
  does not depend on the direction. A partial revolve with kind 1 stays unresolved. The
  full-turn tolerance is a named constant.

Specification (`f3d.md` §3.1) states the 88-byte tail and direction kinds 0 and 1; the layout
table carries the offsets and the decoder reads them from it. DR-269 lists the 361-byte length.
The new **DR-646** asks what direction kind 1 and the second tail reference select, and names
the saves that would settle it. The decoder reports `feature.revolve-layout-assumed` with the
reference's target for the 361-byte frame.

Tests (`design/decode/scopes/tests/referenced_revolve.rs`): direction kind 1 reads only for a
full turn; a 361-byte frame carries a second tail reference and reports the loss.

### Breaking change

### Checklist

- [X] Signed off: I certify this PR under the [Developer Certificate of Origin](https://developercertificate.org/). See [CONTRIBUTING.md](../CONTRIBUTING.md).

### Provenance declaration (decoder or format-spec changes only)

I derived the format knowledge in this contribution only from sources allowed by LEGAL.md.

I derived this contribution solely by analyzing CAD files I am legally entitled to possess and from publicly available information. I did not use vendor SDKs, decompiled binaries, confidential or NDA-covered material, or any source listed as forbidden in LEGAL.md.

The CAD files analyzed for this contribution were created by me and are owned exclusively by me; none of them is or contains NDA-covered material. The format knowledge was derived from these files with AI assistance (Claude Opus 5.5 and Claude Fable 5.2).

---

## PR 2 (#1358): f3d: accept the export marker after a terminal display name

Branch `f3d/manifest-export-marker`

### Summary

A document saved offline (seen on a library part placed in an assembly) ends its top-level
manifest with terminal byte 0, a display name, and then `NA_OFFLINESAVE` where a lineage URN
usually stands. The terminal-byte-1 form already accepts `NA_EXPORT` and `NA_OFFLINESAVE`; the
terminal-byte-0 form now accepts them after the display name too. Before, such an assembly's
part did not open.

The lineage suffix follows only a lineage URN, so a marker followed by a suffix does not parse.
The top-level manifest grammar in `f3d.md` lists both markers.

Tests (`manifest/tests.rs`): both markers, the refused suffix, and an unknown marker.

### Breaking change

### Checklist

- [X] Signed off: I certify this PR under the [Developer Certificate of Origin](https://developercertificate.org/). See [CONTRIBUTING.md](../CONTRIBUTING.md).

### Provenance declaration (decoder or format-spec changes only)

I derived the format knowledge in this contribution only from sources allowed by LEGAL.md.

I derived this contribution solely by analyzing CAD files I am legally entitled to possess and from publicly available information. I did not use vendor SDKs, decompiled binaries, confidential or NDA-covered material, or any source listed as forbidden in LEGAL.md.

The CAD files analyzed for this contribution were created by me and are owned exclusively by me; none of them is or contains NDA-covered material. The format knowledge was derived from these files with AI assistance (Claude Opus 5.5 and Claude Fable 5.2).

---

## PR 3 (#1359): f3d: read the wide expression trailer with discriminator 5

Branch `f3d/wide-expression-trailer-5`

### Summary

A wide discriminated parameter frame (discriminator `u32` at offset 26, comment at 31) with
family discriminator 5 carries the same 11-byte expression trailer as discriminators 0 and 6:
text flag, five zero bytes, driven, reserved, favorite, reserved, document.

Before this, every such frame fell through to the 9-byte trailer, failed, and was counted as an
unparsed parameter frame. A design that writes all its feature parameters this way therefore
decoded with no parameters and no parameter owners at all.

The specification and the layout table state the trailer for discriminators 0, 5 and 6.

Tests (`design/decode/parameters/tests/tail_forms.rs`): a discriminator-5 frame reads; a
discriminator-4 frame is still refused.

### Breaking change

### Checklist

- [X] Signed off: I certify this PR under the [Developer Certificate of Origin](https://developercertificate.org/). See [CONTRIBUTING.md](../CONTRIBUTING.md).

### Provenance declaration (decoder or format-spec changes only)

I derived the format knowledge in this contribution only from sources allowed by LEGAL.md.

I derived this contribution solely by analyzing CAD files I am legally entitled to possess and from publicly available information. I did not use vendor SDKs, decompiled binaries, confidential or NDA-covered material, or any source listed as forbidden in LEGAL.md.

The CAD files analyzed for this contribution were created by me and are owned exclusively by me; none of them is or contains NDA-covered material. The format knowledge was derived from these files with AI assistance (Claude Opus 5.5 and Claude Fable 5.2).

---

## PR 4 (#1360): f3d: decode extended Thread scopes in record order, with hand and face count

Branch `f3d/extended-thread-scopes` · Refs #448

### Summary

The extended standard `Thread` form now covers the scopes of a controlled design that saves one
thread per dialog setting: cosmetic and modeled, full length and a set length, an offset, left
hand, one and two faces, and an internal thread.

- The prefix may sit behind the owner marker, with its fields four bytes later.
- The entry count is one 16-byte entry per face of a modeled thread.
- The first construction-marker byte is the full-length flag, with second byte 1 or 2. The
  pitch marker is 1 only on a modeled thread with a result.
- The trailer byte after the offset-owner reference is the right-handed flag (0 on a
  left-handed thread). `DesignThreadExtended` gains `right_handed`.
- The scope reference table is in record order, so the trailer's length and offset references
  and its counted face-group list decide which reference is which. They must name the owners
  with local ordinals 1 and 0, so a trailer that swaps them does not decode.
- A positive offset is admitted.

The specification, the layout table and its rendered page, and open items DR-142 and DR-205
record what the controlled design settles; the remaining questions stay in those items.

This overlaps the open #1140, which also reads the owner-marked prefix four bytes later. The two
readings differ in places: #1140 takes the byte at marker offset 21 as the modeled flag
independent of the entry count and leaves a marker with second byte 2 unread, while this PR
reads the entry count as one entry per modeled face and admits second byte 1 or 2 (the
full-length setting). The controlled design behind this PR has one save per thread setting;
happy to reconcile with #1140 on whichever reading the maintainers prefer.

Tests (`design/decode/scopes/tests/thread.rs`, `extended_thread.rs`, the feature dispatcher)
follow the specification: a modeled thread without a result stores result flag 0, a modeled
thread stores one prefix entry per face, the extended standard form admits a positive offset,
and swapped trailer owners are refused.

### Breaking change

### Checklist

- [X] Signed off: I certify this PR under the [Developer Certificate of Origin](https://developercertificate.org/). See [CONTRIBUTING.md](../CONTRIBUTING.md).

### Provenance declaration (decoder or format-spec changes only)

I derived the format knowledge in this contribution only from sources allowed by LEGAL.md.

I derived this contribution solely by analyzing CAD files I am legally entitled to possess and from publicly available information. I did not use vendor SDKs, decompiled binaries, confidential or NDA-covered material, or any source listed as forbidden in LEGAL.md.

The CAD files analyzed for this contribution were created by me and are owned exclusively by me; none of them is or contains NDA-covered material. The format knowledge was derived from these files with AI assistance (Claude Opus 5.5 and Claude Fable 5.2).

---

## PR 5 (#1361): f3d: state the preceding plane or cylinder of a face support context

Branch `f3d/face-support-preceding-surface`

### Summary

`DesignHistoricalFaceSupportContext` gains `preceding_surface`: the plane (origin, normal) or
right-circular cylinder (axis origin, axis direction, radius) that the preceding state stores
exactly once for the context's surface carrier, with no row of the other kind.

A consumer of a historical face selection, such as the face a `Thread` is put on, can then place
the face without the final B-rep, which no longer has that face once the feature rewrites the
topology.

The support-context builder and its surface-geometry helper move from `history.rs`, which
exceeded the production file-size limit, into `history/face_support.rs`, with no behaviour
change; callers import from the new module. `f3d.md` states the retained surface.

Tests (`history/tests/selections/face_support.rs`): a unique plane row, a unique cylinder row,
mixed rows, a repeated row, and no row.

### Breaking change

### Checklist

- [X] Signed off: I certify this PR under the [Developer Certificate of Origin](https://developercertificate.org/). See [CONTRIBUTING.md](../CONTRIBUTING.md).

### Provenance declaration (decoder or format-spec changes only)

I derived the format knowledge in this contribution only from sources allowed by LEGAL.md.

I derived this contribution solely by analyzing CAD files I am legally entitled to possess and from publicly available information. I did not use vendor SDKs, decompiled binaries, confidential or NDA-covered material, or any source listed as forbidden in LEGAL.md.

The CAD files analyzed for this contribution were created by me and are owned exclusively by me; none of them is or contains NDA-covered material. The format knowledge was derived from these files with AI assistance (Claude Opus 5.5 and Claude Fable 5.2).

---

## PR 6 (#1362): f3d: do not report a body a state still holds as deleted

Branch `f3d/deleted-bodies-held-by-state` · Related to #1072

### Summary

A feature's `deleted_bodies` were the solid bodies of the preceding state that are not solid
bodies of its own state. A body that the state still holds but whose shell the solid test
cannot close (the body a `Hole` drills, for example) was therefore reported as deleted by the
`Hole`, although the next state and the final B-rep still have it. A body slot present in the
feature's state is no longer counted as deleted.

`f3d.md` defines `deleted_bodies` as the solid bodies of the preceding state that the feature's
own state does not hold.

Tests (`history/tests/feature_outputs/deleted_bodies.rs`): an open body the state still holds is
not deleted; a removed body is.

### Breaking change

### Checklist

- [X] Signed off: I certify this PR under the [Developer Certificate of Origin](https://developercertificate.org/). See [CONTRIBUTING.md](../CONTRIBUTING.md).

### Provenance declaration (decoder or format-spec changes only)

I derived the format knowledge in this contribution only from sources allowed by LEGAL.md.

I derived this contribution solely by analyzing CAD files I am legally entitled to possess and from publicly available information. I did not use vendor SDKs, decompiled binaries, confidential or NDA-covered material, or any source listed as forbidden in LEGAL.md.

The CAD files analyzed for this contribution were created by me and are owned exclusively by me; none of them is or contains NDA-covered material. The format knowledge was derived from these files with AI assistance (Claude Opus 5.5 and Claude Fable 5.2).

---

## PR 7 (#1363): f3d: classify identities beside every listed origin as constructed

Branch `f3d/origin-identities-per-component`

### Summary

A Design stream stores one origin per component. When the type table lists as many complete
origin runs as it registers component naming-space entities, every origin is listed, and a
partial run cannot hold an origin. An axis- or plane-type identity outside the complete runs
then names a constructed entity.

So a `Mirror` plane selection of a construction plane in a multi-component design reads the
WorkPlane at the identity plus one (#1232) instead of staying unclassified, and the Mirror
resolves.

`f3d.md` states the rule. Tests: a plane beside every listed origin is not an origin; a plane
stays unknown while a component origin is unlisted.

### Breaking change

### Checklist

- [X] Signed off: I certify this PR under the [Developer Certificate of Origin](https://developercertificate.org/). See [CONTRIBUTING.md](../CONTRIBUTING.md).

### Provenance declaration (decoder or format-spec changes only)

I derived the format knowledge in this contribution only from sources allowed by LEGAL.md.

I derived this contribution solely by analyzing CAD files I am legally entitled to possess and from publicly available information. I did not use vendor SDKs, decompiled binaries, confidential or NDA-covered material, or any source listed as forbidden in LEGAL.md.

The CAD files analyzed for this contribution were created by me and are owned exclusively by me; none of them is or contains NDA-covered material. The format knowledge was derived from these files with AI assistance (Claude Opus 5.5 and Claude Fable 5.2).

---

## PR 8 (#1364): f3d: read a Mirror plane member as its entity selection over a later face recipe

Branch `f3d/mirror-plane-entity-selection`

### Summary

A `Mirror` plane member is one entity selection or one face-recipe operand, and a face-recipe
member keeps its recipe before the indexed header that follows its N+3 record. A member that
also reads as a face operand whose recipe program starts past the header that closes its
entity-selection frame is that entity selection. An origin-plane selection then binds instead
of staying unresolved.

`f3d.md` states the rule. A binder test (`history/tests/planes.rs`) builds an origin-plane
selection whose member also reads as a face operand with a later recipe, and checks that the
plane binds.

### Breaking change

### Checklist

- [X] Signed off: I certify this PR under the [Developer Certificate of Origin](https://developercertificate.org/). See [CONTRIBUTING.md](../CONTRIBUTING.md).

### Provenance declaration (decoder or format-spec changes only)

I derived the format knowledge in this contribution only from sources allowed by LEGAL.md.

I derived this contribution solely by analyzing CAD files I am legally entitled to possess and from publicly available information. I did not use vendor SDKs, decompiled binaries, confidential or NDA-covered material, or any source listed as forbidden in LEGAL.md.

The CAD files analyzed for this contribution were created by me and are owned exclusively by me; none of them is or contains NDA-covered material. The format knowledge was derived from these files with AI assistance (Claude Opus 5.5 and Claude Fable 5.2).

---

## Checks run

Run on each branch on 15 Nov, on upstream `main` at `32694147f2`, and again on all 8 combined after
the rebase on `29f0038579`:

| Check | Result |
|---|---|
| `cargo fmt --all --check` | pass on all 8 |
| `cargo clippy -p cadmpeg-codec-f3d --all-targets` (warnings denied) | pass on all 8 |
| `cargo test -p cadmpeg-codec-f3d` | pass on all 8 (about 4,975 tests each, 0 failed) |
| `python3 scripts/preflight.py --offline --run-layout` | 0 FAIL on all 8 |
| Combined | the 8 branches apply in turn without conflict and give `f3d2sc-next`; its decode of every benchmark has 0 regressions against the previous decoder (`decode_diff.py`) |

Fixed while splitting, because upstream added the rules after our branch was written:
- PR 5: `preceding_surface` now declares its absent-key spelling with
  `cadmpeg_core::named_optional_field!` (the new deny census, `check-deny-census.py`).
- PR 3 and PR 4: the rendered layout page `docs/layouts/f3d.md` regenerated so each branch
  carries only its own line.

Preflight warnings left, with the answer for the PR description if review asks:
- PR 4 and PR 5, `ir-version`: serialized types outside `cadmpeg-ir` changed
  (`DesignThreadExtended.right_handed`, `DesignHistoricalFaceSupportContext.preceding_surface`).
  These are native f3d records in `cadmpeg-codec-f3d`, not the CADIR wire, so `IR_VERSION` is
  unchanged.
- PR 6, `spec-wording`: the flagged line is an existing long line of `f3d.md`; the word it
  matches ("samples") is upstream text, not ours.

Not run: the `open-prs` check (it needs `gh`), so overlap with other open pull requests is
not checked; the workspace-wide `cargo test-fast` (only the f3d codec crate and the layout
tables were run; nothing else depends on these files, as `dependent-goldens` confirms).

---

# From the focused session (15 Nov, evening): opened as #1377 and #1378

Two more branches, each one commit on upstream `main` at `b60c4a8089`, in worktrees under
`~/cadmpeg-wt/`. `python3 scripts/preflight.py --full` reports 0 FAIL on both. They wait for your
OK (and your tick in the sign-off box) before anything is pushed. Upstream status of the first
eight: #1358, #1359, #1360 and #1363 merged; #1357, #1361, #1362, #1364 open.

## PR 9 (#1377, opened 15 Nov): f3d: read the flip byte of two-distance and distance-angle chamfer sets

Branch `f3d/chamfer-set-flip` (worktree `~/cadmpeg-wt/chamfer`)

### Summary

A two-distance or distance-angle chamfer set stores a flip byte at offset 22 (`0` or `1`).

- With `0`, the first distance of a two-distance set, or the distance of a distance-angle set,
  is the setback along the face whose coedge runs in the direction of the treated edge in the
  state before the feature. The second distance is the setback along the other face.
- With `1` the two faces exchange roles.
- For perpendicular faces, the angle lies between the chamfer face and the face that takes the
  distance.

The set parser admitted only a zero byte at offset 22, so a flipped set left its scope without
a set list. The set record now keeps the byte as `flipped`, and the projected Chamfer sets
`flip_direction` when one of its sets is flipped (with a loss note when its sets disagree).

Evidence: four controlled saves that differ only in the flip button (a 1 mm / 3 mm two-distance
chamfer on a straight box edge, and a 1 mm / 20° distance-angle chamfer on the closed circular
edge between a plane and a cylinder), and a fifth design with a distance-angle chamfer on such
an edge. In each, the result's setbacks agree with the rule above.

`f3d.md` §3.1 and the layout table state the byte. DR-341 now asks only for tangent chains and
for support faces split by earlier operations. Open #1145 states the unflipped half of this
rule for two-distance chamfers and records the two faces on the input edge; this change is
independent of it and adds the flip state.

Tests: the byte in both forms, its refusal in the equal-distance forms, and the projected flip
state for agreeing and disagreeing sets.

### Breaking change

### Checklist

- [ ] Signed off: I certify this PR under the [Developer Certificate of Origin](https://developercertificate.org/). See [CONTRIBUTING.md](../CONTRIBUTING.md).

### Provenance declaration (decoder or format-spec changes only)

I derived the format knowledge in this contribution only from sources allowed by LEGAL.md.

I derived this contribution solely by analyzing CAD files I am legally entitled to possess and from publicly available information. I did not use vendor SDKs, decompiled binaries, confidential or NDA-covered material, or any source listed as forbidden in LEGAL.md.

The CAD files analyzed for this contribution were created by me and are owned exclusively by me; none of them is or contains NDA-covered material. The format knowledge was derived from these files with AI assistance (Claude Opus 5.5 and Claude Fable 5.2).

---

## PR 10 (#1378, opened 15 Nov): f3d: read the current pose of a placement from a component path with a legacy target

Branch `f3d/legacy-root-component-path-pose` (worktree `~/cadmpeg-wt/pose`)

### Summary

A component-path record (`CommonData` type `3B6814F4-D87B-46BD-AD9D-0BE07880F7BB`) stores the
current world pose of a placed occurrence. In designs whose placements are attributed legacy
occurrence placements, the record names the referenced design's root component by the same
legacy target reference the placement uses (`u8 1`, u64 entity, `u8 1`, u64 source occurrence
identity, `u8 0`, LP-ASCII type GUID, `u8 0`). That element stores no link name, and the path
reader refused it, so an external occurrence at the document root kept the earlier matrix of
its placement record.

The record has exactly two path elements: the legacy target of type
`E03784ED-5E19-4E14-B9F2-3B07017018CD`, then the local occurrence-placement record. It binds to
the placement whose own legacy target has the same entity and source occurrence identity, and
then follows the existing current-pose rules (`xref.placement-current-pose`).

Evidence: an assembly with four inserted parts that later features moved (three library
fasteners placed by Screw features, one part placed by a joint). Each has such a record; its
pose equals the part's saved displayed pose (`OGS.BlobFolder/OGS/DefaultScene/world`), and for
the fasteners it is the frame of the hole each sits in. Before, they kept the pose they were
inserted with.

`f3d.md` §1.4 states the form. Test: a synthetic carrier and legacy placement; a carrier with
another source occurrence identity or another target type does not bind.

### Breaking change

### Checklist

- [ ] Signed off: I certify this PR under the [Developer Certificate of Origin](https://developercertificate.org/). See [CONTRIBUTING.md](../CONTRIBUTING.md).

### Provenance declaration (decoder or format-spec changes only)

I derived the format knowledge in this contribution only from sources allowed by LEGAL.md.

I derived this contribution solely by analyzing CAD files I am legally entitled to possess and from publicly available information. I did not use vendor SDKs, decompiled binaries, confidential or NDA-covered material, or any source listed as forbidden in LEGAL.md.

The CAD files analyzed for this contribution were created by me and are owned exclusively by me; none of them is or contains NDA-covered material. The format knowledge was derived from these files with AI assistance (Claude Opus 5.5 and Claude Fable 5.2).
