# SolidCraft issue reports: proposal for approval (15 Nov)

Nothing has been filed. This is how I would report SolidCraft issues 1, 3, 5 and 6 (README,
"SolidCraft issues found") to `tangency-labs/solidcraft`, and what has to happen first to meet
their reporting rules.

## Their rules, and what they mean for us

Read on 15 Nov from the repository's `CONTRIBUTING.md`, `.github/ISSUE_TEMPLATE/bug.yml`,
`AGENTS.md`, `SECURITY.md` and their reporting and ground-rules skills.

| Rule | Consequence for our reports |
|---|---|
| Bug template fields, all required: **What happened**, **Steps to reproduce**, **SolidCraft version and OS**; optional screenshots. Their reporting skill orders the body as *Reproduced on*, *Steps* (a minimal `{"commands":[...]}` script run with `solidcraft-cli run repro.json`), *Expected*, *Actual* (error text verbatim), *Attachments*. Title `<Area>: <what fails>`. | Each issue below follows that layout. |
| "Before reporting a boolean or modelling failure, please try to reproduce it on the latest nightly or a build from current `main`, and say which build you used." | Everything was found on **0.40.1**. The latest release is **0.42.0** (and nightly 0.42.0-nightly.20261115). Each issue has to be re-run on 0.42.0 first; whatever no longer fails is dropped. Needs your OK to download 0.42.0 (from their GitHub release, about the size of 0.40.1). |
| Ground rules: no "test data, measurements, command lists, ids or files produced with" commercial CAD products; no expected values or files from Fusion, SolidWorks, Onshape and the like. | Reports may not use the chaser or film scanner geometry, nor Fusion's volumes as the expected result. **Reproductions 01, 05 and 06 are made from Fusion geometry and cannot be attached as they are.** Each issue needs a synthetic script (round numbers, our own shapes) and an expectation stated without Fusion (for example, "the body remains, with volume = box − cylinder"). Reproduction 03-minimal is already synthetic. |
| Use "a small example without private designs or sensitive details"; strip home folders, user names and paths from logs; don't attach the user's design without asking. | Synthetic scripts only, paths replaced by `repro.json`. |
| A command-line agent without a browser session usually cannot attach a file; say so, and a person attaches it. Logs go in a linked gist. | The scripts are short enough to paste inline in a `json` block, so no attachment is needed. |
| The reporting skill: show the user the finished report; file only after they approve. | This document; I file nothing until you approve each final text. |
| Reports without steps get `needs-info` and close after 14 days. | Every report carries a runnable script. |
| Search for duplicates (not a written rule for issues, but good practice). | Searched; findings per issue below. |
| `SECURITY.md`: crashes and wrong results are normal public bugs. | None of these are security issues. |

No rule requires disclosing that a report was drafted with an AI assistant. I would still add
one line, "Found while converting designs with a script; drafted with Claude Code," unless you
prefer not to.

Filing goes under **your** GitHub account (no `gh` here, so either you paste the text, or you
install and log in `gh`, and I run `gh issue create -R tangency-labs/solidcraft` after your
approval of each text).

## Duplicates found (15 Nov)

| Ours | Closest existing | Verdict |
|---|---|---|
| 3: cut by a threaded tool deletes the target | **#15700** (open, 0.40.1): Combine Cut deletes the target body with no error. Its tool is a loft from STEP, not a thread; a contributor traces it to dropped imprint edges in the boolean. | Same symptom, cause not shown to be the same. **Comment on #15700** with our synthetic thread repro rather than a new issue, asking whether it is the same cause; if a maintainer says no, open a new issue. |
| 5: offset_face on a bore fails with "Face Loop has bad orientation" | **#17168** (open, 0.42.0, in progress): offset of a modeled **thread** face gives the identical "Loop Orientation" error; on current `main` it now fails differently. Several other "OFFSET FACE: operation failed" app reports (#15661, #15715, #16162, #16866, #17012). | Ours is a plain cylindrical bore, not a thread face. **New issue**, linking #17168 as related, but only if it still fails on 0.42.0. |
| 6: cylinder cut with its cap at a face's end deletes the target | #15700 again, but its reporter rules out coincident faces; #13686 (tangent D-cut loses the body) is near. | **New issue**, linking #15700 and #13686. |
| 1: dense degree-5 spline extrude fails the self-check | #15053 (slow/failed extrude), but its cause is duplicate straight edges in planar regularization. | **New issue**. |

Issues 2 and 4 are not proposed: 2 no longer reproduces in a minimal design, and 4 (separate
bodies of disjoint pieces) is a feature request, which I would leave to you.

## Work before filing (needs your go-ahead)

