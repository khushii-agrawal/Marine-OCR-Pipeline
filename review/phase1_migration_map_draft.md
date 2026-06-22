# Phase 1 Migration Map Draft

This is a planning artifact only. No extraction logic has been moved, deleted, or rewritten.

## Proposed Layout Categories

| Category | Future strategy class | Meaning |
| --- | --- | --- |
| Standard spare-parts table | `StandardPartsTableStrategy` | Rows already look like part/description/qty/material/position tables. |
| Drawing + material list | `DrawingMaterialListStrategy` | A sectional drawing or exploded view is paired with a side or nearby material list. |
| Drawing-only title block | `DrawingTitleBlockStrategy` | Pages mainly contain drawings, reference numbers, title blocks, or position callouts. |
| Index/matrix/cross-reference | `MatrixCrossReferenceStrategy` | Variant grids, index lists, NW/model matrices, code cross-reference tables. |
| Accessory/kit/equipment list | `AccessoryKitListStrategy` | Accessories, repair kits, packing lists, equipment lists, additional spares, standard spares. |

## Migration Map

| Script | Manufacturer profile | Layout category or categories | Future strategy class | Reasoning |
| --- | --- | --- | --- | --- |
| `scripts/run_test1.py` | `profiles/main_distribution_board.json` | Standard spare-parts table, drawing-only title block | `StandardPartsTableStrategy`, `DrawingTitleBlockStrategy` | Extracts columned board rows and keeps drawing-page fields from a fixed PDF range. |
| `scripts/run_test2.py` | TBD | TBD | TBD | Fixture exists under `Test/Test 2`, but no runner script is present in this checkout. |
| `scripts/run_test3.py` | `profiles/final_drawings_reference.json` | Drawing-only title block | `DrawingTitleBlockStrategy` | Does not parse OCR directly; copies known-good drawing-page rows from a reference workbook for pages 69-197. |
| `scripts/run_test4.py` | `profiles/furuno_electric_co_ltd.json` | Accessory/kit/equipment list, drawing-only title block | `AccessoryKitListStrategy`, `DrawingTitleBlockStrategy` | Script comment states equipment lists, packing lists, and drawing-page title-block extraction. |
| `scripts/run_test5.py` | `profiles/man_bw_auxiliary_engine.json` | Standard spare-parts table | `StandardPartsTableStrategy` | Uses MAN auxiliary engine table numbers, part-number prefixes, position, and name columns. |
| `scripts/run_test6.py` | `profiles/man_bw_main_engine.json` | Standard spare-parts table, drawing + material list, index/matrix/cross-reference | `StandardPartsTableStrategy`, `DrawingMaterialListStrategy`, `MatrixCrossReferenceStrategy` | Handles left catalog pages, portrait pages, and rotated list pages for MAN B&W catalogue tables. |
| `scripts/run_test7.py` | `profiles/man_bw_auxiliary_engine.json` | Standard spare-parts table | `StandardPartsTableStrategy` | OCR table rows grouped by part, position, quantity, English/German columns for auxiliary-engine spares. |
| `scripts/run_test8.py` | `profiles/man_bw_auxiliary_engine.json` | Standard spare-parts table | `StandardPartsTableStrategy` | Wrapper around `run_test7.py` for another auxiliary-engine manual/range. |
| `scripts/run_test9.py` | `profiles/obp_mooring_winches_windlass.json` | Standard spare-parts table, drawing + material list, accessory/kit/equipment list | `StandardPartsTableStrategy`, `DrawingMaterialListStrategy`, `AccessoryKitListStrategy` | Detects spare-part lists, standard/additional spares, and drawing-derived spare rows. |
| `scripts/run_test10.py` | `profiles/shanghai_hengyuan_marine_equipment.json` | Standard spare-parts table, drawing + material list | `StandardPartsTableStrategy`, `DrawingMaterialListStrategy` | Fan pages detect drawing metadata and parse fan spare-list tables. |
| `scripts/run_test11.py` | `profiles/shanghai_hengyuan_marine_equipment.json` | Standard spare-parts table, drawing + material list | `StandardPartsTableStrategy`, `DrawingMaterialListStrategy` | Wrapper around `run_test10.py` with accommodation-fan component defaults. |
| `scripts/run_test12.py` | `profiles/naniwa_pump_mfg_co_ltd.json` | Standard spare-parts table, accessory/kit/equipment list, drawing + material list | `StandardPartsTableStrategy`, `AccessoryKitListStrategy`, `DrawingMaterialListStrategy` | Parses spare-parts lists plus additional spare-parts lists, with drawing/model metadata. |
| `scripts/run_test13.py` | `profiles/naniwa_pump_mfg_co_ltd.json` | Standard spare-parts table, accessory/kit/equipment list, drawing + material list | `StandardPartsTableStrategy`, `AccessoryKitListStrategy`, `DrawingMaterialListStrategy` | Adds accessories, two-column material lists, side material lists, and robust spare-parts list handling. |
| `scripts/run_test14.py` | `profiles/obp_mooring_winches_windlass.json` | Standard spare-parts table, accessory/kit/equipment list | `StandardPartsTableStrategy`, `AccessoryKitListStrategy` | Extends OBP parsing for part/qty/description tables, Kangrim tables, and additional spare-parts pages. |
| `scripts/run_test15.py` | `profiles/hydrowega_holland_bv.json` | Standard spare-parts table, drawing + material list | `StandardPartsTableStrategy`, `DrawingMaterialListStrategy` | Contains `hydroweg_rows` and `distinta_rows`, indicating vendor table and distinct drawing/list layouts. |
| `scripts/run_test16.py` | `profiles/carrier.json` | Drawing + material list, index/matrix/cross-reference | `DrawingMaterialListStrategy`, `MatrixCrossReferenceStrategy` | Blow-up diagram pages and history/chart-style rows rather than a single normal table. |
| `scripts/run_test17.py` | `profiles/man_d2840_le.json` | Drawing + material list | `DrawingMaterialListStrategy` | Extracts bands around position anchors and derives names/details from emergency-diesel-engine drawing pages. |
| `scripts/run_test18.py` | `profiles/main_engine_accessories_multi_vendor.json` | Standard spare-parts table, drawing + material list, index/matrix/cross-reference, drawing-only title block | `StandardPartsTableStrategy`, `DrawingMaterialListStrategy`, `MatrixCrossReferenceStrategy`, `DrawingTitleBlockStrategy` | Contains Pleiger, EDUR, Boll & Kirch, matrix, rotated matrix, heat-exchanger, and drawing spare-name handlers. |
| `scripts/run_test19.py` | `profiles/bukh.json` | Standard spare-parts table, drawing + material list | `StandardPartsTableStrategy`, `DrawingMaterialListStrategy` | Life-boat BUKH pages parse position/code/name rows and short-code drawing pages. |

## Fixture Organization Proposal

No files have been moved yet. Proposed fixture roots:

| Fixture root | Candidate tests |
| --- | --- |
| `tests/fixtures/standard_parts_table/` | Test 5, Test 7, Test 8, Test 10, Test 11, Test 12, Test 19 |
| `tests/fixtures/drawing_material_list/` | Test 1, Test 6, Test 9, Test 13, Test 15, Test 16, Test 17, Test 18 |
| `tests/fixtures/drawing_title_block/` | Test 3, Test 4 |
| `tests/fixtures/matrix_cross_reference/` | Test 6, Test 16, Test 18 |
| `tests/fixtures/accessory_kit_equipment_list/` | Test 4, Test 9, Test 12, Test 13, Test 14 |

Some tests intentionally appear in more than one category because a single PDF can contain multiple layouts. In the actual fixture move, each PDF should either live under its dominant layout with metadata listing secondary layouts, or be copied into category-specific fixture manifests rather than duplicated.
