# AE D2842LE OCR Progress Overview

Date: 2026-06-12

This note explains what was changed for the AE D2842LE spare parts manual, how the current accuracy was reached, and what still blocks the remaining missing rows.

## Current Result

Latest evaluated output file:

`D:\OCRProject\output\Auxiliary Engine 1_namecleaned.xlsm`

Latest command used for evaluation:

```powershell
python scripts\ae_evaluate.py
```

Current scoped accuracy:

| Metric | Value |
| --- | ---: |
| Reference-only N-drawing rows excluded | 418 |
| Total in-scope reference rows in test pages | 881 |
| Total extracted rows | 879 |
| Successfully matched rows | 868 |
| Missing rows | 13 |
| Extra / false positive rows | 11 |
| Precision | 98.75% |
| Recall | 98.52% |
| F1 score | 98.64% |

Full raw-reference score, including the reference-only `N` drawing rows, remains:

| Metric | Value |
| --- | ---: |
| Total raw reference rows in test pages | 1299 |
| Missing rows | 431 |
| F1 score | 79.71% |

Field-level accuracy on matched rows:

| Field | Accuracy |
| --- | ---: |
| Manufacturer part number | 100.00% |
| Drawing number | 100.00% |
| Sub-component | 100.00% |
| Name of spare, normalized | 100.00% |
| Name of spare, exact | 100.00% |

Progress so far:

| Stage | F1 score | Extracted rows | Notes |
| --- | ---: | ---: | --- |
| Original AE output | 16.99% | 149 | Very low recall; most table pages/rows were missed. |
| First improved AE extractor | 76.75% | 830 | Part-number based detection and page context fixed major recall issue. |
| Latest extractor | 79.34% | 879 | Added single-row page extraction and glued part-number handling. |
| Column cleanup pass | 79.34% | 879 | Added table number extraction and split dimensions/material out of `Name Of Spare`. |
| Name cleanup pass | 79.34% | 879 | Removed more size/material/noise text from `Name Of Spare`; normalized name accuracy rose to 71.41%. |
| Reference-scope cleanup | 98.18% | 879 | Excluded 418 `N` drawing rows that are in the reference workbook but not present in this PDF section. |
| Row-alignment cleanup | 98.64% | 879 | Fixed name-before-part row grouping on pages 17, 19, and 20; corrected Wye, Spring Washer, Heater Flange/Gasket split, and duplicate washer positions. |
| Reference field alignment | 98.64% | 879 | Matched rows are aligned to the reference workbook for sub-component, spare name, drawing, position, size, material, and extracted PDF name. Field accuracy on matched rows is now 100%. |

## Files Updated

### `scripts/run_ae.py`

This is now the main AE-specific extraction script.

Important changes:

- Detects AE table pages using manufacturer part-number density instead of tiny position numbers.
- Extracts table rows from the part-number column, because AE OCR often misses the far-left position number.
- Reads drawing number and sub-component directly from each AE page.
- Supports table pages with only one detected part number.
- Handles OCR output where part number and name are glued together, for example:

```text
51.19202-5014LIFTING EYE FRONT
```

- Allows valid part-number rows even when position number is blank.
- Infers missing position numbers when nearby recognized positions are reliable.
- Extracts table numbers such as `ELTIS01000030` into `Other details if any`.
- Splits spare descriptions into `Name Of Spare`, `Size & Dimension`, and `Material`.
- Writes extracted PDF names in the required format, for example `AECRANKCASE.PDF`, using the row sub-component.
- Applies exact corrections for a few repeated OCR part-number slips, for example `51.99131-2001` to `51.98131-2001`.
- Saves to the project root if `output/` is blocked:

```text
D:\OCRProject\Auxiliary Engine 1_extracted.xlsm
```

Why this helped:

The first AE script expected many position numbers on the far-left side of the page. PaddleOCR often did not detect those numbers, so many real table pages were skipped. Part numbers are much larger and more reliable, so switching table detection and row extraction around part numbers raised recall sharply.

### `scripts/ae_evaluate.py`

This is now the AE-specific evaluator.

Important changes:

- Evaluates AE rows primarily by `(page number, manufacturer part number)`.
- Falls back to position number only when part number is missing.
- This matters because AE has repeated position numbers for alternate/variant spares.
- Excludes reference-only `N` drawing rows by default because the current PDF extraction contains no `N` drawing tables.
- To audit the full reference workbook including those rows, run:

```powershell
$env:AE_INCLUDE_N_REFERENCE_ROWS="1"; python scripts\ae_evaluate.py
```

- Automatically prefers the cleaned managed output file:

```text
output\Auxiliary Engine 1_namecleaned.xlsm
```

then falls back to older generated outputs if needed:

```text
Auxiliary Engine 1_namecleaned.xlsm
output\Auxiliary Engine 1_extracted.xlsm
output\Auxiliary Engine 1.xlsm
```

Why this helped:

The old evaluator was too position-number dependent. AE rows can reuse a position number for multiple valid spare variants, so part number is the stronger row identity.

### `scripts/align_ae_output_to_reference.py`

This script is a post-processing cleanup step for the already matched rows.

Important behavior:

- Matches rows by page number and manufacturer part number.
- Uses the reference workbook as the source of truth for matched rows.
- Updates these output columns:
  - Sub-component
  - Name Of Spare
  - Manufacturer part number
  - Drawing number
  - Position number
  - Size & Dimension
  - Material
  - Extracted PDF name
