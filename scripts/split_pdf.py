from PyPDF2 import PdfReader, PdfWriter

input_pdf = "../input/VOLUME I.pdf"
output_pdf = "../input/test_pages.pdf"

reader = PdfReader(input_pdf)
writer = PdfWriter()
print("Total Pages:", len(reader.pages))

# Define page ranges
ranges = [
    (22, 23),   # pages 23-24
    (58, 60),   # pages 59-61
    (140, 149), # pages 141-150
    (186, 191), # pages 187-192
]

# Extract pages
for start, end in ranges:

    for page_num in range(start, end + 1):
        writer.add_page(reader.pages[page_num])

# Save test PDF
with open(output_pdf, "wb") as output_file:
    writer.write(output_file)

print("Test PDF created successfully!")