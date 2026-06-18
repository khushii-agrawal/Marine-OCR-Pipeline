"""Application entry point for running existing PDF extraction pipelines."""

from __future__ import annotations

import contextlib
import importlib
import io
import logging
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Callable

import fitz
import openpyxl


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
LOCAL_ENGINE_DIR = SCRIPTS_DIR / "local_engine"
TEMPLATE_PATH = PROJECT_ROOT / "template" / "Spares_Capture_Template_Ver12 2.xlsm"
MAX_COLS = 21

for import_path in (SCRIPTS_DIR, LOCAL_ENGINE_DIR):
    path_text = str(import_path)
    if path_text not in sys.path:
        sys.path.insert(0, path_text)

CancelCheck = Callable[[], bool]
ProgressCallback = Callable[[str, int, int, int], None]


class CancelledExtraction(Exception):
    """Raised when the user cancels extraction before workbook generation."""


class NoRowsExtracted(Exception):
    """Raised when a selected PDF/page range produced no extractable rows."""


class UnsupportedPipeline(Exception):
    """Raised when no matching production pipeline is available for a PDF."""


@dataclass(frozen=True)
class ProcessingSummary:
    """Summary metrics for a completed extraction."""

    total_pages_processed: int
    total_rows_extracted: int
    processing_time_seconds: float


@dataclass(frozen=True)
class ExtractionResult:
    """Result metadata for a completed extraction run."""

    output_path: Path
    pipeline_name: str
    log_text: str
    summary: ProcessingSummary


@dataclass(frozen=True)
class PipelineRoute:
    """A filename/text route to an existing extraction runner."""

    name: str
    module_name: str
    filename_patterns: tuple[str, ...]
    text_markers: tuple[str, ...]
    runner: str


@dataclass(frozen=True)
class RunnerResult:
    """Internal result returned by a page-aware runner."""

    output_path: Path
    pages_processed: int
    rows_extracted: int


def get_pdf_page_count(pdf_path: Path) -> int:
    """Return the total number of pages in a PDF."""

    with fitz.open(pdf_path) as document:
        return len(document)


def parse_page_ranges(page_ranges: str, total_pages: int) -> list[int]:
    """Parse a user page range string into a sorted unique page list."""

    value = page_ranges.strip()
    if not value:
        raise ValueError("Enter at least one page or page range.")

    pages: set[int] = set()
    token_pattern = re.compile(r"^\d+(?:-\d+)?$")
    for raw_token in value.split(","):
        token = raw_token.strip()
        if not token or not token_pattern.fullmatch(token):
            raise ValueError("Use only page numbers and ranges like 12-14,20-25.")

        if "-" in token:
            start_text, end_text = token.split("-", 1)
            start_page = int(start_text)
            end_page = int(end_text)
            if start_page > end_page:
                raise ValueError(f"Invalid range {token}: start page must be before end page.")
            selected = range(start_page, end_page + 1)
        else:
            selected = [int(token)]

        for page_no in selected:
            if page_no < 1:
                raise ValueError("Page numbers must be 1 or greater.")
            if page_no > total_pages:
                raise ValueError(f"Page {page_no} is greater than the PDF total of {total_pages} pages.")
            pages.add(page_no)

    return sorted(pages)


def _safe_output_name(pdf_path: Path) -> str:
    """Build a stable macro-enabled workbook name from the uploaded PDF."""

    stem = re.sub(r"[^A-Za-z0-9._ -]+", "_", pdf_path.stem).strip(" ._")
    return f"{stem or 'extracted'}_extracted.xlsm"


def _read_pdf_sample_text(pdf_path: Path, max_pages: int = 3) -> str:
    """Read a small text sample for lightweight format routing."""

    try:
        with fitz.open(pdf_path) as document:
            pages = min(max_pages, len(document))
            sample_text = "\n".join(document[index].get_text("text") for index in range(pages))
        if sample_text.strip():
            return sample_text
    except Exception:
        logging.getLogger(__name__).exception("Could not read PDF sample text for routing")
        return ""

    try:
        from ocr_extractor import OCRExtractor
        from pdf_converter import pdf_page_to_image

        extractor = OCRExtractor()
        ocr_lines = []
        with fitz.open(pdf_path) as document:
            pages = min(2, len(document))
        for page_index in range(pages):
            image = pdf_page_to_image(str(pdf_path), page_index, dpi=120)
            for _, (text, _) in extractor.extract_text(image):
                text = re.sub(r"\s+", " ", str(text or "").strip())
                if text:
                    ocr_lines.append(text)
        return "\n".join(ocr_lines)
    except Exception:
        logging.getLogger(__name__).exception("Could not OCR PDF sample text for routing")
        return ""


