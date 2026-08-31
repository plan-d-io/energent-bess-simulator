# Revenue battery-size sweep

The sweep compares a finite set of battery sizes for one site and period using
only the existing fixed-tariff **Revenue maximisation** optimizer. It is a
screening tool. It is not NPV, profit, or an investment decision.

The six-case comparison remains the detailed follow-up for a size selected
from the sweep. Streamlit is not part of this command.

## Python API

Callers should use these public names. Do not reconstruct the request JSON by
hand.

```python
from btm_sim.sweep import (
    analyse_site,
    inspect_selected_period,
    preflight_sweep_candidates,
    build_sweep_request,
    write_sweep_request,
    load_sweep_request,
    run_revenue_sweep,
    run_sweep_end_to_end,
)
from btm_sim.config import SweepConfig, load_central_defaults
```

`inspect_selected_period` is the read-only UI inspection API. It reuses the
same Fluvius ingestion and selected-period materialization as
`preflight_sweep_candidates` and returns a serializable
`SelectedPeriodInspection` instead of raising. Use it when a caller must
distinguish an acknowledgeable site-boundary issue (`NEGATIVE_LOAD` and/or
`EXPORT_EXCEEDS_PV`) from other validation failures. When acknowledgement is
true, the same raw meter values are retained and the site-boundary issues
become warnings.

`preflight_sweep_candidates` still returns `SiteAnalysis` for existing callers.
If materialization fails it raises `SweepRequestError` with structured
`issues` and `details` (including
`requires_site_boundary_acknowledgement`); do not parse the exception message.

`build_sweep_request` resolves settings with the established precedence,
hashes the three Fluvius files, freezes the candidate list and site analysis,
and returns `SweepRequest`. After freeze the worker uses that list; it does
not silently generate a different one.

`run_revenue_sweep` calculates the no-battery baseline once, then calls
`optimize_revenue` once per frozen candidate.

## Ordinary CLI

```powershell
python -m btm_sim.sweep offtake.csv injection.csv pv.csv `
  --period 2024 `
  --output-dir outputs\my_sweep
```

`btm-sweep` is the same command. Roles are detected from `Register`.
`--period` and `--output-dir` are required on this path. Acknowledgements
match `btm-normalize`. Battery, tariff, cost, evaluation-period, and
duration flags override central defaults. The command does not load
day-ahead prices.

Candidate modes:

- `--mode automatic` (default): use the site-analysis power grid at the
  requested durations. The grid keeps the main engineering step, rounded
  reference, and one-step upper guard, and also includes every
  `1, 2, 5 × 10^n` value from 5 kW through that main step.
- `--mode manual_range --min-power 25 --max-power 100 --power-increment 25`
- `--mode explicit --candidate 50,100 --candidate 75,300`

Default durations are 2 h and 4 h. `--durations 1,6` or repeated
`--duration` values are advanced options. Explicit candidates may have any
positive derived duration.

## Frozen-request CLI

```powershell
python -m btm_sim.sweep --request path\sweep_request.json
```

Do not combine `--request` with Fluvius files or setting overrides.

## Screening economics

```text
estimated_capex_eur = usable_energy_kwh × EUR/kWh
period_revenue_uplift_eur = candidate revenue − no-battery revenue
annual_revenue_uplift_eur = period_uplift ÷ selected_period_year_fraction
simple_payback_years = capex ÷ annual_uplift   (null if uplift ≤ 0)
estimated_value_eur = annual_uplift × evaluation_years − capex
```

Central defaults are 300 EUR/kWh usable (`economics.estimated_battery_cost_eur_per_kwh`)
and a 10-year evaluation period. The EUR/kWh value is shared with ordinary
one-battery comparisons. The estimate excludes financing, discounting,
degradation, operating costs, inflation, and future tariff changes. Estimated
value is not profit or NPV. Simple payback does not use the 10-year evaluation
period.

The user-facing result is **Suggested battery size** or **Highest estimated
value among the tested sizes**. If every tested size has estimated value at
most zero, the suggestion is no battery. Ties use lower CAPEX, then lower
energy, then lower power, then shorter duration.

A revenue-capture point is also reported per duration: the smallest-power
candidate that reaches the configured percentage of that duration's maximum
annual revenue uplift. That point is not the economic recommendation.

Partial periods keep the same formulas and set
`annualized_from_partial_period = true` with a seasonal-bias warning.

Every sweep summary also stores a payback-focused `screening_summary`. It
identifies the tested candidate with the shortest simple payback, the tested
candidate with the highest annual Energent PV-revenue increase, and whether
any tested candidate pays back within `evaluation_period_years` (the Streamlit
**screening period**). A candidate is within that period when it has an
applicable, finite simple payback `<= evaluation_period_years`. Zero or
negative annual revenue increase means there is no applicable payback. The three stable outcomes are
`one_or_more_candidates_within_screening_period`,
`no_candidate_within_screening_period`, and
`no_candidate_with_positive_annual_revenue`. These selections are not an
economic optimum. The estimated-value recommendation object remains in the
folder for audit compatibility.

Candidate rows include `payback_within_evaluation_period`. Each
`best_per_duration` entry keeps its existing highest-value, highest-revenue,
revenue-capture, and range-boundary fields, and adds the shortest-payback
candidate for that duration.

Every sweep also stores a physical `peak_summary`. It reports average monthly
peak reduction and the reduction in the selected period's highest 15-minute
grid import under the existing Revenue maximisation dispatch. The sweep does
not run a peak-optimised case, and it does not assign financial value, bill
savings, or a revised payback to these kW results. Negative reductions stay
negative. `largest_average_monthly_peak_reduction_candidate` is null when the
period has no complete local calendar month or no tested size has a finite
positive reduction.

`sweep_artifact_schema_version` remains 1. Historical version-1 folders
without `screening_summary` or `peak_summary` stay readable.

## Progress and artifacts

Stage total is `5 + n_candidates` and is stable after the request is frozen.
Status field names match the six-case job files so a later UI can poll them.
The request file is `sweep_request.json`. The last completed message is
`Sweep completed`.

A successful folder contains:

```text
sweep_request.json
run_status.json
run_events.jsonl
run.log
normalized_input.parquet
validation_report.json
sweep_summary.json
sweep_summary.csv
sweep_summary.parquet
site_analysis.json
sweep_metadata.json
resolved_config.json
source_defaults.toml
```

`sweep_artifact_schema_version` is 1. The folder does not contain day-ahead
prices, six-case comparison artifacts, seasonal plots, full dispatch traces,
or copies of the raw Fluvius CSVs.
