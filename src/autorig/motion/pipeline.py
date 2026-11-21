"""MotionEngine: planner -> feasibility -> solver -> garment-aware deformation -> evaluation.

One engine per (skeleton, layers) state. It is cached by the editor; poses then cost a solve (milliseconds)
plus a render (seconds on CPU, see the report). ``skeleton_only`` skips the render for interactive scrubbing.
"""

from __future__ import annotations

import math
import time
from typing import Any

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

from ..phase2 import corrective as C
from ..phase2.natural import natural_order
from ..phase2.pose_eval import on_bg
from ..phase2.posing import PRESET_BY_NAME as OLD_PRESETS
from ..phase2.posing import PoseSolver
from ..phase2.skeleton import SIDE_SIGN, SIDES, Skeleton, layer_class
from ..phase2.skinning import Deformer, PoseParams
from .feasibility import ArtInventory, PoseFeasibilityEvaluator
from .handselect import NaturalHandSelectionEngine, action_for, context_from_pose, ease_aim, load_rules
from .garments import ArtworkDeformationEngine, GarmentDeformationEngine, GarmentModel, GarmentSkeleton
from .ik import LAT, ArmIKController, FootContactController, LegIKController
from .intent import ArmIntent, LegIntent, PoseIntent
from .planner import LIBRARY, SPEC_BY_NAME, MotionPlanner, gait
from .profile import build_profile
from .quality import PoseQualityEvaluator
from .solver import SkeletalPoseSolver
from .switches import (DRAWING_TO_POSE, FALLBACK_POSE, HAND_POSES, ArtworkSwitchController,
                       CorrectionSwitchController, HandPoseLibrary, correction_readings_from_pose, offset_layer)

DIAGNOSTICS = ("skeleton", "garments", "boundaries", "weights", "overlaps", "contacts", "com", "provenance",
               "missing", "correctives")


def motion_order(visible: list[str], it: PoseIntent, hands: dict[str, Any], available: set[str]) -> list[str]:
    """Garment-aware layering: per leg thigh, shoe, shin (trouser cuff over the shoe); body; per arm upper arm,
    hand, forearm (sleeve end over the wrist); head. Chosen hand drawings replace the reference hands."""
    vis = set(visible)
    legs = [c for s in SIDES for c in (f"thigh_{s}", f"foot_{s}", f"shin_{s}") if c in vis]
    body = [c for c in ("pelvis", "neck", "torso") if c in vis]
    arms = [c for s in SIDES for c in (f"upper_arm_{s}", f"hand_{s}", f"forearm_{s}") if c in vis]
    head = [c for c in visible if layer_class(c) == "head"]
    rest = [c for c in visible if c not in legs + body + arms + head]
    out = legs + body + rest + (arms + head if it.arms_behind_head else head + arms)
    for s, h in hands.items():
        v = h.get("layer") or (f"hand_{s}" if h["drawing"] == "open" else f"hand_{s}__{h['drawing']}")
        if v == f"hand_{s}":
            continue
        if v in available and f"hand_{s}" in out:
            out[out.index(f"hand_{s}")] = v
            cover = f"forearm_{s}__hscuff" if "__hs." in v and f"forearm_{s}__hscuff" in available else f"forearm_{s}__trim"
            if cover in available and f"forearm_{s}" in out:
                out[out.index(f"forearm_{s}")] = cover
    for f in it.front:
        if f in out:
            out.remove(f)
            out.append(f)
    return out


