# Configuration

## Three input layers

The primary unified comparison (`btm-compare`) resolves settings from three
layers, in this order:

```text
explicit CLI value > run configuration TOML > configs/defaults.toml
```

The end-to-end command (`btm-run` / `python -m btm_sim.run`) uses the same
precedence when it builds a frozen request from Fluvius files. After freeze,
the worker uses the stored effective values; it does not re-merge
`defaults.toml`. See [`docs/RUN.md`](RUN.md).

Those files have different jobs:

- **Central defaults** (`configs/defaults.toml`) hold reusable battery starting
  values, Energent tariffs, peak/off-peak classification, reporting choices,
  and the shared estimated battery cost. They must not contain input or output
  paths.
- A **run configuration** (optional `--config` TOML) holds that run's input and
  output paths and may override any reusable value for that run only.
- **Explicit CLI flags** override both TOML layers.

New runs start with values from `configs/defaults.toml`. A run configuration or
explicit command-line value can override them. The audit folder records every
effective value and its source. Editing the central defaults file affects
future runs, not already completed audit folders.

Do not call both files simply “the config”: that hides their different
purposes.

The Python library APIs (`optimize_*`, `run_reference_controller`,
`run_comparison`) stay filesystem-independent. They use the configuration
objects the caller passes. They do not read `defaults.toml` themselves.

## Central defaults

The project's standard file is `configs/defaults.toml`. `btm-compare` loads it
automatically from the application/project location, not from whichever working
directory launched Python. `--defaults PATH` selects another file; a relative
path then resolves against the working directory.

The central file may contain only:

```text
[battery]
[tariffs]
[reporting]
[economics]
[sweep]
```

`[input]` or `[output]` in a defaults file is an error, so a new site cannot
accidentally reuse Ganda Cars paths. Unknown sections and keys are errors.
Every reusable key listed below must be present; missing values do not fall
through to a second hidden set of user-facing defaults.

Streamlit should import `load_central_defaults` and `standard_defaults_path`
from `btm_sim.config`. It must not parse TOML or duplicate this validation.

## Run configuration

`configs/example.toml` is a generic run-configuration template. Copy it, point
its `[input]` and `[output]` paths at a local run, and uncomment only the values
that should override the central defaults.

Paths inside a run configuration resolve relative to that file. Paths supplied
on the CLI resolve relative to the working directory.

TOML is read with the Python 3.13 standard library `tomllib`. The program
validates the resolved configuration before starting the optimizer.

## Precedence example

If the central defaults say `usable_energy_kwh = 100`, a run configuration that
sets only `battery.usable_energy_kwh = 150` keeps the central charge power,
efficiencies, tariffs, and reporting values. `--e-usable 80` then replaces
only the energy for that invocation. `--power 75` sets both charge and
discharge power unless a more specific `--p-charge` or `--p-discharge` is also
given.

The unified comparison still requires an effective initial charge of 0 kWh.
A non-zero value is rejected no matter which layer supplied it. A standalone
optimized run may explicitly choose another value but also defaults to 0 kWh.

The initial tariff model supports one peak export rate and one off-peak export
rate. The peak interval must start and end on the same local day; its start is
inclusive and its end is exclusive. When `weekends_offpeak = true`, every
Saturday and Sunday interval is off-peak. When it is `false`, the configured
peak hours apply on weekends too. Public holidays are not treated as weekends
in version 1.

## Keys, units, ranges, and CLI flags

Required for a comparison run means the value must exist after the three layers
are merged. Central defaults currently supply every reusable key. Input and
output still come from the run configuration or the CLI.

Battery power may be given as the pair of charge and discharge ratings, or as
`--power` / a pair of equal TOML values.

| Key | Unit | Purpose | Range | CLI |
| --- | --- | --- | --- | --- |
| `input.normalized_parquet` | path | Selected canonical parquet | existing file | positional `input` |
| `input.validation_report` | path | Optional validation report | existing file if set | `--validation-report` |
| `output.root` | path | Timestamped run folder parent | exclusive with directory | `--output-root` |
| `output.directory` | path | Exact run folder | exclusive with root | `-o` / `--output-dir` |
| `battery.usable_energy_kwh` | kWh | Usable stored energy | >= 0, finite | `--e-usable` |
| `battery.charge_power_kw` | kW | Maximum AC charge power | >= 0, finite | `--p-charge` or `--power` |
| `battery.discharge_power_kw` | kW | Maximum AC discharge power | >= 0, finite | `--p-discharge` or `--power` |
| `battery.charge_efficiency` | 1 | AC-to-stored efficiency | (0, 1], finite | `--eta-charge` |
| `battery.discharge_efficiency` | 1 | Stored-to-AC efficiency | (0, 1], finite | `--eta-discharge` |
| `battery.initial_charge_kwh` | kWh | Initial stored energy | 0..usable, finite; comparison must be 0 | `--soc-initial` |
| `battery.max_equivalent_full_cycles_per_year` | 1/year | Maximum equivalent full cycles; prorated by the selected period's share of local calendar years | >= 0, finite | `--max-equivalent-full-cycles-per-year` |
| `input.dynamic_injection_prices` | path | Compatible day-ahead price Parquet | existing file if set; otherwise `data/market/da_prices_qh.parquet` | `--dynamic-injection-prices` |
| `tariffs.customer_sale_eur_per_mwh` | EUR/MWh | Customer PV-sale rate | >= 0, finite | `--customer-rate` |
| `tariffs.peak_export_eur_per_mwh` | EUR/MWh | Peak-period export rate | >= 0, finite | `--export-peak-rate` |
| `tariffs.offpeak_export_eur_per_mwh` | EUR/MWh | Off-peak export rate | >= 0, finite | `--export-offpeak-rate` |
| `tariffs.peak_start_local` | HH:MM | Peak window start, inclusive | earlier than end, same day | `--peak-start` |
| `tariffs.peak_end_local` | HH:MM | Peak window end, exclusive | later than start, same day | `--peak-end` |
| `tariffs.weekends_offpeak` | bool | Weekend intervals off-peak | true/false | `--weekends-offpeak` / `--no-weekends-offpeak` |
| `tariffs.timezone` | IANA name | Local time for classification | installed zoneinfo | `--timezone` |
| `reporting.seasonal_plots` | bool | Write fixed seasonal plots | true/false | `--seasonal-plots` / `--no-seasonal-plots` |
| `reporting.winter_iso_week` | ISO week | Winter plot week | 1..53 | `--winter-iso-week` |
| `reporting.spring_iso_week` | ISO week | Spring plot week | 1..53 | `--spring-iso-week` |
| `reporting.summer_iso_week` | ISO week | Summer plot week | 1..53 | `--summer-iso-week` |
| `reporting.autumn_iso_week` | ISO week | Autumn plot week | 1..53 | `--autumn-iso-week` |
| `economics.estimated_battery_cost_eur_per_kwh` | EUR/kWh usable | Shared estimated battery cost for one-battery comparison and the revenue size sweep | > 0, finite | `--estimated-battery-cost-eur-per-kwh` / `--estimated-battery-cost` |
| `sweep.evaluation_period_years` | year | Screening horizon for estimated value (sweep only) | > 0, finite | `--evaluation-period-years` |
| `sweep.default_durations_hours` | hour | Default automatic/manual durations | unique, positive, non-empty | `--durations` / `--duration` |
| `sweep.revenue_capture_threshold_pct` | % | Share of a duration's max annual uplift used for the capture point | (0, 100] | `--revenue-capture-threshold` |

