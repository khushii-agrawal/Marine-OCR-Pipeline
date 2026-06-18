import os
from pathlib import Path

import run_test10 as base


PROJECT_ROOT = Path(__file__).resolve().parent.parent

base.PDF_PATH = PROJECT_ROOT / "test" / "Test 11" / "V-203-V0000015-accommodation fans-Rev.1.1.pdf"
base.OUTPUT_PATH = PROJECT_ROOT / "output" / "Test11_Accommodation_Fans_extracted.xlsm"
base.COMPONENT = "Accommodation Fans"
base.MANUAL_PDF_NAME = "V-203-V0000015-accommodation fans-Rev.1.1.pdf"
base.EXTRACTED_PDF_NAME = "AF_AccommodationFans.pdf"
base.START_PAGE = int(os.environ.get("TEST11_START_PAGE", "1"))
base.END_PAGE = int(os.environ.get("TEST11_END_PAGE", "0"))


if __name__ == "__main__":
    base.main()
