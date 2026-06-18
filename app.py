"""Streamlit frontend for the existing spare-parts PDF extraction pipeline."""

from __future__ import annotations

import logging
import queue
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import streamlit as st

from scripts.extraction_entrypoint import (
    CancelledExtraction,
    ExtractionResult,
    detect_pipeline,
    get_pdf_page_count,
    parse_page_ranges,
    run_extraction,
)


PROJECT_ROOT = Path(__file__).resolve().parent


def configure_logging(log_path: Path) -> logging.Logger:
    """Configure application logging and return the app logger."""

    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=log_path,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return logging.getLogger(__name__)


def save_uploaded_pdf(uploaded_file: Any, destination_dir: Path) -> Path:
    """Persist a Streamlit uploaded PDF into a temporary directory."""

    destination_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(uploaded_file.name).name
    pdf_path = destination_dir / safe_name
    pdf_path.write_bytes(uploaded_file.getbuffer())
    return pdf_path


def preserve_failed_pdf(pdf_path: Path) -> Path:
    """Copy a failed upload into output for later pipeline analysis."""

    failed_dir = PROJECT_ROOT / "output" / "failed_uploads"
    failed_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    safe_name = Path(pdf_path.name).name
    target = failed_dir / f"{stamp}_{safe_name}"
    shutil.copy2(pdf_path, target)
    return target