`--help` on `btm-compare` and `btm-run` states that reusable starting values come from the
selected central defaults file. It does not bake the current numbers into help
text. `--defaults` may show the resolved standard path. Standalone commands
`btm-self-consumption`, `btm-peak-reduction`, and `btm-revenue` keep their
direct-argument form. The end-to-end command is documented in
[`docs/RUN.md`](RUN.md). The revenue size sweep is documented in
[`docs/SWEEP.md`](SWEEP.md). `[economics]` and `[sweep]` are required in
central defaults. The estimated battery cost is shared by ordinary six-case
comparisons and the revenue size sweep. A run configuration may still set the
deprecated `[sweep].estimated_battery_cost_eur_per_kwh` alias; conflicting
values in both locations are rejected. `evaluation_period_years` remains
sweep-only and is not used for simple payback.

## CLI and Python use

```powershell
python -m btm_sim.compare normalized_input.parquet --output-root outputs
```

```powershell
python -m btm_sim.compare --config configs\example.toml
```

```powershell
python -m btm_sim.compare normalized_input.parquet --output-root outputs `
  --e-usable 150 --power 75
```

The first command is valid because reusable values come from
`configs/defaults.toml`. The third uses the central tariffs, efficiencies, and
reporting choices but overrides battery energy and power.

`--config` remains optional. It is a run configuration, not another name for
the central defaults file. Argparse defaults on `btm-compare` are `None` so
they cannot overwrite TOML values.

The Python API accepts `BatteryConfig` plus optional `TariffConfig`,
`ReportingConfig`, and `EconomicsConfig`, or a resolved `SimulationConfig` via
`run_comparison_from_resolved`. Public defaults helpers:

```python
from btm_sim.config import EconomicsConfig, load_central_defaults, standard_defaults_path
```

## Audit record

Every successful unified run writes `resolved_config.json` containing the
effective configuration, the central defaults path and SHA-256 hash, the run
configuration path and hash when used, explicit CLI override keys, and a
source for each effective value (`defaults_toml`, `run_toml`, or `cli`). The
central defaults file used for the run is copied as `source_defaults.toml`. A
supplied run configuration is copied as `source_config.toml`. Both appear in
`run_metadata.json`. That lets a later reviewer reproduce a run after the
central defaults file has changed.

Every new unified comparison contains six cases: no-battery, simple reference,
best-case self-consumption, best-case peak-reduction, best-case fixed-tariff
Energent PV revenue, and dynamic injection tariff. The financial baseline
remains the fixed-tariff no-battery case. The standard day-ahead price file is
`data/market/da_prices_qh.parquet`; do not put that path in central defaults.
An expert override selects a compatible replacement Parquet. Missing, invalid,
or incomplete prices fail before optimization; the run does not fall back to five
cases. Artifact schema version 2 records the six-case contract, cycle-limit
fields, and `dynamic_injection_prices.parquet` (aligned selected-period prices
only).

The annual cycle limit is a required battery default. Each interval contributes
its physical hours divided by the physical hours of its Europe/Brussels
calendar year. A complete calendar year, including leap year 2024, has year
fraction 1.0. The stored-energy throughput budget is
`2 * usable_energy_kwh * max_equivalent_full_cycles_per_year * year_fraction`.
If an unconstrained schedule already respects that budget, that schedule is
kept so a redundant constraint cannot move an equally optimal vertex.
Equivalent full cycles remain a reported result as well as this hard limit.
Battery degradation cost, rainflow, and warranty modelling stay deferred.

Revenue maximisation first preserves the Self-consumption customer-delivery
schedule, then may inject remaining stored PV at the configured fixed peak and
off-peak injection tariff when that shift is worthwhile. Grid charging remains
forbidden.
