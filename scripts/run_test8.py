from pathlib import Path

import run_test7 as base


PROJECT_ROOT = Path(__file__).resolve().parents[1]

base.PDF_PATH = PROJECT_ROOT / "test" / "Test 8" / "Auxiliary engine spare parts 2 1.pdf"
default_output = PROJECT_ROOT / "output" / "Test8_AE_spares_extracted.xlsm"
base.OUTPUT_PATH = Path(
    __import__("os").getenv("TEST8_OUTPUT_PATH", str(default_output))
)
base.START_PAGE = int(__import__("os").getenv("TEST8_START_PAGE", "2"))
base.END_PAGE = int(__import__("os").getenv("TEST8_END_PAGE", "216"))


if __name__ == "__main__":
    base.main()