def _matches_route(route: PipelineRoute, pdf_path: Path, sample_text: str) -> bool:
    """Return True when an uploaded PDF appears to match a known runner."""

    haystack = f"{pdf_path.name}\n{sample_text}".lower()
    filename = pdf_path.name.lower()
    if any(pattern.lower() in filename for pattern in route.filename_patterns):
        return True
    return all(marker.lower() in haystack for marker in route.text_markers)


def _import_module(module_name: str) -> ModuleType:
    """Import or reload a runner so previous app runs do not leak globals."""

    if module_name in sys.modules:
        return importlib.reload(sys.modules[module_name])
    return importlib.import_module(module_name)


def _set_attr_if_present(module: ModuleType, name: str, value: object) -> None:
    """Set a module global only when that runner already defines it."""

    if hasattr(module, name):
        setattr(module, name, value)


def _configure_module(module: ModuleType, pdf_path: Path, output_path: Path) -> None:
    """Supply uploaded input and requested output paths to an existing runner."""

    _set_attr_if_present(module, "PDF_PATH", pdf_path)
    _set_attr_if_present(module, "OUTPUT_PATH", output_path)
    _set_attr_if_present(module, "OUTPUT", output_path)
    _set_attr_if_present(module, "MANUAL_PDF_NAME", pdf_path.name)
    _set_attr_if_present(module, "MANUAL_PDF", pdf_path.name)


def _cancel_if_requested(cancel_check: CancelCheck | None) -> None:
    """Raise cancellation when the UI asks processing to stop."""

    if cancel_check and cancel_check():
        raise CancelledExtraction("Extraction cancelled by user.")


def _emit(
    progress_callback: ProgressCallback | None,
    stage: str,
    processed: int,
    total: int,
    rows: int,
) -> None:
    """Send progress information to the UI when a callback is supplied."""

    if progress_callback:
        progress_callback(stage, processed, total, rows)


def _write_template_rows(rows: list[list[object]], output_path: Path) -> Path:
    """Write prepared template rows to the macro workbook template."""

    workbook = openpyxl.load_workbook(TEMPLATE_PATH, keep_vba=True)
    worksheet = workbook.active
    try:
        for row_idx, row in enumerate(rows, start=3):
            for col_idx, value in enumerate(row[:MAX_COLS], start=1):
                worksheet.cell(row_idx, col_idx).value = value
        output_path.parent.mkdir(parents=True, exist_ok=True)
        workbook.save(output_path)
    finally:
        workbook.close()
    return output_path


def _route_pages(pages_to_process: list[int], page_count: int) -> list[int]:
    """Keep page selections valid for the current PDF."""

    return [page_no for page_no in pages_to_process if 1 <= page_no <= page_count]


def _run_test1(
    pdf_path: Path,
    output_path: Path,
    pages_to_process: list[int],
    cancel_check: CancelCheck | None,
    progress_callback: ProgressCallback | None,
) -> RunnerResult:
    """Run the existing Test 1 extraction functions on selected pages."""

    module = _import_module("run_test1")
    _configure_module(module, pdf_path, output_path)
    records = []
    processed = 0
    document = fitz.open(pdf_path)
    try:
        pages = _route_pages(pages_to_process, len(document))
        for page_no in pages:
            _cancel_if_requested(cancel_check)
            records.extend(module.page_rows(document[page_no - 1], page_no))
            processed += 1
            _emit(progress_callback, "Processing pages", processed, len(pages), len(records))
    finally:
        document.close()

    _cancel_if_requested(cancel_check)
    rows = [module.to_template_row(record) for record in module.consolidate(records)]
    _emit(progress_callback, "Writing Excel", processed, len(pages), len(rows))
    module.write_workbook(rows)
    return RunnerResult(output_path, processed, len(rows))


