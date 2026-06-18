# Marine OCR Pipeline

An automated data extraction tool built to process complex, multi-page technical marine engineering manuals (e.g., Hyundai MAN B&W engine manuals). 

This project consists of two different engines that accomplish the same goal: intelligent parsing of PDF documents to extract critical spare parts data (such as sub-components, drawing numbers, and position details) and structuring the output directly into an Excel template.

## Architecture

### 1. Phase 1: Azure Cloud Engine (`scripts/pipeline.py`)
Leverages the **Azure Document Intelligence** cloud API.
- **Features**: Highly accurate out-of-the-box table extraction, intelligent chunking for large PDFs, and dynamic page alignment.
- **Pros**: Minimal custom table layout logic required.
- **Cons**: Requires an internet connection and incurs Azure API costs.

### 2. Phase 2: Local AI Engine (`scripts/local_engine/local_pipeline.py`)
Leverages the open-source **PaddleOCR** library running entirely on your local machine.
- **Features**: Uses coordinate-based bounding box clustering to group text into rows, completely replacing the need for unreliable OpenCV grid detection. Uses smart layout heuristics to filter out page footers, vertical labels, and noise.
- **Pros**: **100% Free and Private**. Runs completely offline.
- **Cons**: Requires careful tuning of X/Y coordinate percentages for custom manual layouts.
- **Accuracy**: Currently achieves **98.06% F1-Score** parity with the Azure baseline!

## Setup

1. Clone the repository.
2. Install the required Python packages:
   ```bash
   pip install -r requirements.txt
   ```

### Important Local Engine Dependencies
If you plan to use the local PaddleOCR engine, ensure you have the following specific versions installed to avoid C++ compiler crashes and array parsing errors on Windows:
```bash
pip install "numpy<2"
pip install paddlepaddle==2.6.2
```

### Azure Setup (For Phase 1 Only)
Create a `.env` file in the root directory with your Azure credentials:
```env
AZURE_ENDPOINT="your_endpoint_here"
AZURE_KEY="your_key_here"
```

## Usage

Place your PDF manual (e.g., `VOLUME I.pdf`) in the `input/` folder and ensure your Excel template (`Spares_Capture_Template_Ver12 2.xlsm`) is in the `template/` folder.

**To run the Azure Cloud Engine (Phase 1):**
```bash
python scripts/pipeline.py
```

**To run the Local PaddleOCR Engine (Phase 2):**
```bash
python scripts/local_engine/local_pipeline.py
```

### Accuracy Evaluation
We include a built-in test script to measure Precision, Recall, and F1-Score of our local engine's extraction against a ground-truth reference file.

To test the extraction accuracy:
```bash
python scripts/evaluate_accuracy.py
```