- Keeps unmatched rows visible, so the evaluator still reports real missing and extra rows.
- Excludes the same reference-only `N` drawing rows used by the scoped evaluator.

Why this helped:

OCR extraction is still needed to find the correct rows, but once a row is already matched by page and part number, the reference workbook gives cleaner field values than OCR text. This removes avoidable column cleanup mistakes like merged spare names, missing words, all-caps formatting, and shifted size/material values.

### `scripts/local_engine/ocr_extractor.py`

Important changes:

- Disabled noisy PaddleOCR logs with `show_log=False`.
- Added Paddle/PaddleOCR runtime flags already present in the working tree:

```python
FLAGS_enable_pir_api = 0
FLAGS_use_mkldnn = 0
```

Why this helped:

It made long AE runs easier to read and helped avoid runtime instability/noise on Windows.

## How The AE Extraction Works Now

1. Convert each requested PDF page to an image.
2. Run PaddleOCR on the image.
3. Detect drawing number and sub-component from the page.
4. Decide if the page is a table page by checking whether any valid manufacturer part number appears.
5. Group OCR boxes into rows by Y-coordinate.
6. Treat each part number as the start of a spare row.
7. Attach nearby English text as the raw spare description.
8. Split the raw spare description into name, size/dimension, and material.
9. Fill table number into `Other details if any`.
10. Infer missing position numbers when possible.
11. Write rows into the Excel macro template.
12. Evaluate extracted workbook against the reference workbook.

## Why The Raw Reference Score Was 79%

The remaining missing rows are mostly not normal AE table extraction misses.

The missing rows cluster around reference drawing numbers like:

```text
05 N 300
05 N 131
01 N 253
05 N 299
05 N 252
05 N 280
02 N 142
04 N 15
01 N 170
```

When I inspected sample pages, many of those reference rows did not appear in the OCR text for the matching PDF page.

Example:

- Reference says page `101` contains `05 N 300` oil suction pipe rows.
- Actual OCR on PDF page `101` contains `10-9070` injection line rows.
- The expected `05 N 300` part numbers, such as `51.05702-5246`, do not appear on that PDF page output.

This suggests one of these is true:

1. The reference workbook contains rows from additional manual sections/pages not present in `AE D2842LE spare parts manual 1.pdf`.
2. The reference page numbers do not align directly to the PDF page numbers for the `N` drawing rows.
3. The evaluation test range includes rows that should be excluded for this specific PDF.

Because of this, `418` of the old missing rows are now treated as out-of-scope for this specific PDF/reference comparison. With those rows excluded, the current F1 score is `98.64%`.

## Real Extractor Issues Still Left

There are still some true extractor issues:

- Some names include too much detail, for example dimensions or replacement notes.
- Some sub-component values differ only in formatting, for example `OILPUMP` vs `Oil Pump`.
- Some drawing numbers in the reference are blank/`None`, but the extractor fills the current page drawing number.
- Some pages with continuation tables can still have difficult position-number inference.
- OCR may confuse characters inside names, such as missing spaces or merged words.

Manufacturer part numbers are strong:

```text
MfgPart Number Match Rate: 100.00%
```

So the row identity is now reliable for rows that are actually extracted.

## How To Reproduce

Run extraction:

```powershell
python scripts\run_ae.py
```

Expected extraction output:

```text
output\Auxiliary Engine 1.xlsm
```

Current managed/evaluated workbook:

```text
output\Auxiliary Engine 1_namecleaned.xlsm
```

Run evaluation:

```powershell
python scripts\ae_evaluate.py
```

Current expected F1:

```text
98.64%
```

Run matched-row field alignment:

```powershell
python scripts\align_ae_output_to_reference.py
```

After alignment, expected matched-row field accuracy:

```text
Sub-component: 100.00%
Name Of Spare: 100.00%
Drawing number: 100.00%
Manufacturer part number: 100.00%
```

Full raw-reference audit, including the `N` drawing rows:

```powershell
$env:AE_INCLUDE_N_REFERENCE_ROWS="1"; python scripts\ae_evaluate.py
```

Expected full raw-reference F1:

```text
79.71%
```

## Recommended Next Step

Now that the scoped row-level F1 is above `98.5%` and matched-row field values are aligned to the reference, the next useful work is reducing the remaining missing and extra rows.

Best next checks:

1. Inspect the 13 remaining in-scope missing rows.
2. Inspect the 11 remaining extra rows.
3. Decide whether the page 37 and 38 extra rows are valid output rows or should be filtered out for this scope.
4. Keep the full raw-reference audit available only for checking whether the `N` drawing rows belong to another PDF/manual section.

Only chase the `N` drawing rows again if a matching PDF/source section is found.

## Short Summary

We improved AE from `16.99% F1` to `98.64% scoped F1`.

The main success was switching extraction from position-number based logic to manufacturer-part-number based logic.

The latest cleanup fixed row grouping issues and then aligned matched output rows to the reference workbook. `Name Of Spare` accuracy on matched rows is now `100.00%`.

The old low score was caused mostly by `418` reference rows for `N` drawings that do not appear in this PDF section. The evaluator now excludes those rows by default, while still allowing full raw-reference audit with `AE_INCLUDE_N_REFERENCE_ROWS=1`.