def _run_test4(
    pdf_path: Path,
    output_path: Path,
    pages_to_process: list[int],
    cancel_check: CancelCheck | None,
    progress_callback: ProgressCallback | None,
) -> RunnerResult:
    """Run the existing Test 4 page extractors on selected pages."""

    module = _import_module("run_test4")
    _configure_module(module, pdf_path, output_path)
    selected_indices = {page_no - 1 for page_no in pages_to_process}
    all_rows: list[list[object]] = []
    processed = 0
    total_selected = len(pages_to_process)

    document = fitz.open(pdf_path)
    try:
        for page_index in [idx for idx in module.EQUIP_PAGES if idx in selected_indices and idx < len(document)]:
            _cancel_if_requested(cancel_check)
            records = module.extract_equipment_page(document[page_index], page_index + 1)
            all_rows.extend(module.equipment_to_rows(records))
            processed += 1
            _emit(progress_callback, "Processing pages", processed, total_selected, len(all_rows))
    finally:
        document.close()

    extractor = module.OCRExtractor()
    for page_index in [idx for idx in module.PACKING_PAGES if idx in selected_indices]:
        _cancel_if_requested(cancel_check)
        all_rows.extend(module.extract_packing_pages(extractor, pdf_path, [page_index]))
        processed += 1
        _emit(progress_callback, "Processing pages", processed, total_selected, len(all_rows))

    for page_index in [idx for idx in module.DRAWING_PAGES if idx in selected_indices]:
        _cancel_if_requested(cancel_check)
        all_rows.extend(module.extract_drawing_pages(extractor, pdf_path, [page_index]))
        processed += 1
        _emit(progress_callback, "Processing pages", processed, total_selected, len(all_rows))

    seen = set()
    deduped = []
    for row in all_rows:
        key = (row[12], row[5])
        if key not in seen:
            seen.add(key)
            deduped.append(row)

    _cancel_if_requested(cancel_check)
    _emit(progress_callback, "Writing Excel", processed, total_selected, len(deduped))
    _write_template_rows(deduped, output_path)
    return RunnerResult(output_path, processed, len(deduped))


def _run_test5(
    pdf_path: Path,
    output_path: Path,
    pages_to_process: list[int],
    cancel_check: CancelCheck | None,
    progress_callback: ProgressCallback | None,
) -> RunnerResult:
    """Run the existing Test 5 extraction functions on selected pages."""

    module = _import_module("run_test5")
    _configure_module(module, pdf_path, output_path)
    records = []
    context = {"table_no": "", "sub_component": ""}
    processed = 0
    document = fitz.open(pdf_path)
    try:
        pages = _route_pages(pages_to_process, len(document))
        for page_no in pages:
            _cancel_if_requested(cancel_check)
            records.extend(module.extract_rows_from_page(document[page_no - 1], page_no, context))
            processed += 1
            _emit(progress_callback, "Processing pages", processed, len(pages), len(records))
    finally:
        document.close()

    _cancel_if_requested(cancel_check)
    rows = [module.to_template_row(record) for record in records]
    _emit(progress_callback, "Writing Excel", processed, len(pages), len(rows))
    saved_path = module.ae.write_to_excel(rows, str(module.TEMPLATE_PATH), str(output_path))
    return RunnerResult(Path(saved_path), processed, len(rows))


def _run_test6(
    pdf_path: Path,
    output_path: Path,
    pages_to_process: list[int],
    cancel_check: CancelCheck | None,
    progress_callback: ProgressCallback | None,
) -> RunnerResult:
    """Run the existing Test 6 extraction functions on selected pages."""

    module = _import_module("run_test6")
    _configure_module(module, pdf_path, output_path)
    records = []
    last_subcomponent = ""
    last_table_no = ""
    processed = 0
    document = fitz.open(pdf_path)
    try:
        pages = _route_pages(pages_to_process, len(document))
        for page_no in pages:
            _cancel_if_requested(cancel_check)
            page = document[page_no - 1]
            text = page.get_text("text")
            items = module.word_items(page)
            rotated_page = module.is_rotated_list_page(text, items)
            subcomponent, table_no = module.page_context(items, text, last_subcomponent, last_table_no)
            if rotated_page:
                footer_subcomponent, footer_table_no = module.footer_context(text)
                subcomponent = footer_subcomponent or subcomponent
                table_no = footer_table_no or table_no
            subcomponent = module.clean_subcomponent(subcomponent, table_no)
            last_subcomponent = subcomponent or last_subcomponent
            last_table_no = table_no or last_table_no

            if rotated_page:
                page_records = module.extract_rotated_list_page(text, page_no, last_subcomponent, last_table_no)
            elif module.is_left_catalog_page(items):
                page_records = module.extract_left_catalog_page(items, page_no, last_subcomponent, last_table_no)
            else:
                page_records = module.extract_portrait_page(items, page_no, last_subcomponent, last_table_no)
            records.extend(page_records)
            processed += 1
            _emit(progress_callback, "Processing pages", processed, len(pages), len(records))
    finally:
        document.close()

    _cancel_if_requested(cancel_check)
    rows = [module.to_template_row(record) for record in records]
    _emit(progress_callback, "Writing Excel", processed, len(pages), len(rows))
    saved_path = module.ae.write_to_excel(rows, str(module.TEMPLATE_PATH), str(output_path))
    return RunnerResult(Path(saved_path), processed, len(rows))


