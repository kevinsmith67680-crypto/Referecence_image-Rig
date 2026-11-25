"""Phase 1: detached fragments of bone-assigned parts go to the part they touch (found on the schoolboy v2 test
image, where an ear outline and the blazer's side edge were given to the upper arms)."""

from __future__ import annotations

import numpy as np

from autorig.phase1.partition import FRAGMENT_MAX_FRAC, reassign_fragments


def _parts():
    shape = (60, 80)
    z = lambda: np.zeros(shape, bool)  # noqa: E731
    parts = {"head": z(), "upper_arm_r": z(), "torso": z()}
    parts["head"][5:20, 5:25] = True
    parts["torso"][25:55, 30:60] = True
    parts["upper_arm_r"][30:40, 62:79] = True          # main component (170 px)
    parts["upper_arm_r"][10:14, 26:28] = True          # 8 px, touching the head: an "ear outline" fragment
    parts["upper_arm_r"][45:48, 2:5] = True            # 9 px, touching nothing: stays
    return parts


def test_fragments_move_to_the_part_they_touch_and_the_partition_stays_exact():
    parts = _parts()
    union_before = np.logical_or.reduce(list(parts.values()))
    moved = reassign_fragments(parts, ["upper_arm_r", "torso"])
    assert moved == [{"from": "upper_arm_r", "to": "head", "pieces": 1, "pixels": 8, "largest_px": 8,
                      "largest_bbox_xyxy": [26, 10, 28, 14]}]
    assert parts["head"][10:14, 26:28].all() and not parts["upper_arm_r"][10:14, 26:28].any()
    assert parts["upper_arm_r"][45:48, 2:5].all(), "an isolated piece touching no part stays"
    stack = np.stack(list(parts.values())).sum(0)
    assert stack.max() == 1 and np.array_equal(stack.astype(bool), union_before)


def test_large_detached_pieces_and_direct_mask_parts_are_left_alone():
    parts = _parts()
    big = int(FRAGMENT_MAX_FRAC * 170) + 5
    parts["upper_arm_r"][20:21, 26:26 + big] = True     # a piece above the size limit, touching the head
    parts["head"][40:42, 0:2] = True                    # a detached piece of a direct-mask part
    before = {k: v.copy() for k, v in parts.items()}
    reassign_fragments(parts, ["upper_arm_r"])
    assert parts["upper_arm_r"][20, 26:26 + big].all()
    assert np.array_equal(parts["head"][40:42, 0:2], before["head"][40:42, 0:2])
