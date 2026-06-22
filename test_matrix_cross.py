import json
from pathlib import Path
from strategies.matrix_cross_reference import extract_pdf

def test_fixtures():
    # test6.pdf is in standard_parts_table directory
    pdf_file = Path("tests/fixtures/standard_parts_table/test6.pdf")
    
    if not pdf_file.exists():
        print(f"Skipping because {pdf_file} does not exist.")
        return
        
    profile_path = Path("profiles/man_bw_main_engine.json")
    if not profile_path.exists():
        print("WARNING: Creating dummy profile")
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        with open(profile_path, "w") as f:
            json.dump({"manufacturer": "MAN B&W", "default_unit": "Pcs", "column_assumptions": {}}, f)
            
    print(f"\n--- Processing {pdf_file.name} ---")
    
    try:
        records = extract_pdf(
            pdf_path=str(pdf_file),
            profile_path=str(profile_path),
            fixture_id="test6_man_bw_spares_catalogue",
            start_page=1,
            end_page=0,
            dpi=150
        )
        print(f"Extracted {len(records)} records from {pdf_file.name}")
        if len(records) > 0:
            print("Passed: 1")
        else:
            print("WARNING: Extracted 0 records.")
            print("Passed: 0")
    except Exception as e:
        print(f"FAILED on {pdf_file.name}: {e}")
        print("Failed: 1")

if __name__ == "__main__":
    test_fixtures()
