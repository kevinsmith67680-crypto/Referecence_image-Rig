# Schoolboy: first real-artwork run (snapshot)

Built on 2026-11-20 from `references/schoolboy/front_view.png` with real SAM 3 segmentation and FLUX Fill dev
inpainting. Details, costs and findings: [docs/real_artwork_test.md](../../docs/real_artwork_test.md).

* `character_project/`: Phase 1 output (`analyze ... --manual-landmarks references/schoolboy/landmarks_front.json`).
* `character_package/`: Phase 2 output (`prepare ... --provider flux-fill-dev`). Open
  `character_package/reports/visual_review.html` to inspect it.

Everything generated here is **pending review**. Nothing is approved, and nothing has been tested in Moho.
Rebuilding these folders without the git-ignored `.autorig_cache/` makes new paid Replicate calls (about $0.51).
