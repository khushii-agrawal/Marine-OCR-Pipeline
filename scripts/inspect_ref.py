import openpyxl

ref_path = "refrence/VOLUME I.pdf.xlsm"
wb = openpyxl.load_workbook(ref_path, read_only=True, data_only=True)
sheet = wb.active
print("Active sheet title:", sheet.title)

# Let's inspect headers (usually rows 1-3)
for row_idx in range(1, 10):
    row_vals = [sheet.cell(row=row_idx, column=col_idx).value for col_idx in range(1, 22)]
    print(f"Row {row_idx}: {row_vals}")

wb.close()