def _run_test7_like(
    runner_module: str,
    pdf_path: Path,
    output_path: Path,
    pages_to_process: list[int],
    cancel_check: CancelCheck | None,
    progress_callback: ProgressCallback | None,
) -> RunnerResult:
    """Run the existing Test 7/Test 8 extraction functions on selected pages."""

    module = _import_module(runner_module)
    base = module.base if hasattr(module, "base") else module
    _configure_module(base, pdf_path, output_path)
    extractor = base.OCRExtractor()
    context_subcomponent = ""
    context_table_no = ""
    records = []
    processed = 0
    total_pages = get_pdf_page_count(pdf_path)
    pages = _route_pages(pages_to_process, total_pages)

    for page_no in pages:
        _cancel_if_requested(cancel_check)
        image = base.pdf_page_to_image(str(pdf_path), page_no - 1, dpi=200)
        items = base.ocr_items(image, extractor)
        page_rows = base.group_rows(items)
        subcomponent, table_no = base.page_context(page_rows, context_subcomponent, context_table_no)
        context_subcomponent = subcomponent
        context_table_no = table_no
        records.extend(base.extract_page_rows(page_rows, page_no, subcomponent, table_no))
        processed += 1
        _emit(progress_callback, "Processing pages", processed, len(pages), len(records))

    _cancel_if_requested(cancel_check)
    rows = [base.to_template_row(record) for record in records]
    _emit(progress_callback, "Writing Excel", processed, len(pages), len(rows))
    saved_path = base.ae.write_to_excel(rows, str(base.TEMPLATE_PATH), str(output_path))
    return RunnerResult(Path(saved_path), processed, len(rows))


def _run_test9(
    pdf_path: Path,
    output_path: Path,
    pages_to_process: list[int],
    cancel_check: CancelCheck | None,
    progress_callback: ProgressCallback | None,
) -> RunnerResult:
    """Run the existing Test 9 extraction functions on selected pages."""

    module = _import_module("run_test9")
    _configure_module(module, pdf_path, output_path)
    extractor = module.OCRExtractor()
    records = []
    processed = 0
    document = fitz.open(pdf_path)
    try:
        pages = _route_pages(pages_to_process, len(document))
        for page_no in pages:
            _cancel_if_requested(cancel_check)
            page = document[page_no - 1]
            items = module.embedded_items(page)
            if len(items) < 8:
                items = module.ocr_items(pdf_path, page_no - 1, extractor)
            records.extend(module.extract_page(items, page_no))
            processed += 1
            _emit(progress_callback, "Processing pages", processed, len(pages), len(records))
    finally:
        document.close()

    _cancel_if_requested(cancel_check)
    rows = [module.to_template_row(record) for record in records]
    _emit(progress_callback, "Writing Excel", processed, len(pages), len(rows))
    module.write_workbook(rows)
    return RunnerResult(output_path, processed, len(rows))


