import openpyxl

out_path = "output/VOLUME_I_extracted.xlsm"
wb = openpyxl.load_workbook(out_path, read_only=True, data_only=True)
sheet = wb.active
print("Active sheet title:", sheet.title)

# Let's inspect headers (usually rows 1-3)
for row_idx in range(1, 25):
    row_vals = [sheet.cell(row=row_idx, column=col_idx).value for col_idx in range(1, 22)]
    # Filter empty rows
    if any(row_vals):
        print(f"Row {row_idx}: {row_vals}")

wb.close()
