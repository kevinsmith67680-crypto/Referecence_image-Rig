# AutoRig schoolboy hand pose library — concept assets

Contains 16 **separate RGBA PNG** assets (eight poses for each side), a reference sheet, and a machine-readable manifest.

**Important:** These are AI-generated matching-style concepts, **not pixel-perfect artwork traced from the character**. Each is flagged `NEEDS_REVIEW`. All wrist anchors are estimates and must be aligned to the true character wrist in AutoRig before enabling switches. No assumptions about mirrored anatomy or pose orientation should be made.

Pose names: relaxed, open_palm, fist, point, thumbs_up, grip, palm_down, cupped.

Sprites are on a consistent 400 × 320 transparent canvas. They include a navy sleeve and white cuff for visual testing; when integrating, avoid doubling the character's existing cuff and forearm artwork. The importer should let the user mask/remove redundant sleeve pixels or use a hand-only derivative after approval.

The editor should provide per-variant pivot correction, scale/rotation matching, preview, missing-artwork reporting, and original-asset restoration.
