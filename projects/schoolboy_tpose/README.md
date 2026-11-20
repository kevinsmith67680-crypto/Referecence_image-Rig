# Schoolboy T-pose: second real-artwork run (snapshot)

Built on 2026-11-20 from `references/schoolboy/tpose_front.jpg` (the user's T-pose reference) with real SAM 3
segmentation and FLUX Fill dev inpainting, one candidate per region. No manual landmarks were used. Details:
[docs/real_artwork_test.md](../../docs/real_artwork_test.md) (Run 2).

* `character_project/`: Phase 1 output.
* `character_package/`: Phase 2 output. Open `character_package/reports/visual_review.html` to inspect it.
  `reports/pose_contact_sheet.png` shows the nine test poses.

Everything generated is **pending review**. Nothing is approved, and nothing has been tested in Moho.
Rebuilding without the git-ignored `.autorig_cache/` makes new paid Replicate calls (about $0.41).