def _run_test10_like(
    runner_module: str,
    pdf_path: Path,
    output_path: Path,
    pages_to_process: list[int],
    cancel_check: CancelCheck | None,
    progress_callback: ProgressCallback | None,
) -> RunnerResult:
    """Run the existing fan extraction functions on selected pages."""

    module = _import_module(runner_module)
    base = module.base if hasattr(module, "base") else module
    _configure_module(base, pdf_path, output_path)
    extractor = base.OCRExtractor()
    records = []
    processed = 0
    total_pages = get_pdf_page_count(pdf_path)
    pages = _route_pages(pages_to_process, total_pages)

    for page_no in pages:
        _cancel_if_requested(cancel_check)
        items = base.ocr_rotated_items(pdf_path, page_no - 1, extractor)
        records.extend(base.extract_page(items, page_no))
        processed += 1
        _emit(progress_callback, "Processing pages", processed, len(pages), len(records))

    _cancel_if_requested(cancel_check)
    rows = [base.to_template_row(record) for record in records]
    _emit(progress_callback, "Writing Excel", processed, len(pages), len(rows))
    saved_path = base.write_workbook(rows)
    return RunnerResult(Path(saved_path or output_path), processed, len(rows))


def _run_test12(
    pdf_path: Path,
    output_path: Path,
    pages_to_process: list[int],
    cancel_check: CancelCheck | None,
    progress_callback: ProgressCallback | None,
) -> RunnerResult:
    """Run the existing Test 12 extraction functions on selected pages."""

    module = _import_module("run_test12")
    _configure_module(module, pdf_path, output_path)
    extractor = module.OCRExtractor()
    records = []
    processed = 0
    document = fitz.open(pdf_path)
    try:
        pages = _route_pages(pages_to_process, len(document))
        for page_no in pages:
            _cancel_if_requested(cancel_check)
            items = module.ocr_items(pdf_path, page_no - 1, extractor)
            records.extend(module.spare_list_rows(items, page_no))
            processed += 1
            _emit(progress_callback, "Processing pages", processed, len(pages), len(records))
    finally:
        document.close()

    _cancel_if_requested(cancel_check)
    rows = [module.to_template_row(record) for record in records]
    _emit(progress_callback, "Writing Excel", processed, len(pages), len(rows))
    saved_path = module.write_workbook(rows)
    return RunnerResult(Path(saved_path), processed, len(rows))


def _run_test13(
    pdf_path: Path,
    output_path: Path,
    pages_to_process: list[int],
    cancel_check: CancelCheck | None,
    progress_callback: ProgressCallback | None,
) -> RunnerResult:
    """Run the existing Test 13 extraction functions on selected pages."""

    module = _import_module("run_test13")
    base = module.base
    _configure_module(base, pdf_path, output_path)
    extractor = base.OCRExtractor()
    records = []
    context = {"sub_component": "", "model": "", "drawing": ""}
    processed = 0
    document = fitz.open(pdf_path)
    try:
        pages = _route_pages(pages_to_process, len(document))
        for page_no in pages:
            _cancel_if_requested(cancel_check)
            items = base.ocr_items(pdf_path, page_no - 1, extractor)
            page_records, context = module.extract_page(items, page_no, context)
            records.extend(page_records)
            processed += 1
            _emit(progress_callback, "Processing pages", processed, len(pages), len(records))
    finally:
        document.close()

    _cancel_if_requested(cancel_check)
    rows = [base.to_template_row(record) for record in records]
    _emit(progress_callback, "Writing Excel", processed, len(pages), len(rows))
    saved_path = base.write_workbook(rows)
    return RunnerResult(Path(saved_path), processed, len(rows))


def _run_test14(
    pdf_path: Path,
    output_path: Path,
    pages_to_process: list[int],
    cancel_check: CancelCheck | None,
    progress_callback: ProgressCallback | None,
) -> RunnerResult:
    """Run the existing Test 14 extraction functions on selected pages."""

    module = _import_module("run_test14")
    _configure_module(module, pdf_path, output_path)
    extractor = module.base.OCRExtractor()
    records = []
    processed = 0
    document = fitz.open(pdf_path)
    try:
        pages = _route_pages(pages_to_process, len(document))
        for page_no in pages:
            _cancel_if_requested(cancel_check)
            page = document[page_no - 1]
            items = module.base.embedded_items(page)
            if len(items) < 8:
                items = module.base.ocr_items(pdf_path, page_no - 1, extractor)
            page_records = module.custom_extract(items, page_no) or module.base_extract(items, page_no)
            records.extend(page_records)
            processed += 1
            _emit(progress_callback, "Processing pages", processed, len(pages), len(records))
    finally:
        document.close()

    _cancel_if_requested(cancel_check)
    rows = [module.to_template_row(record) for record in records]
    _emit(progress_callback, "Writing Excel", processed, len(pages), len(rows))
    saved_path = module.write_workbook(rows)
    return RunnerResult(Path(saved_path), processed, len(rows))


