# Phase 4 Part Number Pattern Audit

Fallback pattern is standardized to `^.{3,}$`. It is intentionally a last resort only.

## Patterns Applied

| Profile | Real samples from fixtures/known-good output | Pattern |
| --- | --- | --- |
| `main_distribution_board.json` | `24264/C60 N`, `24278/C60 N`, `26929`, `XB4BVB5`, `LX1FG220`, `LC1F185`, `LADN-22`, `CAD32P7`, `LADT0` | `^[A-Z0-9][A-Z0-9/-]{2,}(?:\s+[A-Z0-9]+)?$` |
| `man_bw_auxiliary_engine.json` | `51.01102-6056`, `51.01105-6037`, `06.22022-3061`, `51.91301-0071`, `51.90020-0293`, `06.01494-4316`, `51.04410-0169` | `^\d{2}\.\d{5}[-.]\d{3,5}$` |
| `man_bw_main_engine.json` | `011.04.001`, `011.04.016`, `011.04.030`, `017.02.CPL`, `017.02.K`, `017.02.001`, `017.02.002` | `^\d{3}\.\d{2}\.(?:\d{3}|[A-Z]{1,4})$` |
| `man_d2840_le.json` | `51.01101-6832`, `51.01105-6013`, `06.22022-3061`, `51.91301-0071`, `51.90020-0126`, `06.01494-4316` | `^\d{2}\.\d{5}[-.]\d{3,5}$` |
| `shanghai_hengyuan_marine_equipment.json` | `6204-2RZ`, `6205-2RZ`, `6206-2RZ`, `6208-2RZ`, `6309-2RZ` | `^\d{4}-[A-Z0-9]{3}$` |
| `naniwa_pump_mfg_co_ltd.json` | `122`, `509`, `103`, `105`, `702`, `111`, `212`, `217`, `215`, `210` | `^\d{1,4}$` |
| `main_engine_accessories_multi_vendor.json` | `2.27.42.01.2`, `2.61.8.023.2`, `2.25.10.01.2`, `2.33.4.028.2`, `2.11.27.08.1`, `2.65.5.112.0` | `^\d+(?:\.\d+){2,5}$` |
| `bukh.json` | `033D0201`, `033D0204`, `033D0401`, `000E4910`, `000E4923`, `009R2013`, `522C3031`, `503N2367` | `^\d{3}[A-Z]\d{4}$` |

## Not Applied Yet

| Profile | Status | Reason |
| --- | --- | --- |
| `furuno_electric_co_ltd.json` | Needs manual/OCR audit | Known-good output contains mixed model/table text alongside likely codes such as `XN12CF-RSB128-105`; not enough clean part-number rows yet. |
| `obp_mooring_winches_windlass.json` | Mixed/unstructured in current output | Samples include `CL2-523C`, `CL2-523R`, `MP1-30G`, `67296`, plus non-part text like `Gearbox`; needs column cleanup before a strict regex is safe. |
| `hydrowega_holland_bv.json` | Not checked yet | No known-good output file found in this checkout; embedded PDF text is empty, so trustworthy samples require OCR. |
| `carrier.json` | Not checked yet | No known-good output file found in this checkout; embedded PDF text is empty, so trustworthy samples require OCR. |
| `final_drawings_reference.json` | No spare-part part numbers expected | Drawing/title-block reference profile, not a spare-parts table profile. |
| `blow_up_diagram.json`, `emergency_diesel_engine.json`, `hydraulic_winch.json`, `naniwa_centrifugal_pump.json`, `hyundai_13k_obp_spares.json` | Placeholder profiles | These are dummy one-line profiles generated during earlier fixture smoke tests, not validated manufacturer profiles. They should be replaced or removed after confirming no strategy depends on them. |

## Genuinely Unstructured

None confirmed yet. `obp_mooring_winches_windlass.json` is currently mixed, but that appears to be a column-quality issue rather than proven unstructured manufacturer numbering.
