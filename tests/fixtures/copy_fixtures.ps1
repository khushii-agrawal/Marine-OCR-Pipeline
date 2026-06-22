# Copy fixture PDFs from Test directories into layout-specific fixture dirs
$root = "d:\OCRProject"

$copies = @{
    # standard_parts_table
    "Test\Test 1\23000143 11L REV 1 AS BUILT 5-12-2013 Pg No 47-53.pdf" = "tests\fixtures\standard_parts_table\test1.pdf"
    "Test\Test 5\Extracted pages from DCI DR 12 & 14 AE PARTS CAT D2842LE ESN 49361860994101.pdf" = "tests\fixtures\standard_parts_table\test5.pdf"
    "Test\Test 6\Extracted pages from MAN B&W SPARES PARTS CATALOGUE.pdf" = "tests\fixtures\standard_parts_table\test6.pdf"
    "Test\Test 7\Auxiliary engine spare parts 1.pdf" = "tests\fixtures\standard_parts_table\test7.pdf"
    "Test\Test 8\Auxiliary engine spare parts 2 1.pdf" = "tests\fixtures\standard_parts_table\test8.pdf"
    "Test\Test 9\13k obp spare - full list rev 4 (2) (1).pdf" = "tests\fixtures\standard_parts_table\test9.pdf"
    "Test\Test 10\V-202-V0000004-cargo area fans-Rev.1.1.pdf" = "tests\fixtures\standard_parts_table\test10.pdf"
    "Test\Test 11\V-203-V0000015-accommodation fans-Rev.1.1.pdf" = "tests\fixtures\standard_parts_table\test11.pdf"
    "Test\Test 12\M-213-M0000012-Positive Displacement Pump-1 REV1.1 (2).pdf" = "tests\fixtures\standard_parts_table\test12.pdf"
    "Test\Test 18\Extracted pages from Main engine assessories (1).pdf" = "tests\fixtures\standard_parts_table\test18.pdf"
    "Test\Test 19\Life boat spares 1 (1).pdf" = "tests\fixtures\standard_parts_table\test19.pdf"
    # drawing_material_list
    "Test\Test 13\M-212-M0000011-Centrifugal Pump REV1.1 (3).pdf" = "tests\fixtures\drawing_material_list\test13.pdf"
    "Test\Test 15\Extracted pages from Hydraulic winch.pdf" = "tests\fixtures\drawing_material_list\test15.pdf"
    "Test\Test 16\5H _blowUpDiagram (1).pdf" = "tests\fixtures\drawing_material_list\test16.pdf"
    "Test\Test 17\Emergency diesel engine 1 1.pdf" = "tests\fixtures\drawing_material_list\test17.pdf"
    # accessory_kit_equipment_list
    "Test\Test 4\IME36520W_FAR2xx8 pg No 18-29,132,197-215.pdf" = "tests\fixtures\accessory_kit_equipment_list\test4.pdf"
    "Test\Test 14\13k obp spare - full list rev 4 (2) (1) (1).pdf" = "tests\fixtures\accessory_kit_equipment_list\test14.pdf"
}

$copied = 0
$missing = 0
foreach ($src in $copies.Keys) {
    $srcPath = Join-Path $root $src
    $dstPath = Join-Path $root $copies[$src]
    if (Test-Path $srcPath) {
        Copy-Item -Path $srcPath -Destination $dstPath -Force
        $copied++
        Write-Host "OK: $($copies[$src])"
    } else {
        $missing++
        Write-Host "MISSING: $src"
    }
}
Write-Host "`nCopied: $copied, Missing: $missing"
