# Phase 2 Labeling And Threshold Notes

## Current Fixture Reality

The current `tests/fixtures/fixture_manifest.json` gives one dominant layout label per PDF. That is enough to bootstrap rendering, but it is not enough for final classifier training because several manuals contain multiple page layouts inside the same PDF.

The smoke export using one page per PDF produced:

| Class | Smoke count | Risk |
| --- | ---: | --- |
| `spare_parts_table` | 12 | Usable starting class, but still needs page-level cleanup. |
| `drawing_material_list` | 4 | Underrepresented. |
| `drawing_only` | 1 | Severely underrepresented. |
| `index_page` | 0 | Missing from manifest primary labels. |
| `repair_kit` | 2 | Severely underrepresented. |

When rendering all pages, counts will increase, but labels will still be noisy unless mixed-layout PDFs are split by page ranges.

## Recommended Additional Labeled Pages

For EfficientNet-B0 fine-tuning, use at least 150-250 clean page images per class. A better target is 300-500 per class if the layouts vary by manufacturer, scan quality, language, and rotation.

Estimated extra pages needed beyond the 19 fixture PDFs after page-level cleanup:

| Class | Recommended minimum | Likely extra needed | Where to source |
| --- | ---: | ---: | --- |
| `spare_parts_table` | 250 | 50-100 | More pages from Test 5, 6, 7, 8, 10, 11, 12, 18, 19 after excluding covers/drawings. |
| `drawing_material_list` | 250 | 100-180 | Test 13, 15, 16, 17, 18 drawing/material pages; unused pages in pump and engine manuals. |
| `drawing_only` | 200 | 150-220 | Drawing/title-block pages from Test 3, 4, 16, 17, 18, and unused drawing sections from the same manuals. |
| `index_page` | 200 | 180-240 | Matrix/cross-reference pages from Test 6, 16, 18; add index pages from manufacturer manuals not currently in the 19-test set. |
| `repair_kit` | 200 | 140-220 | Test 4 packing/equipment lists, Test 9/14 standard/additional spares, Test 12/13 additional spare parts and accessories pages. |

## Practical Labeling Workflow

1. Run `scripts/extract_training_pages.py` to create `training_data/`.
2. Review the generated PNGs class by class.
3. Move wrongly labeled pages into the correct class folder.
4. For mixed-layout manuals, prefer page-level labels over PDF-level labels.
5. Do not wire the classifier into the pipeline until every class reaches about 85% validation accuracy.

## Heuristic Fallback Rationale

The local fallback in `core/layout_classifier.py` only fires when ONNX model confidence is below `0.6`.

Reasoning for `0.6`:

- Five classes means random confidence is around `0.2`.
- A well-trained document-layout classifier should usually emit `0.8+` on familiar pages.
- `0.6` is a conservative uncertainty band: high enough to catch ambiguous/out-of-distribution pages, but low enough that the neural classifier remains the primary path.

The heuristic features are:

- `text_density`: area covered by small connected components that look like text.
- `line_density`: Hough line count normalized by megapixels.
- `image_area_ratio`: area of larger connected components, which rises on drawings, diagrams, and dense image regions.

The thresholds are intentionally cautious:

- High horizontal/vertical line density plus high text-component density suggests `index_page`.
- High line density plus meaningful text suggests `drawing_material_list`.
- High line density with little text suggests `drawing_only`.
- Dense small text components with moderate grid lines suggests `spare_parts_table`.
- Text-heavy pages with fewer grid/drawing lines fall back to `repair_kit`.

These rules are not meant to replace training. They are a safety net for low-confidence pages and should be recalibrated after the first labeled validation report.