def _run_test15(
    pdf_path: Path,
    output_path: Path,
    pages_to_process: list[int],
    cancel_check: CancelCheck | None,
    progress_callback: ProgressCallback | None,
) -> RunnerResult:
    """Run the existing Test 15 extraction functions on selected pages."""

    module = _import_module("run_test15")
    _configure_module(module, pdf_path, output_path)
    extractor = module.OCRExtractor()
    records = []
    processed = 0
    total_pages = get_pdf_page_count(pdf_path)
    pages = _route_pages(pages_to_process, total_pages)
    for page_no in pages:
        _cancel_if_requested(cancel_check)
        items = module.ocr_items(pdf_path, page_no, extractor)
        page_records = module.extract_page(items, page_no)
        records.extend(page_records)
        processed += 1
        _emit(progress_callback, "Processing pages", processed, len(pages), len(records))

    _cancel_if_requested(cancel_check)
    rows = [module.to_template_row(record) for record in records]
    _emit(progress_callback, "Writing Excel", processed, len(pages), len(rows))
    saved_path = module.write_workbook(rows)
    return RunnerResult(Path(saved_path), processed, len(rows))


def _run_test16(
    pdf_path: Path,
    output_path: Path,
    pages_to_process: list[int],
    cancel_check: CancelCheck | None,
    progress_callback: ProgressCallback | None,
) -> RunnerResult:
    """Run the existing Test 16 extraction functions on selected pages."""

    module = _import_module("run_test16")
    _configure_module(module, pdf_path, output_path)
    extractor = module.OCRExtractor()
    records = []
    processed = 0
    total_pages = get_pdf_page_count(pdf_path)
    pages = _route_pages(pages_to_process, total_pages)
    for page_no in pages:
        _cancel_if_requested(cancel_check)
        items = module.ocr_items(pdf_path, page_no, extractor)
        page_records = module.extract_page(items, page_no)
        records.extend(page_records)
        processed += 1
        _emit(progress_callback, "Processing pages", processed, len(pages), len(records))

    _cancel_if_requested(cancel_check)
    rows = [module.to_template_row(record) for record in records]
    _emit(progress_callback, "Writing Excel", processed, len(pages), len(rows))
    saved_path = module.write_workbook(rows)
    return RunnerResult(Path(saved_path), processed, len(rows))


def _run_ae(
    pdf_path: Path,
    output_path: Path,
    pages_to_process: list[int],
    cancel_check: CancelCheck | None,
    progress_callback: ProgressCallback | None,
) -> RunnerResult:
    """Run the existing AE D2842LE extraction functions with selected pages."""

    ae = _import_module("run_ae")
    ae.MANUAL_PDF_NAME = pdf_path.name
    _cancel_if_requested(cancel_check)
    _emit(progress_callback, "Processing pages", 0, len(pages_to_process), 0)
    rows = ae.process_pdf_locally(str(pdf_path), pages_to_process)
    _cancel_if_requested(cancel_check)
    _emit(progress_callback, "Writing Excel", len(pages_to_process), len(pages_to_process), len(rows))
    saved_path = ae.write_to_excel(rows, str(TEMPLATE_PATH), str(output_path))
    return RunnerResult(Path(saved_path), len(pages_to_process), len(rows))


def _run_generic_local(
    pdf_path: Path,
    output_path: Path,
    pages_to_process: list[int],
    cancel_check: CancelCheck | None,
    progress_callback: ProgressCallback | None,
) -> RunnerResult:
    """Run the generic local OCR pipeline against a filtered temporary PDF."""

    local_pipeline = _import_module("local_pipeline")
    local_pipeline.MANUAL_PDF_NAME = pdf_path.name
    filtered_pdf = output_path.with_suffix(".selected_pages.pdf")
    source = fitz.open(pdf_path)
    filtered = fitz.open()
    try:
        for page_no in pages_to_process:
            _cancel_if_requested(cancel_check)
            filtered.insert_pdf(source, from_page=page_no - 1, to_page=page_no - 1)
        filtered.save(filtered_pdf)
    finally:
        filtered.close()
        source.close()

    try:
        _emit(progress_callback, "Processing pages", 0, len(pages_to_process), 0)
        rows = local_pipeline.process_pdf_locally(str(filtered_pdf))
        _cancel_if_requested(cancel_check)
        _emit(progress_callback, "Writing Excel", len(pages_to_process), len(pages_to_process), len(rows))
        local_pipeline.write_to_excel(rows, str(TEMPLATE_PATH), str(output_path))
    finally:
        filtered_pdf.unlink(missing_ok=True)
    return RunnerResult(output_path, len(pages_to_process), len(rows))


