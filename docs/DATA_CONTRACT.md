# Data Contract

## Source files

The primary measured-data route accepts three semicolon-separated Fluvius CSV
exports using comma decimals.

- Grid offtake: select register `Afname Actief`, unit `kWh`.
- Grid injection: select register `Injectie Actief`, unit `kWh`.
- PV production: select register `Productie Actief`, unit `kWh`.

File names and EANs are not role identifiers. The PV submeter export can contain
other registers and other EANs, including `Hulpverbruik Actief`; these are not
PV production. If more than one candidate series exists for a required role,
normalization must stop and request an explicit selection.

Required source columns are:

- `Van (datum)` — Belgian day-month-year. Each file must use one consistent
  format: `DD-MM-YYYY` (hyphen) or `DD/MM/YYYY` (slash). The parser detects the
  format from non-empty `Van (datum)` and `Tot (datum)` values before converting
  intervals. Mixed, unsupported, or inconsistent dates are fatal.
- `Van (tijdstip)` — `HH:MM:SS` or `HH:MM`, including single-digit hours such as
  `0:00:00`.
- `Tot (datum)`
- `Tot (tijdstip)`
- `Register`
- `Volume`
- `Eenheid`
- `Validatiestatus`

`EAN-code`, `Meter`, `Metertype`, and `Omschrijving` are optional metadata and
must not be used to join the three measurements.

## Canonical interval dataset

One row represents one physical quarter-hour. Required columns are:

- `timestamp_utc`: timezone-aware UTC interval start; unique and increasing.
- `timestamp_local`: Europe/Brussels interval start including UTC offset.
- `interval_hours`: always 0.25 in version 1.
- `grid_import_baseline_kwh`: non-negative grid offtake.
- `grid_export_baseline_kwh`: non-negative grid injection.
- `pv_production_kwh`: non-negative measured PV production.
- `site_load_kwh`: reconstructed gross customer load.
- `offtake_quality`, `injection_quality`, `pv_quality`: normalized source
  validation statuses.
- `quality_flag`: combined `validated`, `unvalidated`, or `unavailable` state.
- `pv_source`: `measured_fluvius` or a clearly named synthetic source.

All energy columns are AC energy during the interval in kWh. Power is derived as
`energy_kwh / interval_hours` and stored only in dispatch/reporting outputs when
useful.

The balance identity is:

```text
site_load_kwh = pv_production_kwh
              + grid_import_baseline_kwh
              - grid_export_baseline_kwh
```

Small negative values within a documented numerical tolerance may be rounded to
zero. Material negative load, or grid export materially greater than PV, must
cause a validation failure or an explicitly acknowledged site-boundary
exception. Values must never be silently clipped.

Fluvius offtake and injection registers accumulate energy over each
quarter-hour. They are directional energy totals, not instantaneous power.
Both can therefore be positive in the same interval when the site changes
direction within those 15 minutes. That is expected for these data. Simultaneous
import and export is recorded in the validation report as an informational
diagnostic (`simultaneous_import_export`). It is not a fatal condition, not a
warning, and not a site-boundary exception. The source values remain separate
canonical columns and must not be netted, clipped, overwritten, or discarded.

## DST and timezone requirements

The Fluvius exports contain Belgian local wall-clock start and end values but no
UTC offset. The parser must use `Europe/Brussels` and preserve physical interval
order.

- During the autumn transition, 02:00 through 02:45 occurs twice. Both sets are
  real and must survive as distinct UTC intervals.
- During the spring transition, a source row such as local 01:45 to 03:00 is one
  physical 15-minute interval, not a 75-minute interval and not five intervals.
- Start and end values, row order, and the timezone transition must be used
  together to resolve ambiguous local timestamps.
- After conversion, every accepted row must span exactly 15 minutes in UTC.
- The canonical UTC index must be unique, increasing, and gap-free over every
  period offered for simulation.

The validation report lists each detected UTC-offset change under
`dst.transitions`. Each entry records the local date, whether the clocks moved
forward or backward, the UTC offsets before and after the change, and the number
of physical quarter-hours in that local day. For a selected simulation period,
the list contains only transitions detected in that period. An ingestion-only
report uses the continuous common coverage.

Dropping duplicate local timestamps, averaging them, using
`ambiguous='infer'` after deduplication, shifting nonexistent timestamps, or
building a naive 96-row local day are prohibited.

## Validation severities

Fatal conditions include:

- Missing required columns.
- Missing or ambiguous required register.
- Unit other than kWh for the selected active-energy series.
- Unparseable interval boundaries.
- Mixed, unsupported, or inconsistent Fluvius date formats in a file.
- Duplicate or non-monotonic canonical UTC intervals.
- A physical interval other than 15 minutes after timezone conversion.
- Null or `Geen gegevens` readings inside a selected simulation period.
- Unresolved quarter-hour gaps.
- Material negative reconstructed load, or grid export materially greater than
  measured PV.

Warnings include:

- Non-null `Ongevalideerd` readings.
- Additional unused registers or EANs.
- Minor values inside an explicitly documented numerical tolerance.
- Partial calendar years.

The validation report also records `simultaneous_import_export`, an
informational count of intervals where both offtake and injection exceed the
material threshold. That field is not a fatal entry and not a warning.

Warnings and the user's acknowledgement must be written to the run metadata.

## Period discovery

Coverage is based on usable interval data, not filenames or raw first/last rows.
The resolver must offer only continuous common periods required by the selected
scenario.

For the supplied sample files:

- Common non-null measured coverage is 2023-11-08 through 2025-10-26 local.
- Calendar year 2024 contains all 35,136 physical quarter-hours.
- The 96 intervals on 2024-10-02 are non-null but `Ongevalideerd`.
- 2023 and 2025 are partial years.

The GUI should therefore offer 2024 as a complete year with an unvalidated-day
warning, plus the partial 2023 and 2025 windows. If no complete calendar year is
available but at least twelve continuous months exist, a rolling twelve-month
window may be offered and must be labelled as such.

## Dependency-aware overlap

- Measured-PV physical and fixed-tariff runs depend only on the three Fluvius
  series.
- The dynamic-injection scenario additionally requires the project day-ahead
  price dataset to cover the selected period.
- Synthetic regional PV is outside the current data contract and no Elia PV
  profile is distributed with the simulator.

## Run-specific normalized input

Each run stores `normalized_input.parquet` containing only the selected period
and canonical input columns actually used by that run. For a measured-PV run,
this is the normalized Fluvius data. It does not duplicate the complete
project-level market parquet.
