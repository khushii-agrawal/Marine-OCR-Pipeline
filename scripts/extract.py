from azure.ai.formrecognizer import DocumentAnalysisClient
from azure.core.credentials import AzureKeyCredential
from dotenv import load_dotenv
import os

# =========================
# LOAD ENV VARIABLES
# =========================

load_dotenv("../.env")

endpoint = os.getenv("AZURE_ENDPOINT")
key = os.getenv("AZURE_KEY")

# =========================
# CREATE AZURE CLIENT
# =========================

client = DocumentAnalysisClient(
    endpoint=endpoint,
    credential=AzureKeyCredential(key)
)

# =========================
# PDF PATH
# =========================

pdf_path = "../input/test_pages.pdf"

print("Sending PDF to Azure...")

# =========================
# ANALYZE DOCUMENT
# =========================

with open(pdf_path, "rb") as f:

    poller = client.begin_analyze_document(
        "prebuilt-layout",
        document=f
    )

    result = poller.result()

print("Extraction completed!")

# =========================
# CREATE OUTPUT FOLDER
# =========================

os.makedirs("../output", exist_ok=True)

# =========================
# EXTRACT TABLES
# =========================

for table_index, table in enumerate(result.tables):

    print(f"\n========== TABLE {table_index} ==========\n")

    rows = table.row_count
    cols = table.column_count

    # Create empty matrix
    matrix = [["" for _ in range(cols)] for _ in range(rows)]

    # Fill matrix using Azure cells
    for cell in table.cells:
        matrix[cell.row_index][cell.column_index] = cell.content

    # Convert to DataFrame
    df = pd.DataFrame(matrix)

    # Skip empty tables
    if df.empty:
        print("Skipped empty table")
        continue

    # =========================
    # USE FIRST ROW AS HEADER
    # =========================

    df.columns = df.iloc[0]

    # Remove first row from data
    df = df[1:]

    # =========================
    # CLEAN EMPTY VALUES
    # =========================

    # Replace empty strings with NaN
    df = df.replace(r'^\s*$', pd.NA, regex=True)

    # Remove fully empty rows
    df = df.dropna(how='all')

    # =========================
    # OCR CLEANING RULES
    # =========================

    # Only apply if Qty column exists
    if "Qty" in df.columns:

        df["Qty"] = df["Qty"].replace({
            "t": None,
            "-": None,
            "": None,
            "I": "1",
            "O": "0"
        })

    # =========================
    # REMOVE DUPLICATE HEADERS
    # =========================

    if "Item no" in df.columns:

        df = df[df["Item no"] != "Item no"]

    # =========================
    # RESET INDEX
    # =========================

    df = df.reset_index(drop=True)

    # =========================
    # PRINT CLEAN TABLE
    # =========================

    print(df)

    # =========================
    # SAVE CSV
    # =========================

    output_path = f"../output/table_{table_index}.csv"

    df.to_csv(output_path, index=False)

    print(f"Saved: {output_path}")

print("\nAll tables extracted successfully!")

