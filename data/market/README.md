# Belgian day-ahead price input

`da_prices_qh.parquet` is the standard project-level price input for the
**Dynamic injection tariff** comparison case.

It contains Belgian day-ahead wholesale prices on a continuous physical
15-minute UTC index. The UTC timestamps avoid ambiguity during Belgian clock
changes.

## Columns

- `datetime_utc`: timezone-aware UTC interval start.
- `da_price_eur_mwh`: Belgian day-ahead energy price in EUR/MWh. Negative
  prices are valid.
- `native_resolution`: `PT60M` or `PT15M` source market-time-unit duration.
- `upsampled_from_hourly`: whether the hourly price was repeated over four
  quarter-hours.
- `source_file`: original ENTSO-E GUI Energy Prices CSV filename.

Before Belgian delivery day 1 October 2025, the source prices are hourly and
each value is repeated over its four quarter-hours. From that date, the source
uses native 15-minute prices. Repetition is a time alignment step, not
interpolation.

The file covers 4 January 2015 at 23:00 UTC through 13 July 2026 at 21:45 UTC.
Calendar year 2024 is complete with 35,136 quarter-hours.

`MANIFEST.json` records the source vintage, transformation, validation results,
coverage, columns, and SHA-256 hash. A simulation should record this reference
hash and store the selected aligned prices in its audit artifacts. It should
not copy the complete project-level Parquet into every run folder.

The price column is a wholesale day-ahead reference. It does not include a
supplier margin, imbalance fee, tax, multiplier, or another contract-specific
adjustment.
