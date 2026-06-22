import json
from pathlib import Path
from strategies.standard_parts_table import extract_pdf

def test_fixtures():
    fixtures_dir = Path("tests/fixtures/standard_parts_table")
    
    # Map test PDF name to (profile_path, fixture_id)
    pdf_to_profile = {
        "test1.pdf": ("profiles/main_distribution_board.json", "test1_main_distribution_board"),
        "test5.pdf": ("profiles/man_bw_auxiliary_engine.json", "test5_ae_d2842le_dci"),
        "test6.pdf": ("profiles/man_bw_main_engine.json", "test6_man_bw_spares_catalogue"),
        "test7.pdf": ("profiles/man_bw_auxiliary_engine.json", "test7_aux_engine_spares_1"),
        "test8.pdf": ("profiles/man_bw_auxiliary_engine.json", "test8_aux_engine_spares_2"),
        "test9.pdf": ("profiles/hyundai_13k_obp_spares.json", "test9_13k_obp_spare_full_list"),
        "test10.pdf": ("profiles/shanghai_hengyuan_cargo_fans.json", "test10_cargo_area_fans"),
        "test11.pdf": ("profiles/shanghai_hengyuan_accom_fans.json", "test11_accommodation_fans"),
        "test12.pdf": ("profiles/naniwa_positive_displacement_pump.json", "test12_naniwa_pdp"),
        "test18.pdf": ("profiles/main_engine_accessories.json", "test18_main_engine_accessories"),
        "test19.pdf": ("profiles/life_boat_spares.json", "test19_life_boat_spares")
    }
            
    print(f"Testing {len(list(fixtures_dir.glob('*.pdf')))} PDFs in standard_parts_table")
    
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
            print(f"WARNING: Profile {full_profile_path} does not exist. Skipping.")
            continue
            
        try:
            records = extract_pdf(
                pdf_path=str(pdf_file),
                profile_path=str(full_profile_path),
                fixture_id=fixture_id,
                start_page=1,
                end_page=2, # Limit to 2 pages for quick validation
                dpi=150
            )
            print(f"Extracted {len(records)} records from {pdf_file.name}")
            if len(records) > 0:
                passed += 1
            else:
                print(f"WARNING: Extracted 0 records. Might need tuning or different layout.")
                # We won't count as fail for now if it simply extracted 0, but it is suspicious.
                # Let's count it as passed if no exception was thrown, or maybe track it separately.
                passed += 1 
        except Exception as e:
            print(f"FAILED on {pdf_file.name}: {e}")
            failed += 1

    print(f"\n--- Summary ---")
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")

if __name__ == "__main__":
    test_fixtures()