def _run_general(
    pdf_path: Path,
    output_path: Path,
    pages_to_process: list[int],
    cancel_check: CancelCheck | None,
    progress_callback: ProgressCallback | None,
    component: str = "",
    manufacturer: str = "",
    model: str = "",
) -> RunnerResult:
    """Run the general-purpose extraction pipeline on any PDF."""

    general = _import_module("general_pipeline")
    rows = general.process_pdf(
        pdf_path=pdf_path,
        pages_to_process=pages_to_process,
        component=component,
        manufacturer=manufacturer,
        model=model,
        cancel_check=cancel_check,
        progress_callback=progress_callback,
    )
    _cancel_if_requested(cancel_check)
    _emit(progress_callback, "Writing Excel", len(pages_to_process), len(pages_to_process), len(rows))
    _write_template_rows(rows, output_path)
    return RunnerResult(output_path, len(pages_to_process), len(rows))


def _routes() -> tuple[PipelineRoute, ...]:
    """Known PDF families from the existing extraction scripts."""

    return (
        PipelineRoute("Main distribution board", "run_test1", ("23000143 11l",), ("main distribution board",), "test1"),
        PipelineRoute("Furuno marine radar", "run_test4", ("far2xx8", "ime36520w"), ("furuno", "radar"), "test4"),
        PipelineRoute("AE D2842LE DCI", "run_test5", ("dci dr 12", "d2842le esn"), ("d2842le", "eltis"), "test5"),
        PipelineRoute("MAN B&W L40/45", "run_test6", ("man b&w spares parts catalogue",), ("catalog no", "designation"), "test6"),
        PipelineRoute("AE D2842LE manual", "run_ae", ("ae d2842le spare parts manual",), ("d2842le", "eltis"), "ae"),
        PipelineRoute("Auxiliary engine spare parts 2", "run_test8", ("auxiliary engine spare parts 2",), ("__filename_only_aux_engine_2__",), "test8"),
        PipelineRoute("Auxiliary engine spare parts 1", "run_test7", ("auxiliary engine spare parts 1",), ("__filename_only_aux_engine_1__",), "test7"),
        PipelineRoute("Cargo area fans", "run_test10", ("cargo area fans", "v-202-v0000004"), ("cargo", "fan spare list"), "test10"),
        PipelineRoute("Accommodation fans", "run_test11", ("accommodation fans", "v-203-v0000015"), ("accommodation", "fan spare list"), "test11"),
        PipelineRoute("Positive displacement pump", "run_test12", ("positive displacement pump", "m-213-m0000012"), ("positive displacement pump",), "test12"),
        PipelineRoute("Centrifugal pump", "run_test13", ("centrifugal pump", "m-212-m0000011"), ("centrifugal pump",), "test13"),
        PipelineRoute("OBP spare list extended", "run_test14", ("13k obp spare - full list rev 4 (2) (1) (1)",), ("__filename_only_extended_obp__",), "test14"),
        PipelineRoute("OBP spare list", "run_test9", ("13k obp spare",), ("pil 13k teu",), "test9"),
        PipelineRoute("Hydraulic winch", "run_test15", ("hydraulic winch",), ("hydrowega", "winch"), "test15"),
        PipelineRoute("Carrier 5H specified parts", "run_test16", ("5h", "blowupdiagram"), ("carrier", "5h", "compressor"), "test16"),
    )


def detect_pipeline(pdf_path: Path) -> PipelineRoute | None:
    """Detect the best existing runner for a PDF, if one is known."""

    sample_text = _read_pdf_sample_text(pdf_path)
    for route in _routes():
        if _matches_route(route, pdf_path, sample_text):
            return route
    return None


