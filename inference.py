"""The inference layer: every guess the converter makes where the file does not say.

The decoder (the cadmpeg fork) reads what the file states; f3d2sc.py maps that onto
SolidCraft. Where neither is enough, the converter may infer: pick a thread's face by its
radius, take a revolve's axis from the sketch line through the body's centre, and so on.
Each such inference is named here, can be switched off (--no-infer NAME, or "disable" in an
overrides file), and is recorded as an assumption in the report wherever it is used.

An overrides file (--overrides FILE, JSON) gives explicit answers instead of guesses, per
design or component (by the name the report shows) and feature name:

    {
      "disable": ["profile_ids_by_rank"],
      "designs": {
        "Stack Main": {
          "Thread 2": {"thread_faces": [[3.5, 2.0, 0.5]]},
          "Revolve 1": {"revolve_axis": {"origin": [0, 0, 0], "dir": [0, 1, 0]}}
        },
        "*": {"Remove Body 1": {"remove_piece": "negative"}}
      }
    }

Override keys: revolve_axis {origin, dir}; thread_faces [points on the faces];
remove_piece "positive" | "negative" (the side of the split plane's normal); owner
(component name a feature belongs to)."""
from __future__ import annotations

import json

HEURISTICS = {
    "freeze_from_final_brep":
        "a feature that cannot be converted: its bodies are imported as the f3d's final B-rep has them "
        "(with the later features that change them), non-editable",
    "revolve_axis_through_body_centre":
        "an undecoded revolve's axis: the line of its sketch (or a world axis in its plane) through the "
        "centre Fusion gives the body it makes or changes",
    "revolve_regions_by_final_volume":
        "a revolve over a sketch with several regions on the axis's side: the regions whose revolved volume "
        "(Pappus) equals the final body's, when no later feature changes that body",
    "revolve_sketch_from_records":
        "an undecoded revolve's sketch: the one sketch whose number its records name",
    "thread_face_by_radius":
        "a thread whose face selection the decoder resolves to no cylinder: among the cylinders earlier "
        "features of the body made, the one whose radius fits the designation (preferring the feature it "
        "depends on)",
    "thread_start_at_axis_end":
        "a thread of a set length: it starts at the end of its face that the cylinder's axis points to "
        "(seen on one modeled thread; the file's field for the end is not identified)",
    "thread_record_by_strings":
        "a thread record the decoder does not read: its designation and class from the record's strings, "
        "taken as a cosmetic full-length thread (which changes no geometry)",
    "primitive_placed_by_body_centre":
        "a box or cylinder primitive (the decoder gives its sizes, not its place): centred where Fusion records "
        "its body's centre, its sides along the world axes, a cylinder along the axis its final body's box tells",
    "profile_ids_by_rank":
        "extrude selection ids with gaps: the k-th smallest id the sketch's extrudes use is profile k",
    "owner_from_body_evidence":
        "a feature cadmpeg puts in a component that cannot hold it (no body yet) moves to the component "
        "of the nearest earlier feature with body data",
    "split_piece_by_final_side":
        "Remove Body after a Split removes the piece on the side of the plane where the final model has no body",
    "skip_unconvertible":
        "a feature that changes existing bodies and can be neither converted nor imported is left out, so the "
        "rest of the timeline still converts (the result lacks it; reported under 'skipped')",
    "insert_pose_from_component_path":
        "a Component Insert's occurrence takes the current pose of the component-path record stored for the "
        "record before the insert's first reference (centimetres), where cadmpeg's pose differs from it",
    "insert_occurrences_from_display_poses":
        "Component Inserts cadmpeg leaves unbound, paired in timeline order with the linked designs' "
        "unplaced occurrences in the archive's reference order (only when the counts agree); each gets "
        "the pose Fusion's display scene stores for its insert record, i.e. after joints",
    "screw_occurrences_from_component_paths":
        "a Screw (fastener) feature cadmpeg gives no occurrence of its own: one occurrence of the linked "
        "screw design its library data names (by display name), at the component-path pose stored for "
        "the record before the Screw's first reference (centimetres, as Fusion stores them); it replaces "
        "that design's one unplaced occurrence",
    "no_geometry_by_absence":
        "a native feature with no parameters and no body data (Fusion's 'Screw') makes no geometry",
    "region_by_arrangement":
        "a region selection whose curves bound no whole profile loop: the one closed face of the sketch's "
        "curve arrangement that runs along every member curve the sketch holds",
    "stale_region_left_out":
        "a stored profile region that names only curves its sketch no longer holds (the sketch was edited "
        "after the feature picked it) selects nothing; the feature takes its other regions",
    "hide_history_only_bodies":
        "a body whose last centre matches no body of Fusion's final model and whose number is no final "
        "body's B-rep entity number is outside Fusion's current model (a kept, hidden Combine tool): hidden "
        "at the end of the timeline, and left out of the final-shape check",
    "revolve_region_from_initial_loop":
        "a Revolve whose stored region selection cadmpeg keeps native (the sketch was edited after the "
        "Revolve picked its profile, so the stored regions no longer fit the sketch) revolves the face of "
        "the sketch's curve arrangement that runs along every curve of the stored selected region",
    "chamfer_side_by_final_shape":
        "with --check, when the final shape does not match Fusion's: a distance-angle or two-distance "
        "chamfer (which face takes the distance is not decoded, cadmpeg DR-341) is flipped, one at a "
        "time, and the first flip that makes the final shape match is kept",
    "bore_widened_by_cut":
        "a thread's bore that SolidCraft refuses to widen with solid.offset_face is widened by cutting a "
        "coaxial cylinder of the new radius over the face's decoded length instead",
    "split_threaded_cut_tool":
        "a Combine cut by a body with a modeled thread cuts by the tool's two halves, split through the "
        "thread's axis (a copy of the tool when the tools are kept): the same solid, without SolidCraft "
        "issue 3, where such a cut can delete the target",
    "unvalidated_on_self_check":
        "a feature whose result SolidCraft's kernel self-check (SMLib) refuses runs with validation off around "
        "it; the per-feature and final-shape checks still judge what it makes",
}


class Inference:
    def __init__(self, overrides: dict | None = None, disabled=()):
        self.overrides = overrides or {}
        self.disabled = set(disabled) | set(self.overrides.get("disable", []))
        unknown = self.disabled - set(HEURISTICS)
        if unknown:
            raise SystemExit(f"f3d2sc: unknown inference name(s): {', '.join(sorted(unknown))} "
                             f"(known: {', '.join(HEURISTICS)})")
        self.design = "*"
        self.used: list[dict] = []

    @classmethod
    def from_args(cls, path, disabled):
        ov = json.load(open(path)) if path else {}
        return cls(ov, disabled or [])

    def for_design(self, label):
        """A view for one design or component: same switches, its own record of use."""
        v = Inference(self.overrides, self.disabled)
        v.design = label
        return v

    def enabled(self, name):
        assert name in HEURISTICS, name
        return name not in self.disabled

    def override(self, feature, key):
        des = self.overrides.get("designs", {})
        for d in (self.design, "*"):
            v = des.get(d, {}).get(feature, {}).get(key)
            if v is not None:
                return v
        return None

    def use(self, name, feature, detail=""):
        """Record that `name` decided something for `feature`."""
        assert name in HEURISTICS, name
        self.used.append({"inference": name, "feature": feature, "detail": detail})

    def report(self):
        return self.used
