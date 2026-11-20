# Live animation readiness: synthetic_fixture

Classification of whether the **existing artwork** supports each live capability.

| Capability | Status | Why | Artwork required |
|---|---|---|---|
| blinking | **REQUIRES_NEW_ARTWORK** | Both eyes are separable, but no closed-eye artwork exists in a single open-eyed reference. | closed_eye_r, closed_eye_l |
| eyebrow_movement | **REQUIRES_RECONSTRUCTION** | Brows are separable; moving them reveals skin that was never painted (must be reconstructed, not invented here). | skin_under_brow_r, skin_under_brow_l |
| mouth_opening | **REQUIRES_NEW_ARTWORK** | The reference mouth is a closed line: no lips or interior to open. | open_mouth_with_interior, skin_under_mouth |
| lip_sync | **REQUIRES_NEW_ARTWORK** | Lip-sync needs a set of phoneme mouth shapes; only one exists. | phoneme_mouth_set |
| eye_direction | **REQUIRES_RECONSTRUCTION** | Pupils are separable inside the eyes, but the sclera under them is hidden and must be reconstructed before pupils can move. | sclera_fill_r, sclera_fill_l |
| head_tilting | **REQUIRES_RECONSTRUCTION** | Head layer and neck pivot exist; tilting exposes hidden neck/collar pixels under the chin that must be reconstructed. | neck_extension_under_chin, back_of_hair_and_hidden_head_outline |
| head_turning | **REQUIRES_NEW_ARTWORK** | Turning needs three-quarter/profile views or new turn artwork; a single front view cannot provide them. | three_quarter_and_profile_head_views |
| facial_expressions | **REQUIRES_NEW_ARTWORK** | Only the neutral expression exists; expressions need new brow/eye/mouth artwork. | expression_variants (brows, eyes, mouth) |

Summary: {'READY': 0, 'REQUIRES_RECONSTRUCTION': 3, 'REQUIRES_NEW_ARTWORK': 5, 'UNSUPPORTED': 0, 'NEEDS_REVIEW': 0}

Policy: READY only when all required artwork already exists in the reference; nothing is invented.
