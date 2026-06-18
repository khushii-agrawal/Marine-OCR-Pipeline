# OCR Extraction Rules

When page numbers are provided for a new test PDF, do not assume one existing parser covers every page.

## Required Workflow

1. Inspect the requested pages first.
   - Render or OCR representative pages from the provided page list.
   - Check at least the first page of each page range.
   - If a page range is large, sample the start, middle, and end of that range.

2. Identify every layout type present.
   - Examples:
     - `SPARE PARTS LIST`
     - `ACCESSORIES FOR EACH 1 PUMP`
     - two-column `PART / NAME OF PART / MATERIAL / Q'TY`
     - side material list beside sectional drawing
     - drawing-only/specification page
     - index/matrix/cross-reference page
   - Do not mark a page as non-extractable until it has been visually/OCR checked.

3. Write extraction logic for each extractable layout.
   - Keep layout-specific logic isolated in the relevant test runner when possible.
   - Reuse existing shared helpers only after confirming the layout matches.
   - If a page has spare names, part numbers, material, quantity, accessories, or remarks, treat it as extractable.

4. Run targeted checks before the full run.
   - Test at least one page for every detected layout.
   - Print/sample extracted rows and verify:
     - subcomponent
     - model
     - drawing number
     - name of spare
     - part number
     - material
     - quantity/details
     - remarks, if present

5. Run the full requested page list only after targeted checks pass.
   - Process exactly the user-provided page numbers/ranges.
   - De-duplicate page numbers.
   - Keep page numbers in the output.

6. Audit the final workbook.
   - Report total rows.
   - Report row count by page.
   - Report requested pages that produced zero rows.
   - For zero-row pages, state whether they are drawing-only or need another parser.
   - Check blank counts for important fields.

## Important Rule

If the extracted row count seems too low, stop and re-inspect the requested pages before saying the output is complete.

## Current Lesson From Test 13

Test 13 had multiple valid extractable layouts:

- Page 4 style: `ACCESSORIES FOR EACH 1 PUMP`
- Page 5 style: two-column material/parts table
- Page 13 style: `SPARE PARTS LIST`
- Page 40 style: left-side material list beside sectional drawing

The first attempt extracted too few rows because only the spare-parts-list layout was handled. Future runs must identify all layouts before final extraction.