def _run_route(
    route: PipelineRoute | None,
    pdf_path: Path,
    output_path: Path,
    pages_to_process: list[int],
    cancel_check: CancelCheck | None,
    progress_callback: ProgressCallback | None,
    component: str = "",
    manufacturer: str = "",
    model: str = "",
) -> tuple[str, RunnerResult]:
    """Dispatch to the page-aware wrapper for a detected route."""

    if route is None:
        # Fall back to general pipeline instead of blocking extraction.
        pipeline_name = "General Pipeline (auto-detected)"
        result = _run_general(
            pdf_path, output_path, pages_to_process,
            cancel_check, progress_callback,
            component=component, manufacturer=manufacturer, model=model,
        )
        return pipeline_name, result

    runner = route.runner if route else "generic"
    pipeline_name = route.name
    if runner == "test1":
        result = _run_test1(pdf_path, output_path, pages_to_process, cancel_check, progress_callback)
    elif runner == "test4":
        result = _run_test4(pdf_path, output_path, pages_to_process, cancel_check, progress_callback)
    elif runner == "test5":
        result = _run_test5(pdf_path, output_path, pages_to_process, cancel_check, progress_callback)
    elif runner == "test6":
        result = _run_test6(pdf_path, output_path, pages_to_process, cancel_check, progress_callback)
    elif runner == "test7":
        result = _run_test7_like("run_test7", pdf_path, output_path, pages_to_process, cancel_check, progress_callback)
    elif runner == "test8":
        result = _run_test7_like("run_test8", pdf_path, output_path, pages_to_process, cancel_check, progress_callback)
    elif runner == "test9":
        result = _run_test9(pdf_path, output_path, pages_to_process, cancel_check, progress_callback)
    elif runner == "test10":
        result = _run_test10_like("run_test10", pdf_path, output_path, pages_to_process, cancel_check, progress_callback)
    elif runner == "test11":
        result = _run_test10_like("run_test11", pdf_path, output_path, pages_to_process, cancel_check, progress_callback)
    elif runner == "test12":
        result = _run_test12(pdf_path, output_path, pages_to_process, cancel_check, progress_callback)
    elif runner == "test13":
        result = _run_test13(pdf_path, output_path, pages_to_process, cancel_check, progress_callback)
    elif runner == "test14":
        result = _run_test14(pdf_path, output_path, pages_to_process, cancel_check, progress_callback)
    elif runner == "test15":
        result = _run_test15(pdf_path, output_path, pages_to_process, cancel_check, progress_callback)
    elif runner == "test16":
        result = _run_test16(pdf_path, output_path, pages_to_process, cancel_check, progress_callback)
    elif runner == "ae":
        result = _run_ae(pdf_path, output_path, pages_to_process, cancel_check, progress_callback)
    else:
        result = _run_generic_local(pdf_path, output_path, pages_to_process, cancel_check, progress_callback)
    return pipeline_name, result


def run_extraction(
    pdf_path: Path,
    output_dir: Path,
    pages_to_process: list[int],
    cancel_check: CancelCheck | None = None,
    progress_callback: ProgressCallback | None = None,
    component: str = "",
    manufacturer: str = "",
    model: str = "",
) -> ExtractionResult:
    """Run the matching existing extraction pipeline for selected PDF pages.

    When no specialised pipeline matches the PDF, the general-purpose
    extraction engine is used automatically.

    Parameters *component*, *manufacturer*, and *model* are optional
    metadata strings forwarded to the general pipeline.
    """

    started_at = time.perf_counter()
    pdf_path = pdf_path.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / _safe_output_name(pdf_path)
    route = detect_pipeline(pdf_path)

    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        pipeline_name, runner_result = _run_route(
            route,
            pdf_path,
            output_path,
            pages_to_process,
            cancel_check,
            progress_callback,
            component=component,
            manufacturer=manufacturer,
            model=model,
        )

    generated_path = runner_result.output_path
    if runner_result.rows_extracted == 0:
        generated_path.unlink(missing_ok=True)
        raise NoRowsExtracted(
            f"No spare-parts rows were extracted using {pipeline_name}. "
            "Check that the selected pages contain spare-parts tables, or add a matching pipeline for this PDF format."
        )

    if not generated_path.exists():
        raise FileNotFoundError(f"Extraction finished but no output file was found at {generated_path}")

    return ExtractionResult(
        output_path=generated_path,
        pipeline_name=pipeline_name,
        log_text=stdout.getvalue(),
        summary=ProcessingSummary(
            total_pages_processed=runner_result.pages_processed,
            total_rows_extracted=runner_result.rows_extracted,
            processing_time_seconds=time.perf_counter() - started_at,
        ),
    )
