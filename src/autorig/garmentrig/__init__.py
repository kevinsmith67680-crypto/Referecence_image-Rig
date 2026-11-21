"""Phase 3.3: production-quality garment rigging.

The outfit is described as semantic garment parts, each with a deformation class: RIGID, LIMITED_DEFORMATION or
FLEXIBLE (`structure`). Pivots are aligned to the garment attachments and validated (`structure.align_pivots`).
Per-layer weight meshes hold normalised, restricted, lockable bone weights that users can edit (`weights`). The
structured deformer (`deform`) draws rigid segments over weighted joint patches. It also draws generated pixels
beneath the layer that hid them in the source. The correction controller covers angle-range corrective artwork
(`corrections`), the evaluator gives per-garment verdicts (`evaluate`), and `evidence` writes the renders.
"""

from .structure import FLEXIBLE, LIMITED, RIGID, GarmentStructure, GarmentStructureAnalyzer, align_pivots

__all__ = ["FLEXIBLE", "LIMITED", "RIGID", "GarmentStructure", "GarmentStructureAnalyzer", "align_pivots"]
