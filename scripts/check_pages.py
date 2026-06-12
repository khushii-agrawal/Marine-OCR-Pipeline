import re
import os

log_path = r"C:\Users\Lenovo\.gemini\antigravity-ide\brain\a8de2e5d-8029-4c36-8c5b-2b5cd003afd7\.system_generated\tasks\task-105.log"
with open(log_path, 'r', encoding='utf-8') as f:
    text = f.read()

table_pages = []
for line in text.split('\n'):
    if "Processing page" in line:
        current_page = line.split()[2].split('/')[0]
    if "[TABLE PAGE]" in line:
        table_pages.append(current_page)

print(f"Total Table Pages Found: {len(table_pages)}")
print(f"Pages: {table_pages}")
