# Marine OCR Pipeline

An automated data extraction tool built to process complex, multi-page technical marine engineering manuals (e.g., Hyundai MAN B&W engine manuals). 

Leveraging Azure Form Recognizer, this pipeline intelligently parses PDF documents to extract critical spare parts data—such as sub-components, drawing numbers, and position details—and structures the output directly into an Excel template.

## Features
- **Intelligent Chunking**: Splits large PDFs into API-friendly chunks using `PyPDF2`.
- **Azure Form Recognizer**: Extracts complex tables and layout metadata.
- **Dynamic Page Alignment**: Automatically maps table bounding regions back to the exact source page.
- **Custom Heuristics**: Filters out noise and correctly identifies sub-component names.
- **Accuracy Evaluation**: Includes a built-in test script to measure Precision, Recall, and F1-Score against ground-truth data (currently achieving **99.35% F1-Score**).

## Setup
1. Clone the repository.
2. Install the required Python packages (`pip install -r requirements.txt`).
3. Create a `.env` file in the root directory with your Azure credentials:
   ```env
   AZURE_ENDPOINT="your_endpoint_here"
   AZURE_KEY="your_key_here"
   ```
4. Place your PDF manual in the `input/` folder and update the paths in `scripts/pipeline.py`.

## Usage
1. (Optional) Run `python scripts/split_pdf.py` to extract specific test pages.
2. Run `python scripts/pipeline.py` to extract the data and generate the `.xlsm` file.
3. Run `python scripts/evaluate_accuracy.py` to test the extraction accuracy against reference data.
