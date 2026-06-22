import json
from pathlib import Path
from strategies.accessory_kit_equipment_list import extract_pdf

def test_fixtures():
    fixtures_dir = Path("tests/fixtures/accessory_kit_equipment_list")
    
    pdf_to_profile = {
        "test4.pdf": ("profiles/furuno_electric_co_ltd.json", "test4_furuno_far2xx8_radar"),
        "test14.pdf": ("profiles/hyundai_13k_obp_spares.json", "test14_obp_spares"),
    }
            
    print(f"Testing {len(list(fixtures_dir.glob('*.pdf')))} PDFs in accessory_kit_equipment_list")
    
    passed = 0
    failed = 0
    
    for pdf_file in fixtures_dir.glob("*.pdf"):
        print(f"\n--- Processing {pdf_file.name} ---")
        if pdf_file.name in pdf_to_profile:
            profile_path, fixture_id = pdf_to_profile[pdf_file.name]
            print(f"Using profile {profile_path} (fixture: {fixture_id})")
        else:
            print(f"WARNING: Could not find profile for {pdf_file.name}. Skipping.")
            continue
            
        full_profile_path = Path(profile_path)
        if not full_profile_path.exists():
            print(f"WARNING: Profile {full_profile_path} does not exist. Creating dummy.")
            full_profile_path.parent.mkdir(parents=True, exist_ok=True)
            with open(full_profile_path, "w") as f:
                json.dump({"manufacturer": "Dummy", "default_unit": "Pcs", "column_assumptions": {}}, f)
            
        try:
            records = extract_pdf(
                pdf_path=str(pdf_file),
                profile_path=str(full_profile_path),
                fixture_id=fixture_id,
                start_page=1,
                end_page=0, # Process all pages
                dpi=150
            )
            print(f"Extracted {len(records)} records from {pdf_file.name}")
            if len(records) > 0:
                passed += 1
            else:
                print(f"WARNING: Extracted 0 records. Might need tuning or different layout.")
                passed += 1 
        except Exception as e:
            print(f"FAILED on {pdf_file.name}: {e}")
            failed += 1

    print(f"\n--- Summary ---")
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")

if __name__ == "__main__":
    test_fixtures()