class MotionEngine:
    def __init__(self, sk: Skeleton, layers: dict[str, np.ndarray], generated: dict[str, np.ndarray] | None,
                 source: np.ndarray, variants: dict[str, np.ndarray] | None = None,
                 approvals: dict[str, Any] | None = None, view: str = "front",
                 profile_overrides: dict[str, Any] | None = None, corrective_ids: list[str] | None = None,
                 corrective_manifest: dict[str, Any] | None = None, hand_store: dict[str, Any] | None = None,
                 garment_mode: str = "structured", garment_config: dict[str, Any] | None = None,
                 weight_ops: list[dict[str, Any]] | None = None):
        """``hand_store``: {"layers": derived rest layers of stored hand drawings, "candidates": their records,
        "use": the user's active-candidate choices} (``handstore.derive_layers``; built by the editor).

        ``garment_mode``: "structured" (Phase 3.3: garment parts, aligned shoulder pivots, weight meshes, rigid
        segments over joint patches) or "legacy" (the Phase 3.1 band deformer, kept for before/after).
        ``weight_ops``: the user's weight edits (editor project), replayed on the automatic weights."""
        t0 = time.time()
        if garment_mode not in ("structured", "legacy"):
            raise ValueError(f"unknown garment_mode {garment_mode!r}")
        self.garment_mode = garment_mode
        self.layers, self.generated, self.source = layers, generated or {}, source
        self.hand_store = hand_store or {"layers": {}, "candidates": [], "use": {}}
        self.variants = {**(variants or {}), **self.hand_store.get("layers", {})}
        self.approvals = approvals or {}
        masks = {c: a[..., 3] > 0 for c, a in layers.items()}
        self.masks = masks
        self.pivot_report: list[dict[str, Any]] = []
        self.structure = self.weights = None
        if garment_mode == "structured":
            from ..garmentrig.structure import DEFAULT_CONFIG, align_pivots

            import copy as _copy
            self.garment_cfg = garment_config or _copy.deepcopy(DEFAULT_CONFIG)
            sk, self.pivot_report = align_pivots(sk, masks, self.garment_cfg)
        self.sk = sk
        self.profile = build_profile(sk, masks, view, profile_overrides)
        self.garments = GarmentModel.build(source, masks, sk, self.profile)
        if garment_mode == "structured":
            from ..garmentrig.deform import StructuredDeformer, StructuredSkeleton
            from ..garmentrig.structure import GarmentStructureAnalyzer
            from ..garmentrig.weights import WeightSet

            self.gsk = StructuredSkeleton(sk, self.garments)
            an = GarmentStructureAnalyzer(sk, source, layers, self.generated, self.garments, self.profile,
                                          self.garment_cfg, self.pivot_report)
            self.structure = an.analyze()
            self.weights = WeightSet(self.gsk, self.structure, an, self.garment_cfg, self.pivot_report)
            self.weights.ops = [dict(o) for o in (weight_ops or [])]
            self.dfm = StructuredDeformer(self.gsk, {**layers, **self.variants}, self.generated, source,
                                          set(self.variants), self.weights, self.structure,
                                          bool(self.garment_cfg.get("overlays", True)), self.pivot_report)
        else:
            self.gsk = GarmentSkeleton(sk, self.garments)
            self.dfm = ArtworkDeformationEngine(self.gsk, {**layers, **self.variants}, self.generated,
                                                reference=source, variants=set(self.variants))
        self.gdef = GarmentDeformationEngine(self.gsk, self.garments, bands=garment_mode == "legacy")
        self.solver = SkeletalPoseSolver(sk, self.profile, self.dfm.masses())
        self.planner = MotionPlanner(self.profile)
        self.inventory = ArtInventory.build(set(layers), set(self.variants), self.approvals, view, corrective_ids)
        self.feas = PoseFeasibilityEvaluator(self.inventory)
        self.rest_area = {c: int((a[..., 3] > 127).sum()) for c, a in {**layers, **self.variants}.items()}
        self.quality = PoseQualityEvaluator(self.profile, sk, self.rest_area)
        lum = source[..., :3].astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)
        fp = np.zeros(source.shape[:2], bool)
        for m in masks.values():
            fp |= m
        self.dark = (lum < 80) & fp
        self._legacy = None
        self.hand_lib = HandPoseLibrary(sk, layers, self.variants, self.approvals, corrective_manifest,
                                        self.hand_store.get("candidates"), self.hand_store.get("use"),
                                        self.hand_store.get("phase2_active", True))
        # the feasibility inventory sees each pose through its active candidate (canonical drawing keys)
        self.inventory.hands = {s: {HAND_POSES[p]: v.status for p, v in self.hand_lib.hands[s].items() if v.available}
                                for s in SIDES}
        if garment_mode == "structured":
            from ..garmentrig.corrections import GarmentCorrectionController

            self.corr_switch = GarmentCorrectionController(sk, self.approvals, corrective_ids)
        else:
            self.corr_switch = CorrectionSwitchController(sk, self.approvals, corrective_ids)
        self._offset_layers: dict[tuple, str] = {}
        self._view, self._corrective_ids, self._corrective_manifest = view, corrective_ids, corrective_manifest
        self.hand_select = NaturalHandSelectionEngine()  # engine_for_package loads the character's rules
        self.build_s = round(time.time() - t0, 3)

    def set_hand_store(self, hand_store: dict[str, Any] | None, approvals: dict[str, Any]) -> dict[str, Any]:
        """Incremental update after a Hand Library edit (approval, placement, mask, version, choice): only the
        changed hand layers are re-registered; the skeleton, garments and other layers are kept."""
        t0 = time.time()
        hs = hand_store or {"layers": {}, "candidates": [], "use": {}}
        old = self.hand_store.get("layers", {})
        changed = [lid for lid, a in hs.get("layers", {}).items() if old.get(lid) is not a]
        for lid in changed:
            self.dfm.add_variant_layer(lid, hs["layers"][lid])
            self.variants[lid] = hs["layers"][lid]
            self.rest_area[lid] = int((hs["layers"][lid][..., 3] > 127).sum())
        for lid in set(old) - set(hs.get("layers", {})):
            self.variants.pop(lid, None)
        self.hand_store, self.approvals = hs, approvals
        self.inventory = ArtInventory.build(set(self.layers), set(self.variants), approvals, self._view, self._corrective_ids)
        self.feas = PoseFeasibilityEvaluator(self.inventory)
        self.hand_lib = HandPoseLibrary(self.sk, self.layers, self.variants, approvals, self._corrective_manifest,
                                        hs.get("candidates"), hs.get("use"), hs.get("phase2_active", True))
        self.inventory.hands = {s: {HAND_POSES[p]: v.status for p, v in self.hand_lib.hands[s].items() if v.available}
                                for s in SIDES}
        self.corr_switch.approvals = approvals
        return {"changed_layers": len(changed), "update_s": round(time.time() - t0, 3)}

    # -- switches ------------------------------------------------------------------------------------
    def switch_controller(self, state: dict[str, Any] | None = None) -> ArtworkSwitchController:
        # one source of truth for approval: the feasibility inventory (the planner's arm style reads it too)
        for s in SIDES:
            for v in self.hand_lib.hands[s].values():
                if v.layer is not None and v.cid != "original":
                    v.status = self.inventory.hands.get(s, {}).get(v.drawing, v.status)
        return ArtworkSwitchController.build(self.hand_lib, self.sk, self.layers, self.corr_switch, state)

    def _offset_layer(self, layer: str, pivot, off: dict[str, float]) -> str:
        """Deformer layer id of ``layer`` with a rigid local offset about the group pivot (cached)."""
        key = (layer, round(off["dx"], 3), round(off["dy"], 3), round(off["rot"], 3))
        if key not in self._offset_layers:
            src = {**self.layers, **self.variants}[layer]
            oid = f"{layer}__off{len(self._offset_layers)}"
            self.dfm.add_variant_layer(oid, offset_layer(src, pivot, off["dx"], off["dy"], off["rot"]))
            self._offset_layers[key] = oid
        return self._offset_layers[key]

    def select_hands(self, name: str, it: PoseIntent, pose: PoseParams, allow_pending: bool,
                     switches: dict[str, Any] | None, t_key: float | None, actions: dict[str, str | None] | None = None,
                     targets: dict[str, Any] | None = None, states: dict[str, Any] | None = None,
                     t: float | None = None, beat: int | None = None, aim: bool = True,
                     previous_wrists: dict[str, Any] | None = None) -> dict[str, Any]:
        """Stage 3b (hand selection, after the skeletal solve): the NaturalHandSelectionEngine's automatic choice
        per side, the switch resolution it feeds (user > keyframe > auto > default), and the wrist aim of the
        active drawing (index toward the target, thumb up), applied to the hand bone within the wrist limit.
        The forearm and every other bone are untouched; bone lengths never change."""
        ctl = self.switch_controller(switches)
        ctxs = {s: context_from_pose(self.solver, self.sk, pose, it, s, action_for(name, s, (actions or {}).get(s)),
                                     (targets or {}).get(s), (previous_wrists or {}).get(s), t, beat, self._view)
                for s in SIDES}
        dec = self.hand_select.select_pair(self.hand_lib, ctxs, states, allow_pending)
        out = {}
        for s in SIDES:
            r = ctl.resolve(f"hand_{s}", t_key, dec[s]["chosen"], allow_pending)
            v = self.hand_lib.hands[s].get(r["active"])
            rule = self.hand_select.rule(ctxs[s].action)
            a = self.hand_select.aim(v, ctxs[s], rule) if (aim and v is not None and v.available) else None
            goal = a["applied_deg"] if a and a.get("measured") else 0.0
            applied, limited = ease_aim(goal, (states or {}).get(s), t) if aim else (0.0, False)
            if a and a.get("measured"):
                a = {**a, "goal_applied_deg": a["applied_deg"], "applied_deg": round(float(applied), 2),
                     "residual_deg": round(float(a["needed_deg"] - applied), 2), "eased": limited}
            elif abs(applied) > 1e-9:
                a = {"feature": None, "measured": True, "applied_deg": round(float(applied), 2), "goal_applied_deg": 0.0,
                     "needed_deg": 0.0, "residual_deg": round(float(-applied), 2), "eased": limited,
                     "note": "easing the wrist back from the previous gesture's aim"}
            if abs(applied) > 1e-9:
                pose.theta[self.sk.idx[f"hand_{s}"]] += applied
            missing = [p for p in rule.get("prefer", []) if not (self.hand_lib.hands[s].get(p) and
                                                                  self.hand_lib.hands[s][p].available)]
            chosen = dec[s]["chosen"]
            if chosen in rule.get("prefer", []):
                missing = [p for p in missing if rule["prefer"].index(p) < rule["prefer"].index(chosen)]
            out[s] = {**dec[s], "resolution": {k: r[k] for k in ("requested", "active", "source", "note")},
                      "aim_applied": a, "missing_preferred": missing,
                      "overridden_by": r["source"] if r["source"] in ("user", "keyframe") else None}
        return {"decisions": out, "auto": {s: dec[s]["chosen"] for s in SIDES}}

    def resolve_switches(self, it: PoseIntent, allow_pending: bool, switches: dict[str, Any] | None,
                         t: float | None, selection: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
        """Stage 6 (switch resolution). Hands: user > keyframe > automatic choice (``selection``: the
        NaturalHandSelectionEngine; without it the planner's preference list) > reference hand.
        Returns the hands record used for layering/feasibility and the full resolution per group."""
        auto = self.feas.choose_hands(it, allow_pending)
        ctl = self.switch_controller(switches)
        out, res = {}, {}
        for s in SIDES:
            g = f"hand_{s}"
            a = auto[s]
            dec = (selection or {}).get("decisions", {}).get(s)
            auto_pose = dec["chosen"] if dec is not None else DRAWING_TO_POSE.get(a["drawing"], FALLBACK_POSE)
            r = ctl.resolve(g, t, auto_pose, allow_pending)
            res[g] = r
            drawing = HAND_POSES[r["active"]]
            if dec is not None:
                prefer = self.hand_select.rule(dec["action"]).get("prefer") or [FALLBACK_POSE]
                pref = HAND_POSES[prefer[0]] if r["source"] in ("auto", "default") else HAND_POSES.get(r["requested"], drawing)
                note = r["note"] or (None if r["source"] not in ("auto", "default") else
                                     (f"{dec['action']}: {dec['chosen']} ({dec['reason']})" if dec["chosen"] else
                                      f"{dec['action']}: no eligible drawing, showing the default"))
            else:
                pref = a["preferred"] if r["source"] in ("auto", "default") else HAND_POSES.get(r["requested"], drawing)
                note = r["note"] or (a["note"] if r["source"] in ("auto", "default") else None)
            out[s] = {"drawing": drawing, "pose": r["active"], "preferred": pref, "source": r["source"],
                      "pending_review": r["pending_review"], "note": note, "needs_artwork": r["needs_artwork"],
                      "offset": r["offset"], "layer": r["layer"],
                      "cid": self.hand_lib.hands[s][r["active"]].cid}
            if dec is not None:
                out[s]["selection"] = dec
                missing = dec.get("missing_preferred") or []
                if missing and r["source"] in ("auto", "default"):
                    out[s]["missing_preferred"] = missing
                    out[s]["fallback"] = r["active"]
                continue
            prefs = it.arms[s].hand if s in it.arms else ["open"]
            missing = [p for p in prefs if self.inventory.hands.get(s, {}).get(p, "missing") == "missing"]
            if missing and r["source"] in ("auto", "default"):
                out[s]["missing_preferred"] = [DRAWING_TO_POSE.get(p, p) for p in missing]
                out[s]["fallback"] = r["active"]
        for g in ctl.groups:
            if g not in res and ctl.groups[g].kind == "face":
                res[g] = ctl.resolve(g, t, None, allow_pending)
        return out, res

    # -- poses ---------------------------------------------------------------------------------------
    def arm_style(self, name: str, allow_pending: bool) -> str:
        """Run arms: the fist pump only when a fist drawing can be used, else the approved open-hand style."""
        if name != "running":
            return "fist"
        ok = all(self.inventory.hands.get(s, {}).get("fist") == "approved" or
                 (allow_pending and self.inventory.hands.get(s, {}).get("fist") == "pending_review") for s in SIDES)
        return "fist" if ok else "open"

    def apply_ik(self, it: PoseIntent, ik: dict[str, Any] | None) -> list[str]:
        """User IK controls on top of the planned intent (stage 2: contact and IK target planning).
        ``ik``: {"arm_r"|"arm_l"|"leg_r"|"leg_l": {enabled, mode, target, pole, bend, lock, max_flex, torso_follow},
        "pelvis_offset": [dx, dy]}. Targets are world source px."""
        from dataclasses import replace
        notes: list[str] = []
        if not ik:
            return notes
        for s in SIDES:
            a = ik.get(f"arm_{s}") or {}
            if a.get("enabled") and a.get("mode") == "fk" and a.get("fk"):
                arm = it.arms.get(s) or ArmIntent()
                fk = a["fk"]
                it.arms[s] = replace(arm, target=None, reach=None, target_joint=None, pole=None,
                                     abduction=float(fk.get("abduction", arm.abduction)),
                                     flexion=float(fk.get("flexion", arm.flexion)))
                if fk.get("clavicle_extra"):  # shoulder compensation carried over from an IK pose (match_ik)
                    it.clavicle[s] = it.clavicle.get(s, 0.0) + float(fk["clavicle_extra"])
            if a.get("enabled") and a.get("mode", "ik") == "ik" and a.get("target") is not None:
                arm = it.arms.get(s) or ArmIntent()
                tgt = np.asarray(a["target"], float)
                pole = tuple(a["pole"]) if a.get("pole") is not None else None
                if a.get("bend") in (1, -1, "1", "-1"):
                    # bend side held (no elbow inversion while dragging): a far pole perpendicular to the
                    # shoulder->target line on the chosen side; cross(u, n) = +1 for n = (-u_y, u_x)
                    sh = self.sk.bone(f"upper_arm_{s}").head
                    d = tgt - sh
                    u = d / max(float(np.hypot(*d)), 1e-6)
                    pole = tuple((sh + tgt) / 2 + int(a["bend"]) * np.array([-u[1], u[0]]) * 1000.0)
                it.arms[s] = replace(arm, target=tuple(tgt), reach=None, target_joint=None, pole=pole,
                                     max_flex=float(a["max_flex"]) if a.get("max_flex") else None)
            if a.get("enabled") and a.get("end_angle") is not None and s in it.arms:
                # wrist orientation: world direction of the hand bone relative to its rest direction
                it.arms[s] = replace(it.arms[s], hand_dir=float(self.sk.bone(f"hand_{s}").rest_angle) + float(a["end_angle"]))
                if a.get("torso_follow"):
                    sh = self.sk.bone(f"upper_arm_{s}").head
                    reach = self.profile.arm(s)
                    over = float(np.hypot(*(np.asarray(a["target"]) - sh))) - 0.95 * reach
                    if over > 0:
                        lean = LAT[s] * min(8.0, 40.0 * over / reach)
                        it.chest = (it.chest if it.chest is not None else 0.0) + lean
                        notes.append(f"arm_{s}: torso leans {lean:+.1f} deg toward the target")
            g = ik.get(f"leg_{s}") or {}
            if g.get("enabled") and g.get("mode", "ik") == "ik" and g.get("target") is not None:
                tx, ty = map(float, g["target"])
                ground = self.profile.ankle_y[s]
                if ty > ground:  # never below the ground
                    notes.append(f"leg_{s}: ankle target {ty - ground:.1f} px below the ground, clamped")
                    ty = ground
                lock = bool(g.get("lock"))
                if lock and abs(ty - ground) > 0.5:  # a locked foot is planted: it stands on the ground
                    notes.append(f"leg_{s}: locked foot placed on the ground ({ground - ty:.1f} px lower)")
                    ty = ground
                pole = tuple(g["pole"]) if g.get("pole") is not None else None
                kmax = float(g.get("max_flex") or self.profile.front_knee["squat"])
                base = it.legs.get(s)
                if base is None:
                    it.legs[s] = LegIntent(lock, (tx, ty), knee_max=kmax, knee_min=3.0, pole=pole,
                                           ground=(tx, ground), load=1.0 if lock else 0.0)
                else:
                    # keep the planner's load, ground and foot behaviour (pelvis height and balance stay
                    # continuous); the user target replaces the ankle goal and the swing knee schedule
                    it.legs[s] = replace(base, planted=lock, ankle=(tx, ty), pole=pole, hang_knee=None,
                                         knee_max=max(kmax, base.knee_max),
                                         load=base.load if lock == base.planted else (1.0 if lock else 0.0))
                if g.get("end_angle") is not None:  # foot orientation (world, from rest); planted feet stay flat by default
                    it.legs[s] = replace(it.legs[s], foot_angle=float(g["end_angle"]), foot_follow=0.0)
        po = ik.get("pelvis_offset")
        if po and (abs(po[0]) > 1e-6 or abs(po[1]) > 1e-6):
            it.pelvis_dx += float(po[0])
            it.pelvis_dy += float(po[1])
            if it.balance == "static":
                it.balance = "manual"  # the user places the pelvis; planted feet stay locked through IK
            notes.append(f"pelvis offset {po[0]:+.1f}, {po[1]:+.1f} px with feet locked")
        return notes

    def feature_world(self, side: str, pose: PoseParams, feature: str) -> np.ndarray | None:
        """Posed world position (rest-canvas px) of a stored drawing's feature (grip_center, index_tip ...) of the
        side's active drawing for the feature's pose; None without a measured feature."""
        pose_name = {"grip_center": "gripping", "index_tip": "pointing", "thumb_tip": "thumbs_up"}.get(feature)
        v = self.hand_lib.hands[side].get(pose_name) if pose_name else None
        if v is None or not v.available or not v.placement:
            return None
        f = ((v.meta or {}).get("features") or {}).get(feature)
        fin = (v.placement or {}).get("final")
        if f is None or fin is None:
            return None
        from ..handstore import Placement  # noqa: PLC0415

        M = Placement(tuple(fin["anchor"]), tuple(fin["point"]), fin["angle_deg"], fin["scale"]).matrix()
        rest = M[:2, :2] @ np.asarray(f, float) + M[:2, 2]
        return self.solver.K.point(self.sk.idx[f"hand_{side}"], rest, pose)

    def _grip_ik(self, name, phase, speed, exaggeration, travel, overrides, live_head, allow_pending, fluid, ik,
                 switches, switch_time, actions, targets) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        """Gripping with an interaction target: the arm's IK target is moved so the grip drawing's grip centre
        (not the wrist) lands on the target (two fixed-point passes). Uses the one arm IK (TwoBoneIKSolver)."""
        report: dict[str, Any] = {}
        sides = [s for s in SIDES if (targets or {}).get(s) is not None and
                 action_for(name, s, (actions or {}).get(s)) == "gripping"]
        if not sides:
            return ik, report
        import copy  # noqa: PLC0415

        from .rigctl import default_ik_state  # noqa: PLC0415

        ik2 = copy.deepcopy(ik) if ik else {}
        for s in sides:
            arm = {**default_ik_state()[f"arm_{s}"], **ik2.get(f"arm_{s}", {})}
            arm.update(enabled=True, mode="ik", target=[float(v) for v in targets[s]])
            ik2[f"arm_{s}"] = arm
        # hold each gripping arm's current bend side: near a hanging, almost straight arm the default elbow pole is
        # nearly collinear with the shoulder->hand line and the IK could bend the elbow the other way
        _, pose0, info0 = self.plan_and_solve(name, phase, speed, exaggeration, travel, overrides, live_head,
                                              allow_pending, fluid, ik)
        for s in sides:
            ctl = self.limb(f"arm_{s}")
            r0 = ctl.root(pose0)
            wr, el = np.asarray(info0["joints_posed"][f"hand_{s}"]), np.asarray(info0["joints_posed"][f"forearm_{s}"])
            u = wr - r0
            c = float(u[0] * (el[1] - r0[1]) - u[1] * (el[0] - r0[0]))
            if abs(c) > 1e-6 and ik2[f"arm_{s}"].get("bend") not in (1, -1, "1", "-1"):
                ik2[f"arm_{s}"]["bend"] = 1 if c > 0 else -1
        for k in range(16):
            it, pose, _info = self.plan_and_solve(name, phase, speed, exaggeration, travel, overrides, live_head,
                                                  allow_pending, fluid, ik2)
            gain = 1.0 if k < 2 else 0.5  # damped after two full steps (the hand turns with the forearm)
            done = True
            for s in sides:
                g = self.feature_world(s, pose, "grip_center")
                if g is None:
                    report[s] = {"status": "no grip drawing with a measured grip centre: the wrist goes to the target"}
                    continue
                err = np.asarray(targets[s], float) - g
                report[s] = {"grip_center": [round(float(v), 2) for v in g], "error_px": round(float(np.hypot(*err)), 2),
                             "wrist_target": [round(float(v), 2) for v in ik2[f"arm_{s}"]["target"]]}
                if np.hypot(*err) > 0.5:
                    ik2[f"arm_{s}"]["target"] = [float(ik2[f"arm_{s}"]["target"][0] + gain * err[0]),
                                                 float(ik2[f"arm_{s}"]["target"][1] + gain * err[1])]
                    done = False
            if done:
                break
        return ik2, report

    def plan_and_solve(self, name: str, phase: float | None = None, speed: float = 1.0, exaggeration: float = 1.0,
                       travel: float = 0.0, overrides: dict[str, float] | None = None,
                       live_head: dict[str, float] | None = None, allow_pending: bool = False, fluid: bool = True,
                       ik: dict[str, Any] | None = None):
        it = self.planner.plan(name, phase, speed, exaggeration, travel, self.arm_style(name, allow_pending), fluid)
        ik_notes = self.apply_ik(it, ik)
        pose, info = self.solver.solve(it)
        info["ik_notes"] = ik_notes
        manual = {}
        for b, deg in (overrides or {}).items():
            if b not in self.sk.idx:
                raise KeyError(b)
            pose.theta[self.sk.idx[b]] += float(deg)
            manual[b] = float(deg)
        if live_head:
            info["live_head"] = self.compose_live_head(pose, live_head)
        if manual or live_head:
            info.update(self.solver.K.joints(pose))
            info["anatomical"] = self.solver.anatomical(pose)
            info["violations"] = self.solver.check_limits(pose, it, info["anatomical"])
            info["com"] = self.solver.com(pose)
        info["manual_rotations"] = manual
        info["contacts"] = FootContactController().report(it, info)
        return it, pose, info

    def limb(self, limb: str):
        kind, s = limb.split("_")
        cls = ArmIKController if kind == "arm" else LegIKController
        return cls(self.sk, self.solver.K, s)

    def match_ik(self, limb: str, to: str, name: str, phase=None, ik: dict[str, Any] | None = None,
                 **kw) -> dict[str, Any]:
        """FK/IK matching for a limb, evaluated in the current pose (the other limbs keep their state).
        to="ik": target, pole and held bend side that reproduce the limb's FK pose.
        to="fk" (arms): FK abduction/flexion that reproduce the limb's IK pose. Legs have no FK angles: in FK
        mode they follow the planner, so the result reports the jump instead of hiding it.
        Returns the new limb state and the measured joint jump (px) of the switch."""
        ik = {k: (dict(v) if isinstance(v, dict) else v) for k, v in (ik or {}).items()}
        ctl = self.limb(limb)
        cur = dict(ik.get(limb) or {})
        _, pose0, info0 = self.plan_and_solve(name, phase, ik=ik, **kw)
        if to == "ik":
            m = ctl.fk_to_ik(pose0)
            new = {**cur, "enabled": True, "mode": "ik", "target": m["target"], "pole": m["pole"]}
            if ctl.kind == "arm":
                r = ctl.root(pose0)
                u = np.asarray(m["target"]) - r
                new["bend"] = 1 if float(u[0] * (m["pole"][1] - r[1]) - u[1] * (m["pole"][0] - r[0])) >= 0 else -1
            else:
                it0 = self.planner.plan(name, phase, kw.get("speed", 1.0), kw.get("exaggeration", 1.0),
                                        kw.get("travel", 0.0), self.arm_style(name, kw.get("allow_pending", False)),
                                        kw.get("fluid", True))
                self.apply_ik(it0, ik)
                leg = it0.legs.get(ctl.side)
                new["lock"] = bool(leg.planted) if leg is not None else True
        elif ctl.kind == "arm":
            new = {**cur, "enabled": True, "mode": "fk", "fk": ctl.fk_params(pose0)}
            # an IK arm may have moved the shoulder to reach (clavicle compensation, beyond the rhythm): carry the
            # difference so the FK pose reproduces the IK pose exactly
            ik_try = {**ik, limb: new}
            _, pose_f, _ = self.plan_and_solve(name, phase, ik=ik_try, **kw)
            ci = self.sk.idx[f"clavicle_{ctl.side}"]
            extra = SIDE_SIGN[ctl.side] * float(pose0.theta[ci] - pose_f.theta[ci])
            if abs(extra) > 1e-6:
                new = {**new, "fk": {**new["fk"], "clavicle_extra": extra}}
        else:
            new = {**cur, "enabled": False, "mode": "fk"}
        ik[limb] = new
        _, pose1, info1 = self.plan_and_solve(name, phase, ik=ik, **kw)
        J0, J1 = info0["joints_posed"], info1["joints_posed"]
        jump = max(float(np.hypot(J0[b][0] - J1[b][0], J0[b][1] - J1[b][1])) for b in J0)
        return {"limb": limb, "state": new, "jump_px": round(jump, 4),
                "max_rotation_jump_deg": round(float(np.abs(pose1.theta - pose0.theta).max()), 4)}

    def compose_live_head(self, pose: PoseParams, live: dict[str, float]) -> dict[str, Any]:
        """Live puppeteering channel: head tilt is added to the neck/head bones only, on top of the body pose.
        Head turn needs turned-head drawings (reported, not faked)."""
        I = self.sk.idx
        tilt = float(live.get("tilt", 0.0))
        lo, hi = self.solver.limit("neck", "bend")
        add_n = float(np.clip(pose.theta[I["neck"]] + 0.4 * tilt, lo, hi)) - pose.theta[I["neck"]]
        pose.theta[I["neck"]] += add_n
        lo, hi = self.solver.limit("head", "bend")
        add_h = float(np.clip(pose.theta[I["head"]] + tilt - add_n, lo, hi)) - pose.theta[I["head"]]
        pose.theta[I["head"]] += add_h
        out = {"tilt_applied": round(add_n + add_h, 2), "bones": ["neck", "head"]}
        if live.get("turn"):
            out["turn"] = "not applied: head turns need turned-head drawings (REQUIRES_NEW_ARTWORK)"
        return out

    def pose(self, name: str, phase: float | None = None, speed: float = 1.0, exaggeration: float = 1.0,
             travel: float = 0.0, allow_pending: bool = False, overrides: dict[str, float] | None = None,
             live_head: dict[str, float] | None = None, visible: list[str] | None = None, pad: int = 160,
             skeleton_only: bool = False, fluid: bool = True, ik: dict[str, Any] | None = None,
             switches: dict[str, Any] | None = None, switch_time: float | None = None,
             corrections_stateful: bool = False, correction_overrides: dict[str, bool] | None = None,
             hand_layers: dict[str, str] | None = None, hand_actions: dict[str, str | None] | None = None,
             hand_targets: dict[str, Any] | None = None, hand_state: dict[str, Any] | None = None,
             hand_time: float | None = None, hand_beat: int | None = None, hand_aim: bool = True,
             previous_wrists: dict[str, Any] | None = None, evaluate_garments: bool = True) -> dict[str, Any]:
        """Processing order: intent -> contact/IK targets -> skeletal solve -> hand selection (+ wrist aim) ->
        correction selection -> garment deformation -> switch resolution -> composition -> evaluation.

        Hand selection: ``hand_actions`` (side -> action; default from the preset), ``hand_targets`` (side ->
        [x, y]: pointing/gripping targets; a gripping target also moves that arm's IK target so the grip centre
        lands on it), ``hand_state`` (side -> HandSelectionState, for sequences), ``hand_time`` (s), ``hand_beat``."""
        spec = SPEC_BY_NAME[name]
        t0 = time.perf_counter()
        ik, grip_report = self._grip_ik(name, phase, speed, exaggeration, travel, overrides, live_head, allow_pending,
                                        fluid, ik, switches, switch_time, hand_actions, hand_targets)
        it, pose, info = self.plan_and_solve(name, phase, speed, exaggeration, travel, overrides, live_head,
                                             allow_pending, fluid, ik)
        hsel = self.select_hands(name, it, pose, allow_pending, switches,
                                 switch_time if switch_time is not None else it.phase, hand_actions, hand_targets,
                                 hand_state, hand_time, hand_beat, hand_aim, previous_wrists)
        if any((d.get("aim_applied") or {}).get("applied_deg") for d in hsel["decisions"].values()):
            info.update(self.solver.K.joints(pose))
            info["anatomical"] = self.solver.anatomical(pose)
        for s, g in grip_report.items():  # the grip centre on the final pose (after the last IK pass)
            gw = self.feature_world(s, pose, "grip_center") if "grip_center" in g else None
            if gw is not None:
                err = float(np.hypot(*(np.asarray(hand_targets[s], float) - gw)))
                g.update(grip_center=[round(float(v), 2) for v in gw], error_px=round(err, 2),
                         wrist_target=[round(float(v), 2) for v in ik[f"arm_{s}"]["target"]],
                         target=[round(float(v), 2) for v in hand_targets[s]],
                         ik_status=(info.get("arms") or {}).get(s, {}).get("status"))
        hsel["grip"] = grip_report
        t_solve = time.perf_counter() - t0
        t1 = time.perf_counter()
        forced = dict(correction_overrides or {})
        for g, gs in (switches or {}).items():  # explicit user choice on a correction group forces it on/off
            if g.startswith("corr_") and gs.get("user") and not gs.get("auto", True):
                forced[g[5:]] = gs["user"] == "on"
        readings = correction_readings_from_pose(self.solver, pose)
        if self.garment_mode == "structured":
            from ..garmentrig.corrections import pose_readings

            readings = pose_readings(spec, it, readings, pose, self.sk)
        csw = self.corr_switch.evaluate(readings, allow_pending, corrections_stateful, forced or None)
        corr = self.gdef.corrections(pose, it.garment)
        t_corr = time.perf_counter() - t1
        t1 = time.perf_counter()
        hands, switch_res = self.resolve_switches(it, allow_pending, switches,
                                                  switch_time if switch_time is not None else it.phase, hsel)
        for s, lay in (hand_layers or {}).items():  # Hand Library preview of one candidate (never an approval)
            if lay in self.variants or lay == f"hand_{s}":
                hands[s] = {**hands[s], "layer": lay, "preview_override": True,
                            "pending_review": lay != f"hand_{s}", "note": "Hand Library preview"}
        t_switch = time.perf_counter() - t1
        res: dict[str, Any] = {"name": name, "title": spec.title, "kind": spec.kind, "phase": it.phase,
                               "params": {"speed": speed, "exaggeration": exaggeration, "travel": travel,
                                          "allow_pending": allow_pending, "fluid": fluid},
                               "intent": it, "pose": pose, "solve": info, "hands": hands,
                               "corrections": corr["active"], "spec": spec.to_json(), "pad": pad,
                               "switches": switch_res, "correction_switches": csw, "hand_selection": hsel,
                               "timing_ms": {"solve": round(t_solve * 1000, 2), "corrections": round(t_corr * 1000, 2),
                                             "switches": round(t_switch * 1000, 2)}}
        solver_notes = [n for s in SIDES if s in info["legs"] for n in [info["legs"][s]["note"]] if n]
        if skeleton_only:
            res["feasibility"] = self._feasibility(spec, it, hands, corr, solver_notes, csw, False, exaggeration)
            return res
        vis = visible or [c for c in self.layers]
        order = motion_order(vis, it, hands, set(self.variants))
        for s in SIDES:  # alignment offsets of the active hand variant (rigid, about the wrist pivot)
            off = hands[s]["offset"]
            base = hands[s].get("layer") or (f"hand_{s}__{hands[s]['drawing']}" if hands[s]["drawing"] != "open" else f"hand_{s}")
            if base in order and any(abs(off[k]) > 1e-9 for k in ("dx", "dy", "rot")):
                pivot = self.sk.bone(f"hand_{s}").head
                order[order.index(base)] = self._offset_layer(base, pivot, off)
        t1 = time.perf_counter()
        r = self.dfm.render_with(pose, order, corr, pad)
        tears = self.dfm.tears_with(pose, r, corr, self.dark)
        t_render = time.perf_counter() - t1
        gussets, gusset_need = [], False
        armpit = sum(1 for s in SIDES for (x, y) in tears["exposed_gap"] + tears["visible_seam"]
                     if np.hypot(x - pad - info["joints_posed"][f"upper_arm_{s}"][0],
                                 y - pad - info["joints_posed"][f"upper_arm_{s}"][1])
                     <= 2.2 * self.sk.bone(f"upper_arm_{s}").h)
        if armpit >= 6:
            gusset_need = True
            states = [self.inventory.correctives.get(f"gusset__{name}__{s}", "missing") for s in SIDES]
            if all(st == "approved" for st in states if st != "missing") and any(st != "missing" for st in states) \
                    or allow_pending:
                gussets = C.armpit_gussets(r, tears, self.sk, info, self.dfm, pose)
                if gussets:
                    tears = self.dfm.tears_with(pose, r, corr, self.dark)
        feas = self._feasibility(spec, it, hands, corr, solver_notes, csw, gusset_need, exaggeration)
        pending = [f"hand {s}: {h['drawing']}" for s, h in hands.items() if h["pending_review"]]
        if gussets and any(self.inventory.correctives.get(f"gusset__{name}__{g['side']}") != "approved"
                           for g in gussets):
            pending.append("armpit gusset (generated)")
        q = self.quality.frame(it, pose, info, r, tears, pending, int(r["generated_visible"].sum()))
        res.update({"image": r["image"], "render": r, "tears": tears, "order": order, "gussets": gussets,
                    "feasibility": feas, "quality": q})
        if self.garment_mode == "structured" and evaluate_garments:
            from ..garmentrig.evaluate import GarmentDistortionEvaluator

            t2 = time.perf_counter()
            ge = GarmentDistortionEvaluator(self).evaluate(res)
            res["garment_eval"] = ge
            res["timing_ms"]["garment_eval"] = round((time.perf_counter() - t2) * 1000, 1)
            feas["garments"] = {k: v["status"] for k, v in ge.get("garments", {}).items()}
            for m in ge.get("missing_artwork", []):
                if not any(x.get("asset") == m for x in feas["missing"] + feas.get("optional_assets", [])):
                    feas.setdefault("optional_assets", []).append(
                        {"asset": m, "sides": [], "state": "missing", "why": "garment evaluator: needed to avoid "
                                                                              "visible garment distortion"})
        res["timing_ms"].update({"render_and_tears": round(t_render * 1000, 1),
                                 "total": round((time.perf_counter() - t0) * 1000, 1)})
        return res

    def _feasibility(self, spec, it, hands, corr, solver_notes, csw, gusset_need, exaggeration) -> dict[str, Any]:
        feas = self.feas.evaluate(spec, it, hands, corr["active"], solver_notes, tears_need_gusset=gusset_need,
                                  exaggeration=exaggeration)
        # explicit switch choices without a usable drawing: NEEDS_ARTWORK with the fallback that is shown
        for s, h in hands.items():
            if h.get("needs_artwork") and h["source"] in ("user", "keyframe"):
                feas["missing"].append({"asset": f"hand:{HAND_POSES[h['needs_artwork']]}", "sides": [s],
                                        "state": "missing", "why": f"selected hand pose '{h['needs_artwork']}'",
                                        "fallback": h["pose"]})
                feas["status"] = "NEEDS_ARTWORK"
        feas["hand_fallbacks"] = [{"side": s, "missing": h["missing_preferred"], "shown": h["fallback"]}
                                  for s, h in hands.items() if h.get("missing_preferred")]
        # pose-aware corrections that triggered without a drawing: deformation fallback, asset recommended
        for c in csw:
            if c["needs_artwork"]:
                entry = {"asset": f"corrective:{c['asset']}", "sides": [c["side"]], "state": "missing",
                         "why": f"{c['title']} triggered at {c['value_deg']:.0f} deg; band-rule deformation used instead"}
                if c["required"]:  # the deformation-only fallback is visibly broken: the pose needs the drawing
                    feas["missing"].append(entry)
                    feas["status"] = "NEEDS_ARTWORK"
                    feas["alternative"] = feas.get("alternative") or "reduce the bend below the trigger angle"
                else:
                    feas["optional_assets"].append(entry)
        return feas

    def cycle(self, name: str, samples: int = 32, speed: float = 1.0, exaggeration: float = 1.0,
              travel: float = 0.0, allow_pending: bool = False, fluid: bool = True) -> dict[str, Any]:
        """Gait metrics over ``samples`` evenly spaced phases plus phase 1 (= phase 0 one cycle later)."""
        kind = {"running": "run", "casual_walk": "walk"}[name]
        style = self.arm_style(name, allow_pending)
        frames = []
        for i in range(samples + 1):
            ph, cyc = (i / samples, 0.0) if i < samples else (0.0, 1.0)
            it = gait(self.profile, kind, ph, speed, exaggeration, travel, cyc, arm_style=style, fluid=fluid)
            pose, info = self.solver.solve(it)
            frames.append((it, info))
        return self.quality.cycle(frames, travel)

    # -- comparison: the Phase 3 engine (before) -------------------------------------------------------
    def legacy(self, name: str, pad: int = 160) -> dict[str, Any] | None:
        if name not in OLD_PRESETS:
            return None
        if self._legacy is None:
            dfm = Deformer(self.sk, {**self.layers, **self.variants}, self.generated, reference=self.source,
                           variants=set(self.variants))
            self._legacy = (dfm, PoseSolver(self.sk, dfm))
        dfm, solver = self._legacy
        pr = OLD_PRESETS[name]
        pose, info = solver.solve(pr)
        order = natural_order([c for c in self.layers], pr, set(self.variants))
        r = dfm.render(pose, order, pad=pad)
        info.update(solver.joints_world(pose))
        return {"image": r["image"], "solve": info, "pose": pose, "pad": pad, "render": r}

    # -- diagnostics ---------------------------------------------------------------------------------
    def diagnostics(self, res: dict[str, Any], kinds: list[str]) -> dict[str, np.ndarray]:
        r, info, pad = res["render"], res["solve"], res["pad"]
        H, W = r["image"].shape[:2]
        out: dict[str, np.ndarray] = {}
        J, T = info["joints_posed"], info["bone_tails_posed"]
        P = lambda q: (q[0] + pad, q[1] + pad)
        for k in kinds:
            im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            d = ImageDraw.Draw(im)
            if k == "skeleton":
                for b in self.sk.bones:
                    col = (255, 140, 40, 255) if b.side == "r" else (60, 180, 255, 255) if b.side == "l" else (250, 220, 60, 255)
                    d.line((*P(J[b.name]), *P(T[b.name])), fill=col, width=4)
                for n, q in J.items():
                    x, y = P(q)
                    d.ellipse((x - 6, y - 6, x + 6, y + 6), fill=(20, 110, 230, 255), outline=(255, 255, 255, 255))
            elif k == "garments":
                lab = self.garments.overlay()
                posed = self._warp_rest_overlay(lab, res)
                im = Image.fromarray(posed, "RGBA")
                d = ImageDraw.Draw(im)
                h = self.garments.hem
                q = self.solver.K.point(0, (h["pivot_x"], h["pivot_y"]), res["pose"])
                for nm in ("upper_arm_r", "upper_arm_l", "shin_r", "shin_l", "forearm_r", "forearm_l"):
                    x, y = P(J[nm])
                    d.rectangle((x - 7, y - 7, x + 7, y + 7), outline=(255, 255, 255, 255), width=3)
                x, y = P(q)
                d.polygon([(x, y - 10), (x + 10, y), (x, y + 10), (x - 10, y)], outline=(255, 40, 120, 255), width=3)
            elif k == "boundaries":
                ov = np.zeros((H, W, 4), np.uint8)
                rng = np.random.default_rng(7)
                for cid, a in r["alphas"].items():
                    edge = a & ~ndimage.binary_erosion(a)
                    ov[edge] = (*rng.integers(40, 255, 3).tolist(), 255)
                im = Image.fromarray(ov, "RGBA")
            elif k == "weights":
                corr = self.gdef.corrections(res["pose"], res["intent"].garment)
                im = Image.fromarray(self.dfm.weight_overlay(res["pose"], corr, pad, r), "RGBA")
            elif k == "overlaps":
                cnt = np.zeros((H, W), np.int16)
                for a in r["alphas"].values():
                    cnt += a
                ov = np.zeros((H, W, 4), np.uint8)
                ov[cnt >= 2] = (255, 0, 255, 140)
                im = Image.fromarray(ov, "RGBA")
            elif k == "contacts":
                gy = self.profile.ground_y + pad
                d.line((0, gy, W, gy), fill=(0, 170, 0, 255), width=3)
                for s in SIDES:
                    x, y = P(J[f"foot_{s}"])
                    planted = info["contact"][s]
                    col = (0, 200, 0, 255) if planted else (230, 160, 0, 255)
                    d.ellipse((x - 10, y - 10, x + 10, y + 10), outline=col, width=4)
                    if planted:
                        d.line((x, y, x, gy), fill=col, width=2)
            elif k == "com":
                cx, cy = P(info["com"])
                gy = self.profile.ground_y + pad
                d.line((cx, cy, cx, gy), fill=(230, 120, 0, 255), width=3)
                d.ellipse((cx - 11, cy - 11, cx + 11, cy + 11), outline=(230, 120, 0, 255), width=4)
                xr = info["support"].get("x_range")
                if xr:
                    d.line((xr[0] + pad, gy + 8, xr[1] + pad, gy + 8), fill=(0, 120, 0, 255), width=8)
            elif k == "provenance":
                ov = np.zeros((H, W, 4), np.uint8)
                ov[r["generated_visible"]] = (255, 0, 255, 200)
                im = Image.fromarray(ov, "RGBA")
            elif k == "missing":
                y = 10
                for m in res["feasibility"]["missing"]:
                    d.rectangle((8, y - 2, 8 + 9 * len(m["asset"]) + 140, y + 16), fill=(180, 0, 0, 220))
                    d.text((12, y), f"MISSING {m['asset']} ({m['state']})", fill=(255, 255, 255, 255))
                    y += 22
                for s, h in res["hands"].items():
                    if h["note"]:
                        x, yy = P(J[f"hand_{s}"])
                        d.ellipse((x - 26, yy - 26, x + 26, yy + 26), outline=(220, 0, 0, 255), width=4)
            elif k == "correctives":
                ov = np.zeros((H, W, 4), np.uint8)
                for g in res.get("gussets", []):
                    poly = [(x + pad, y + pad) for x, y in g["polygon_posed_px"]]
                    if len(poly) >= 3:
                        ImageDraw.Draw(im).polygon(poly, outline=(255, 0, 0, 255), width=3)
                for c in res["corrections"]:
                    if c["rule"].startswith("band:"):
                        bn = c["rule"][5:]
                        x, y = P(J[bn])
                        rad = (self.gsk.rp[self.sk.idx[bn]] + self.gsk.rc[self.sk.idx[bn]]) / 2
                        a = int(60 + 150 * min(1.0, (c["band_scale"] - 1) / 0.7))
                        ImageDraw.Draw(im).ellipse((x - rad, y - rad, x + rad, y + rad), outline=(255, 200, 0, a), width=3)
                del ov
            out[k] = np.asarray(im)
        return out

    def _warp_rest_overlay(self, rgba: np.ndarray, res: dict[str, Any]) -> np.ndarray:
        """Move a rest-pose overlay with the pose (forward map of the owning layer's pixels)."""
        r, pad = res["render"], res["pad"]
        H, W = r["image"].shape[:2]
        out = np.zeros((H, W, 4), np.uint8)
        corr = self.gdef.corrections(res["pose"], res["intent"].garment)
        self.gsk.set_bands(corr["rp"], corr["rc"])
        self.dfm.terms = corr["terms"]
        try:
            for cid in r["order"]:
                if cid not in self.dfm.info:
                    continue
                inf = self.dfm.info[cid]
                x0, y0 = inf["origin"]
                ay, ax = np.nonzero(inf["alpha0"])
                ay, ax = ay[::2], ax[::2]
                pts = np.stack([ax + x0, ay + y0], 1).astype(np.float64)
                cols = rgba[ay + y0, ax + x0]
                keep = cols[:, 3] > 0
                q = self.dfm.forward(cid, pts[keep], res["pose"])
                qx, qy = np.rint(q[:, 0] + pad).astype(int), np.rint(q[:, 1] + pad).astype(int)
                ok = (qx >= 0) & (qx < W) & (qy >= 0) & (qy < H)
                out[qy[ok], qx[ok]] = cols[keep][ok]
        finally:
            self.dfm.terms = []
        return ndimage.grey_dilation(out, size=(3, 3, 1))

    def closeups(self, res: dict[str, Any], before: dict[str, Any] | None = None, zoom: float = 1.6) -> Image.Image:
        """Blazer shoulders, sleeves/elbows, pelvis and hem, trouser knees, shoes and ankles."""
        J = res["solve"]["joints_posed"]
        pad = res["pad"]
        regions = [("blazer shoulder r", "upper_arm_r", 120), ("blazer shoulder l", "upper_arm_l", 120),
                   ("sleeve / elbow r", "forearm_r", 100), ("sleeve / elbow l", "forearm_l", 100),
                   ("pelvis + blazer hem", "pelvis", 150), ("trouser knee r", "shin_r", 100),
                   ("trouser knee l", "shin_l", 100), ("shoe / ankle r", "foot_r", 100), ("shoe / ankle l", "foot_l", 100)]
        a_img = on_bg(res["image"], (214, 230, 214))
        b_img = on_bg(before["image"], (214, 230, 214)) if before is not None else None
        bJ = before["solve"]["joints_posed"] if before is not None else None
        cells = []
        for title, bn, r in regions:
            size = (int(2 * r * zoom), int(2 * r * zoom))
            col = Image.new("RGB", (size[0], size[1] * (2 if b_img else 1) + 24), "white")
            ImageDraw.Draw(col).text((4, 5), title + ("  (top: Phase 3, bottom: Phase 3.1)" if b_img else ""),
                                     fill=(0, 0, 0))
            y0 = 24
            if b_img:
                x, y = bJ[bn]
                box = (int(x + pad - r), int(y + pad - r), int(x + pad + r), int(y + pad + r))
                col.paste(b_img.crop(box).resize(size, Image.LANCZOS), (0, y0))
                y0 += size[1]
            x, y = J[bn]
            box = (int(x + pad - r), int(y + pad - r), int(x + pad + r), int(y + pad + r))
            col.paste(a_img.crop(box).resize(size, Image.LANCZOS), (0, y0))
            cells.append(col)
        h = max(c.height for c in cells)
        w = sum(c.width for c in cells) + 6 * (len(cells) - 1)
        sheet = Image.new("RGB", (w, h), "white")
        x = 0
        for c in cells:
            sheet.paste(c, (x, 0))
            x += c.width + 6
        return sheet

    def library(self) -> list[dict[str, Any]]:
        return [s.to_json() for s in LIBRARY]


# -- package-level evidence (CLI: autorig motion <package>) ---------------------------------------------------
def engine_for_package(pkg, view: str = "front", garment_mode: str = "structured") -> tuple["MotionEngine", list[str]]:
    """MotionEngine for a Phase 2 package, built exactly as the editor builds it (edited bones, masks, approvals)."""
    import json
    from pathlib import Path

    from ..editor.artwork import ArtworkModel
    from ..editor.posesvc import PoseService
    from ..editor.project import EditorProject
    from .profile import load_overrides

    pkg = Path(pkg)
    proj, art = EditorProject(pkg), ArtworkModel(pkg)
    ps = PoseService(pkg, art)
    st = proj.state()
    m = art.masks(st["mask_ops"])
    sk = ps.skeleton(st, m, None)
    rej = {k for k, v in st["approvals"].items() if v.get("status") == "rejected"}
    layers = {c: art.layer_rgba(c, m, rej) for c in m}
    gen = {c: art.generated_mask(c, m, rej) for c in m}
    cids = [k.split(":", 1)[1] for k in st["approvals"] if k.startswith("corrective:gusset")]
    from ..handstore import engine_input

    hs = engine_input(pkg, st.get("hand_store"), sk, layers, ps.variants, st["approvals"], art.source)
    from ..garmentrig.structure import load_config

    eng = MotionEngine(sk, layers, gen, art.source, ps.variants, st["approvals"], view, load_overrides(pkg), cids,
                       corrective_manifest(pkg), hs, garment_mode, load_config(pkg),
                       (st.get("garment_weights") or {}).get("ops"))
    eng.hand_select = NaturalHandSelectionEngine.for_character(load_rules(pkg), (st.get("hand_selection") or {}).get("weights"))
    vis = [L["id"] for L in st["layers"] if L["visible"] and L["kind"] == "bitmap" and L["id"] in m]
    del json
    return eng, vis


def corrective_manifest(pkg) -> dict[str, Any] | None:
    import json
    from pathlib import Path

    f = Path(pkg) / "natural_pose" / "corrective" / "manifest.json"
    return json.loads(f.read_text()) if f.is_file() else None


def _tile(img: np.ndarray, box, title: str, sub: str, colour=(34, 40, 60)) -> Image.Image:
    im = on_bg(img).crop(box)
    cell = Image.new("RGB", (im.width, im.height + 58), "white")
    d = ImageDraw.Draw(cell)
    d.rectangle((0, 0, im.width, 54), fill=colour)
    d.text((8, 8), title, fill=(255, 255, 255))
    d.text((8, 30), sub, fill=(210, 220, 255))
    cell.paste(im, (0, 58))
    return cell


def _rows(rows: list[list[Image.Image]], scale: float = 1.0, gap: int = 14) -> Image.Image:
    rows = [[t.resize((int(t.width * scale), int(t.height * scale)), Image.LANCZOS) for t in r] for r in rows if r]
    W = max(sum(t.width for t in r) + gap * (len(r) - 1) for r in rows) + 2 * gap
    H = sum(max(t.height for t in r) for r in rows) + gap * (len(rows) + 1)
    sheet = Image.new("RGB", (W, H), (236, 238, 242))
    y = gap
    for r in rows:
        x = gap
        for t in r:
            sheet.paste(t, (x, y))
            x += t.width + gap
        y += max(t.height for t in r) + gap
    return sheet


def _summary(res: dict[str, Any]) -> dict[str, Any]:
    q, f, s = res["quality"], res["feasibility"], res["solve"]
    return {"name": res["name"], "title": res["title"], "phase": res["phase"], "params": res["params"],
            "feasibility": f, "quality": {k: q[k] for k in ("checks", "numerical", "warnings", "review_items",
                                                           "artistic_review_required", "visual_quality")},
            "hands": res["hands"], "corrections": res["corrections"], "order": res["order"],
            "gussets": [{k: g[k] for k in ("side", "pixels", "torn_pairs")} for g in res["gussets"]],
            "solve": {k: s[k] for k in ("joints_posed", "contact", "contact_loaded", "pelvis_shift_px", "clamps",
                                        "violations", "anatomical", "legs", "arms", "clavicle_elevation")
                      if k in s} | {"com": [round(float(v), 2) for v in s["com"]]},
            "intent": {k: v for k, v in res["intent"].to_json().items() if k not in ("legs", "arms")},
            "timing_ms": res["timing_ms"]}


def motion_report(pkg, out=None, progress=print) -> dict[str, Any]:
    """Render the evidence set for Phase 3.1 into ``<package>/natural_pose/motion`` (or ``out``)."""
    import json
    from pathlib import Path

    pkg = Path(pkg)
    out = Path(out) if out else pkg / "natural_pose" / "motion"
    (out / "closeups").mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    eng, vis = engine_for_package(pkg)
    progress(f"engine built in {eng.build_s:.1f} s")
    (out / "profile.json").write_text(json.dumps(eng.profile.to_json(), indent=1, default=float))
    (out / "garments.json").write_text(json.dumps(eng.garments.to_json(), indent=1, default=float))
    Image.fromarray(eng.garments.overlay(), "RGBA").save(out / "garment_regions.png", optimize=True)
    results: dict[str, Any] = {"presets": {}, "cycles": {}, "generated_s": None}
    pad = 160
    H, W = eng.source.shape[:2]
    box = (pad + int(eng.profile.midline_x) - 360, pad + 40, pad + int(eng.profile.midline_x) + 360, pad + H + 60)
    renders: dict[str, Any] = {}

    def run(key, name, phase=None, allow_pending=False):
        r = eng.pose(name, phase, visible=vis, allow_pending=allow_pending, pad=pad)
        renders[key] = r
        results["presets"][key] = _summary(r)
        progress(f"{key}: {r['feasibility']['status']}, checks {r['quality']['numerical']['passed']}/"
                 f"{r['quality']['numerical']['total']}, {r['timing_ms']['total']:.0f} ms")
        return r

    def lab(r):
        q = r["quality"]["numerical"]
        return f"{r['feasibility']['status']} | checks {q['passed']}/{q['total']} | review required"

    neutral = np.zeros((H + 2 * pad, W + 2 * pad, 4), np.uint8)
    for c in motion_order(vis, eng.planner.plan("relaxed_standing"), {s: {"drawing": "open"} for s in SIDES}, set()):
        lay = eng.layers.get(c)
        if lay is None:
            continue
        sub = neutral[pad:pad + H, pad:pad + W]
        a = lay[..., 3:4].astype(np.float32) / 255
        sub[..., :3] = (lay[..., :3] * a + sub[..., :3] * (1 - a)).astype(np.uint8)
        sub[..., 3] = np.maximum(sub[..., 3], lay[..., 3])
    row1 = [_tile(neutral, box, "Original neutral (reference pixels)", "identity pose: bit-exact")]
    for key, name in (("relaxed_standing", "relaxed_standing"), ("pointing_speaking", "pointing_speaking"),
                      ("both_hands_raised", "both_hands_raised"), ("head_tilt_compensated", "head_tilt_compensated"),
                      ("conversational_gesture", "conversational_gesture"), ("one_hand_shrug", "one_hand_shrug"),
                      ("hands_on_hips", "hands_on_hips")):
        r = run(key, name)
        row1.append(_tile(r["image"], box, r["title"], lab(r)))
    row2 = []
    for i in range(4):
        r = run(f"casual_walk@{i / 4:.3f}", "casual_walk", i / 4)
        row2.append(_tile(r["image"], box, f"Walking {i / 4:.2f} ({r['intent'].channels['phase_name']})", lab(r)))
    row3, row4 = [], []
    for i in range(8):
        r = run(f"running@{i / 8:.3f}", "running", i / 8)
        row3.append(_tile(r["image"], box, f"Running {i / 8:.3f} ({r['intent'].channels['phase_name']}), open hands",
                          lab(r)))
    for i in range(8):
        r = run(f"running_fists_preview@{i / 8:.3f}", "running", i / 8, allow_pending=True)
        row4.append(_tile(r["image"], box, f"Running {i / 8:.3f}, PREVIEW of pending fists", lab(r), (120, 40, 40)))
    row5 = []
    for key in ("sitting", "natural_squat", "side_lean", "leaning_forward", "hands_at_sides", "leaning_backward"):
        r = run(key, key)
        note = " (fallback shown)" if key.startswith("leaning") else " (approximation)" if key == "sitting" else ""
        row5.append(_tile(r["image"], box, r["title"] + note, lab(r)))
    _rows([row1, row2 + row5[:4], row3, row4, row5[4:]], 0.6).save(out / "contact_sheet.jpg", quality=90)
    _rows([row3]).save(out / "run_cycle.jpg", quality=90)
    _rows([row4]).save(out / "run_cycle_fists_preview.jpg", quality=90)
    progress("contact sheets written")
    # cycles (skeleton level, 64 samples) in place and moving
    for name in ("running", "casual_walk"):
        for travel in (0.0, 40.0):
            for ap in ((False, True) if name == "running" else (False,)):
                key = f"{name}{'_moving' if travel else ''}{'_fists' if ap else ''}"
                results["cycles"][key] = eng.cycle(name, 64, travel=travel, allow_pending=ap)
    # fluid motion: animated cycles (full artwork) and hand-path plots, plain vs fluid
    anim = out / "animations"
    anim.mkdir(exist_ok=True)
    cyc = {}
    for key, name, n, ap, fl in (("run_fluid", "running", 24, False, True), ("run_plain", "running", 24, False, False),
                                 ("walk_fluid", "casual_walk", 16, False, True),
                                 ("run_fists_preview_fluid", "running", 16, True, True)):
        cyc[key] = render_cycle(eng, name, n, vis, allow_pending=ap, fluid=fl, pad=pad)
        progress(f"animated cycle {key}: {n} frames")
    for key in ("run_fluid", "walk_fluid", "run_fists_preview_fluid"):  # GIF renders inline on GitHub
        save_animation(cyc[key]["frames"], cyc[key]["duration_ms"], anim / f"{key}.gif", scale=0.4)
    both = side_by_side(cyc["run_plain"]["frames"], cyc["run_fluid"]["frames"])
    save_animation(both, cyc["run_fluid"]["duration_ms"], anim / "run_plain_vs_fluid.gif", scale=0.4,
                   labels=["left: plain cycle   right: fluid (overlap, follow-through, arcs)"] * len(both))
    hand_paths(eng).save(out / "hand_paths.png", optimize=True)
    for name in ("running", "casual_walk"):
        results["cycles"][f"{name}_plain"] = eng.cycle(name, 64, fluid=False)
    progress("animations and hand paths written")
    # before / after (the Phase 3 engine on the same package) and close-ups
    pairs = []
    for key, name in (("relaxed_standing", "relaxed_standing"), ("pointing_speaking", "pointing_speaking"),
                      ("both_hands_raised", "both_hands_raised"), ("natural_squat", "natural_squat"),
                      ("sitting", "sitting"), ("running@0.250", "running"), ("casual_walk@0.250", "casual_walk")):
        b = eng.legacy(name, pad)
        new = renders[key]
        pairs.append([_tile(b["image"], box, f"Phase 3: {name}", "previous engine", (90, 90, 90)),
                      _tile(new["image"], box, f"Phase 3.1: {new['title']}", lab(new))])
        eng.closeups(new, b).save(out / "closeups" / f"{key.replace('@', '_')}.jpg", quality=90)
    _rows([sum(pairs[:4], []), sum(pairs[4:], [])], 0.6).save(out / "before_after.jpg", quality=90)
    progress("before/after and close-ups written")
    # diagnostics for one run frame
    diag = eng.diagnostics(renders["running@0.250"], list(DIAGNOSTICS))
    tiles = []
    base = on_bg(renders["running@0.250"]["image"])
    for k, ov in diag.items():
        im = base.copy().convert("RGBA")
        im.alpha_composite(Image.fromarray(ov, "RGBA"))
        tiles.append(_tile(np.asarray(im), box, f"diagnostic: {k}", "running 0.25"))
    _rows([tiles[:5], tiles[5:]], 0.5).save(out / "diagnostics_running.jpg", quality=90)
    results["generated_s"] = round(time.time() - t0, 1)
    results["library"] = eng.library()
    results["profile"] = eng.profile.to_json()
    (out / "results.json").write_text(json.dumps(results, indent=1, default=lambda o: o.tolist()
                                                 if isinstance(o, np.ndarray) else float(o)))
    progress(f"done in {results['generated_s']} s")
    return results


# -- animated cycle previews ----------------------------------------------------------------------------------
def cycle_duration_s(name: str, speed: float = 1.0) -> float:
    from .planner import CYCLE_SECONDS
    return CYCLE_SECONDS.get({"running": "run", "casual_walk": "walk"}.get(name, ""), 1.0) / max(speed, 0.1)


def render_cycle(eng: "MotionEngine", name: str, frames: int = 24, visible: list[str] | None = None,
                 speed: float = 1.0, exaggeration: float = 1.0, allow_pending: bool = False, fluid: bool = True,
                 pad: int = 160, progress=None, lock=None, ik: dict[str, Any] | None = None,
                 switches: dict[str, Any] | None = None) -> dict[str, Any]:
    """Render ``frames`` evenly spaced phases of a gait with the full artwork. Returns the frames (RGBA, cropped to
    their common bounds), the crop box in padded canvas coordinates and the per-frame duration. Switch keyframes
    are evaluated at the gait phase; correction triggers keep their hysteresis state across the frames."""
    import contextlib

    imgs, joints = [], []
    eng.corr_switch.reset()
    for i in range(frames):
        with (lock or contextlib.nullcontext()):
            r = eng.pose(name, i / frames, speed, exaggeration, 0.0, allow_pending, visible=visible, pad=pad,
                         fluid=fluid, ik=ik, switches=switches, corrections_stateful=True)
        imgs.append(r["image"])
        joints.append(r["solve"]["joints_posed"])
        if progress:
            progress(i + 1, frames)
    acc = np.zeros(imgs[0].shape[:2], bool)
    for im in imgs:
        acc |= im[..., 3] > 0
    ys, xs = np.nonzero(acc)
    m = 16
    box = (max(0, int(xs.min()) - m), max(0, int(ys.min()) - m), min(acc.shape[1], int(xs.max()) + m + 1),
           min(acc.shape[0], int(ys.max()) + m + 1))
    crops = [im[box[1]:box[3], box[0]:box[2]] for im in imgs]
    return {"frames": crops, "box": box, "pad": pad, "joints": joints,
            "duration_ms": round(1000 * cycle_duration_s(name, speed) / frames, 1)}


def save_animation(frames: list[np.ndarray], duration_ms: float, path, scale: float = 1.0, bg=(255, 255, 255),
                   labels: list[str] | None = None) -> None:
    """Animated WebP or GIF (by suffix), looping forever, frames composited on ``bg``."""
    from pathlib import Path

    path = Path(path)
    out = []
    for i, f in enumerate(frames):
        im = on_bg(f, bg)
        if scale != 1.0:
            im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))), Image.LANCZOS)
        if labels:
            ImageDraw.Draw(im).text((6, 6), labels[i], fill=(0, 0, 0))
        out.append(im)
    kw = dict(save_all=True, append_images=out[1:], duration=int(round(duration_ms)), loop=0)
    if path.suffix.lower() == ".gif":
        pal = [im.quantize(colors=200, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE) for im in out]
        pal[0].save(path, **{**kw, "append_images": pal[1:]}, optimize=True, disposal=1)
    else:
        out[0].save(path, **kw, quality=88, method=4)