1. Download SolidCraft 0.42.0 for Linux x86_64 from
   `github.com/tangency-labs/solidcraft/releases/tag/v0.42.0` (checking the release digest, as
   for 0.40.1) and re-run all four.
2. Make synthetic reproductions:
   - **1:** a degree-5 control spline whose control points cluster 0.1–15 µm apart, generated
     from a formula (an involute of a round-number base circle), not from Fusion.
   - **3:** `03-minimal-threaded-cut-deletes-target.json` is already synthetic (a 40 × 100 × 40
     box, a Ø65 × 50 cylinder with a modeled M65x3 thread, one cut). Expected: the box remains,
     cut by the threaded cylinder.
   - **5 and 6:** my first seven synthetic attempts (a Ø1 bore: through, tilted 25°, blind,
     between two pockets, with a counterbore, through a sloped or curved top) all offset fine,
     so the trigger is not yet isolated. Next step: reduce the 82-command script command by
     command while it still fails, then replace the remaining Fusion coordinates with round
     numbers and check it still fails. If it cannot be made synthetic, the issue is not filed.
3. Show you the four final texts; file each only on your yes.

## Draft texts (to be finalized after steps 1–2)

### Comment on #15700 (our issue 3)

> Possibly the same symptom with a different tool: a cut whose tool carries a modeled thread
> deletes the target and reports success.
>
> **Reproduced on:** SolidCraft 0.42.0 (CLI), Linux x86_64. *(to be confirmed)*
>
> ```json
> {"commands": [
>  {"command": "solid.box", "params": {"length": 40, "width": 100, "height": 40, "center": [2.5, 0, 10], "operation": "new", "name": "Frame", "body_name": "Frame"}},
>  {"command": "solid.cylinder", "params": {"radius": 32.5, "height": 50, "base": [13, 0, 25], "axis": "X", "operation": "new", "name": "Shaft", "body_name": "Shaft"}},
>  {"command": "solid.thread", "params": {"designation": "M65x3", "modeled": true, "face": [38, -32.5, 25], "name": "Thread"}},
>  {"command": "solid.combine", "params": {"target": "Frame", "tools": ["Shaft"], "operation": "cut", "name": "Cut", "keep_tools": true}}
> ]}
> ```
>
> `solidcraft-cli run repro.json --save out.sc`, then `solidcraft-cli eval out.sc`.
>
> **Expected:** `Frame` remains, cut by the threaded shaft.
> **Actual:** the combine succeeds with no warning; afterwards only `Shaft` exists. Also with
> `--validation off`. It depends on where the thread crosses the box's end face: moving that face
> through one pitch (20.0–23.0 mm in 0.1 mm steps) loses the box at 21.4, 21.8, 22.1, 22.4 and
> 22.5 mm only. Cutting with the shaft split lengthwise through its axis (two halves as tools)
> gives the expected solid.

### New issue (our issue 5): `Offset face: a small bore face is refused with "Face Loop has bad orientation"`

> **Reproduced on:** SolidCraft 0.42.0 (CLI), Linux x86_64. *(to be confirmed)*
> ### Steps
> *(synthetic script, to be made)* `solidcraft-cli run repro.json`
> ### Expected
> The r = 0.5 mm bore face is offset inward by 0.1355 mm (the bore grows to r = 0.6355 mm).
> ### Actual
> ```text
> SMLib healing: SMLib's check refuses the result: 1 failed tests:
> SmTrimmingTools::Test [#3] Loop Orientation: Face Loop has bad orientation [owner SmFace]
> ```
> Any distance fails, inward or outward, also with `--validation off`. A coaxial `solid.cylinder`
> cut of radius 0.6355 mm makes the expected solid. Related: #17168 (same error on a thread face).

### New issue (our issue 6): `Cylinder cut: a cut whose end cap lies on a face's end deletes the target body`

> **Reproduced on:** SolidCraft 0.42.0 (CLI), Linux x86_64. *(to be confirmed)*
> ### Steps
> *(synthetic script, to be made)*
> ### Expected
> The body remains, with the bore widened.
> ### Actual
> The cut reports success and no body remains. The same cut extended by 1 µm past each end
> works. Related: #15700, #13686.

### New issue (our issue 1): `Extrude: a dense degree-5 control spline gives a surface SMLib refuses`

> **Reproduced on:** SolidCraft 0.42.0 (CLI), Linux x86_64. *(to be confirmed)*
> ### Steps
> *(synthetic script with a generated spline, to be made)*
> ### Expected
> The closed profile extrudes into a solid.
> ### Actual
> ```text
> SmSurfOfExtrusion::Test #8 Origin: m_vOrigin is a point on m_pGenCurve
> ```
> The same points as a cubic control spline fail too. Evenly spaced splines with up to 400
> control points extrude fine.