def initialize_state() -> None:
    """Create Streamlit session keys used by the extraction workflow."""

    defaults = {
        "download_name": None,
        "download_bytes": None,
        "pipeline_name": None,
        "extraction_log": None,
        "summary": None,
        "worker": None,
        "cancel_event": None,
        "event_queue": None,
        "progress": None,
        "error_message": None,
        "cancelled": False,
        "uploaded_signature": None,
        "uploaded_page_count": None,
        "uploaded_pipeline_name": None,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def reset_result_state() -> None:
    """Clear result and status data before starting a fresh extraction."""

    for key in (
        "download_name",
        "download_bytes",
        "pipeline_name",
        "extraction_log",
        "summary",
        "error_message",
    ):
        st.session_state[key] = None
    st.session_state["cancelled"] = False
    st.session_state["progress"] = {
        "stage": "Uploading",
        "processed": 0,
        "total": 0,
        "rows": 0,
    }


def cache_result(result: ExtractionResult) -> dict[str, Any]:
    """Convert a completed extraction result into queue-safe data."""

    return {
        "download_name": result.output_path.name,
        "download_bytes": result.output_path.read_bytes(),
        "pipeline_name": result.pipeline_name,
        "extraction_log": result.log_text,
        "summary": result.summary,
    }


def analyze_uploaded_pdf(uploaded_file: Any) -> tuple[int, str]:
    """Return page count and detected pipeline name for an uploaded PDF."""

    with tempfile.TemporaryDirectory(prefix="ocr_analyze_") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        pdf_path = save_uploaded_pdf(uploaded_file, temp_dir)
        page_count = get_pdf_page_count(pdf_path)
        route = detect_pipeline(pdf_path)
    return page_count, route.name if route else "No matching pipeline"


def sync_page_range_default(uploaded_file: Any) -> None:
    """Set the page range field to all pages when a new PDF is uploaded."""

    if uploaded_file is None:
        return

    signature = (uploaded_file.name, uploaded_file.size)
    if st.session_state.get("uploaded_signature") == signature:
        return

    try:
        page_count, pipeline_name = analyze_uploaded_pdf(uploaded_file)
    except Exception:
        st.session_state["uploaded_signature"] = signature
        st.session_state["uploaded_page_count"] = None
        st.session_state["uploaded_pipeline_name"] = None
        st.session_state["page_ranges_value"] = ""
        return

    st.session_state["uploaded_signature"] = signature
    st.session_state["uploaded_page_count"] = page_count
    st.session_state["uploaded_pipeline_name"] = pipeline_name
    st.session_state["page_ranges_value"] = f"1-{page_count}" if page_count > 1 else "1"


def extraction_worker(
    pdf_path: Path,
    output_dir: Path,
    selected_pages: list[int],
    temp_dir: Path,
    cancel_event: threading.Event,
    events: queue.Queue[dict[str, Any]],
    component: str = "",
    manufacturer: str = "",
    model: str = "",
) -> None:
    """Run extraction in a worker thread and publish UI-safe events."""

    try:
        def is_cancelled() -> bool:
            return cancel_event.is_set()

        def report_progress(stage: str, processed: int, total: int, rows: int) -> None:
            events.put({
                "type": "progress",
                "stage": stage,
                "processed": processed,
                "total": total,
                "rows": rows,
            })

        result = run_extraction(
            pdf_path=pdf_path,
            output_dir=output_dir,
            pages_to_process=selected_pages,
            cancel_check=is_cancelled,
            progress_callback=report_progress,
            component=component,
            manufacturer=manufacturer,
            model=model,
        )
        if cancel_event.is_set():
            raise CancelledExtraction("Extraction cancelled by user.")
        events.put({"type": "success", **cache_result(result)})
    except CancelledExtraction:
        events.put({"type": "cancelled"})
    except Exception as exc:
        logging.getLogger(__name__).exception("PDF extraction failed")
        preserved_path = preserve_failed_pdf(pdf_path) if pdf_path.exists() else None
        message = str(exc)
        if preserved_path:
            message = f"{message} Saved failed upload for analysis: {preserved_path}"
        events.put({"type": "error", "message": message})
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def start_extraction(
    uploaded_file: Any,
    page_ranges: str,
    logger: logging.Logger,
    component: str = "",
    manufacturer: str = "",
    model: str = "",
) -> None:
    """Validate page ranges, save the upload, and start the worker thread."""

    reset_result_state()
    temp_dir = Path(tempfile.mkdtemp(prefix="ocr_streamlit_"))
    try:
        input_dir = temp_dir / "input"
        output_dir = temp_dir / "output"
        pdf_path = save_uploaded_pdf(uploaded_file, input_dir)
        page_count = get_pdf_page_count(pdf_path)
        selected_pages = parse_page_ranges(page_ranges, page_count)
    except ValueError as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        st.session_state["error_message"] = str(exc)
        return
    except Exception as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        logger.exception("Could not prepare uploaded PDF")
        st.session_state["error_message"] = f"Could not prepare uploaded PDF: {exc}"
        return

    events: queue.Queue[dict[str, Any]] = queue.Queue()
    cancel_event = threading.Event()
    worker = threading.Thread(
        target=extraction_worker,
        args=(pdf_path, output_dir, selected_pages, temp_dir, cancel_event, events, component, manufacturer, model),
        daemon=False,
    )

    st.session_state["event_queue"] = events
    st.session_state["cancel_event"] = cancel_event
    st.session_state["worker"] = worker
    st.session_state["progress"] = {
        "stage": "Uploading",
        "processed": 0,
        "total": len(selected_pages),
        "rows": 0,
    }
    worker.start()


def drain_worker_events() -> None:
    """Move worker events into Streamlit session state."""

    events = st.session_state.get("event_queue")
    if events is None:
        return

    while True:
        try:
            event = events.get_nowait()
        except queue.Empty:
            break

        event_type = event.get("type")
        if event_type == "progress":
            st.session_state["progress"] = {
                "stage": event["stage"],
                "processed": event["processed"],
                "total": event["total"],
                "rows": event["rows"],
            }
        elif event_type == "success":
            st.session_state["download_name"] = event["download_name"]
            st.session_state["download_bytes"] = event["download_bytes"]
            st.session_state["pipeline_name"] = event["pipeline_name"]
            st.session_state["extraction_log"] = event["extraction_log"]
            st.session_state["summary"] = event["summary"]
            st.session_state["worker"] = None
            st.session_state["cancel_event"] = None
            st.session_state["event_queue"] = None
            st.session_state["progress"] = {
                "stage": "Completed",
                "processed": st.session_state["progress"]["total"],
                "total": st.session_state["progress"]["total"],
                "rows": event["summary"].total_rows_extracted,
            }
        elif event_type == "cancelled":
            st.session_state["cancelled"] = True
            st.session_state["download_name"] = None
            st.session_state["download_bytes"] = None
            st.session_state["worker"] = None
            st.session_state["cancel_event"] = None
            st.session_state["event_queue"] = None
        elif event_type == "error":
            st.session_state["error_message"] = event["message"]
            st.session_state["worker"] = None
            st.session_state["cancel_event"] = None
            st.session_state["event_queue"] = None


def render_running_state() -> None:
    """Render progress and cancellation controls while extraction is active."""

    progress = st.session_state.get("progress") or {}
    total = max(int(progress.get("total") or 0), 1)
    processed = int(progress.get("processed") or 0)
    stage = str(progress.get("stage") or "Processing pages")
    rows = int(progress.get("rows") or 0)

    if stage == "Uploading":
        progress_value = 10
    elif stage == "Writing Excel":
        progress_value = 90
    else:
        progress_value = min(85, 15 + int((processed / total) * 70))

    st.progress(progress_value, text=stage)
    st.write(f"Pages processed: {processed}/{total}")
    st.write(f"Rows extracted so far: {rows}")

    if st.button("Stop Extraction", type="secondary"):
        cancel_event = st.session_state.get("cancel_event")
        if cancel_event is not None:
            cancel_event.set()
            st.warning("Stopping extraction after the current page finishes.")


def render_download() -> None:
    """Render the workbook download button and processing summary."""

    workbook_bytes = st.session_state.get("download_bytes")
    download_name = st.session_state.get("download_name")
    if not workbook_bytes or not download_name:
        return

    st.success("Extraction completed.")
    summary = st.session_state.get("summary")
    if summary:
        st.write(f"Total pages processed: {summary.total_pages_processed}")
        st.write(f"Total rows extracted: {summary.total_rows_extracted}")
        st.write(f"Processing time: {summary.processing_time_seconds:.2f} seconds")

    st.download_button(
        label="Download Excel",
        data=workbook_bytes,
        file_name=download_name,
        mime="application/vnd.ms-excel.sheet.macroEnabled.12",
    )

    pipeline_name = st.session_state.get("pipeline_name")
    if pipeline_name:
        st.caption(f"Generated by: {pipeline_name}")

    extraction_log = st.session_state.get("extraction_log")
    if extraction_log:
        with st.expander("Extraction log"):
            st.code(extraction_log)


def main() -> None:
    """Render the Streamlit app and manage extraction workflow state."""

    st.set_page_config(page_title="PDF Spare Parts Extractor", page_icon=":page_facing_up:")
    logger = configure_logging(PROJECT_ROOT / "output" / "streamlit_app.log")
    initialize_state()
    drain_worker_events()

    st.title("PDF Spare Parts Extractor")

    uploaded_file = st.file_uploader(
        "Upload PDF manual",
        type=["pdf"],
        accept_multiple_files=False,
    )
    sync_page_range_default(uploaded_file)

    page_ranges = st.text_input(
        "Pages to Process",
        key="page_ranges_value",
        help="Examples: 12-14,20-25 or 1-5,8,10-12",
    )

    st.sidebar.header("Optional Metadata")
    st.sidebar.write("If the PDF doesn't contain this information, you can provide it here:")
    component = st.sidebar.text_input("Component Name", value="", help="e.g. Auxiliary Engine")
    manufacturer = st.sidebar.text_input("Manufacturer", value="")
    model = ""  # Model is always extracted from the page, not user-supplied

    page_count = st.session_state.get("uploaded_page_count")
    if page_count:
        st.caption(f"PDF pages: {page_count}")

    pipeline_name = st.session_state.get("uploaded_pipeline_name")
    if pipeline_name:
        st.caption(f"Detected pipeline: {pipeline_name}")
        if pipeline_name == "No matching pipeline":
            st.info("No specialized pipeline matched. The general extraction engine will be used automatically.")

    worker = st.session_state.get("worker")
    is_running = worker is not None and worker.is_alive()

    extract_clicked = st.button(
        "Extract Excel",
        type="primary",
        disabled=uploaded_file is None or is_running,
    )

    if extract_clicked and uploaded_file is not None:
        start_extraction(uploaded_file, page_ranges, logger, component, manufacturer, model)
        st.rerun()

    if st.session_state.get("error_message"):
        st.error(st.session_state["error_message"])

    if st.session_state.get("cancelled"):
        st.warning("Extraction cancelled by user.")

    worker = st.session_state.get("worker")
    if worker is not None and worker.is_alive():
        render_running_state()
        time.sleep(1)
        drain_worker_events()
        st.rerun()

    render_download()


if __name__ == "__main__":
    main()