def side_by_side(a: list[np.ndarray], b: list[np.ndarray], gap: int = 12) -> list[np.ndarray]:
    """Two equally long frame lists placed next to each other (bottom-aligned), for before/after animations."""
    out = []
    for x, y in zip(a, b):
        H = max(x.shape[0], y.shape[0])
        W = x.shape[1] + gap + y.shape[1]
        c = np.zeros((H, W, 4), np.uint8)
        c[H - x.shape[0]:, :x.shape[1]] = x
        c[H - y.shape[0]:, x.shape[1] + gap:] = y
        out.append(c)
    return out


def hand_paths(eng: "MotionEngine", samples: int = 64) -> Image.Image:
    """Hand (wrist) paths relative to the chest over one cycle, plain vs fluid, for run and walk. A lockstep arm
    retraces a line; follow-through and arcs open it into a loop."""
    cells = []
    for name, kind in (("running", "run"), ("casual_walk", "walk")):
        style = eng.arm_style(name, False)
        for fluid in (False, True):
            pts = {s: [] for s in SIDES}
            for i in range(samples):
                it = gait(eng.profile, kind, i / samples, arm_style=style, fluid=fluid)
                _, info = eng.solver.solve(it)
                c = np.array(info["joints_posed"]["chest"])
                for s in SIDES:
                    pts[s].append(np.array(info["joints_posed"][f"hand_{s}"]) - c)
            im = Image.new("RGB", (460, 300), "white")
            d = ImageDraw.Draw(im)
            ext = max(float(np.ptp(np.array(v), axis=0).max()) for v in pts.values())
            sc = 190 / max(ext, 30.0)  # same scale for both hands and all panels of this gait
            for s, col, cx in (("r", (220, 90, 0), 115), ("l", (0, 110, 220), 345)):
                ctr = np.array(pts[s]).mean(0)
                q = [((x - ctr[0]) * sc + cx, (y - ctr[1]) * sc + 160) for x, y in pts[s]]
                d.line(q + [q[0]], fill=col, width=3)
                for k in range(0, samples, 8):
                    d.ellipse((q[k][0] - 3, q[k][1] - 3, q[k][0] + 3, q[k][1] + 3), fill=col)
            d.text((8, 8), f"{name} - {'fluid' if fluid else 'plain'}: hand paths relative to the chest "
                           f"(each hand centred; {ext:.0f} px span)", fill=(0, 0, 0))
            cells.append(im)
    return _rows([cells])

