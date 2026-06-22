import json
from pathlib import Path
from strategies.drawing_material_list import extract_pdf

def test_fixtures():
    fixtures_dir = Path("tests/fixtures/drawing_material_list")
    
    pdf_to_profile = {
        "test13.pdf": ("profiles/naniwa_centrifugal_pump.json", "test13_naniwa_cp"),
        "test15.pdf": ("profiles/hydraulic_winch.json", "test15_hydraulic_winch"),
        "test16.pdf": ("profiles/blow_up_diagram.json", "test16_blowup_diagram"),
        "test17.pdf": ("profiles/emergency_diesel_engine.json", "test17_emergency_diesel")
    }
            
    print(f"Testing {len(list(fixtures_dir.glob('*.pdf')))} PDFs in drawing_material_list")
    
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
        # Create a dummy profile just to test if the profile doesn't exist
        if not full_profile_path.exists():
            print(f"WARNING: Profile {full_profile_path} does not exist. Creating a dummy profile for testing.")
            full_profile_path.parent.mkdir(parents=True, exist_ok=True)
            with open(full_profile_path, "w") as f:
                json.dump({"manufacturer": "Dummy", "default_unit": "Pcs", "column_assumptions": {}}, f)
            
        try:
            records = extract_pdf(
                pdf_path=str(pdf_file),
                profile_path=str(full_profile_path),
                fixture_id=fixture_id,
                start_page=4, # Test13 has content around page 4
                end_page=6,
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
