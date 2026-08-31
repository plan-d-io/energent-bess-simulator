# Model Specification

## Resolution and baseline

For every physical quarter-hour `t`, let `dt = 0.25 h` and define:

- `pv[t]`: measured PV production in kWh.
- `load[t]`: gross customer load in kWh.
- `g_import_0[t]`: measured baseline grid offtake in kWh.
- `g_export_0[t]`: measured baseline grid injection in kWh.

The baseline identity is:

```text
load[t] + g_export_0[t] = pv[t] + g_import_0[t]
```

Direct PV delivered to the customer before the battery is:

```text
pv_direct[t] = pv[t] - g_export_0[t]
```

This identity assumes no material onsite generator other than the selected PV
installation.

## Battery parameters

- `E_usable`: usable stored-energy capacity in kWh.
- `P_charge`: maximum AC charging power in kW.
- `P_discharge`: maximum AC discharging power in kW.
- `eta_charge`: AC-to-stored charging efficiency in `(0, 1]`.
- `eta_discharge`: stored-to-AC discharging efficiency in `(0, 1]`.
- `soc_initial`: stored energy at the start of the period in kWh.

The initial user interface may offer one symmetric power input while the model
keeps separate charge and discharge parameters.

## Decision variables

- `charge_pv[t]`: surplus PV taken from the AC bus to charge the battery, kWh.
- `discharge_load[t]`: AC energy delivered from the battery to customer load,
  kWh.
- `discharge_grid[t]`: optional AC energy injected from the battery into the
  grid after the customer-first schedule is preserved, kWh. It is zero in the
  Self-consumption and Peak reduction cases.
- `soc[t]`: stored energy at the start of interval `t`, kWh.
- `g_import[t]`: grid offtake with the battery, kWh.
- `g_export[t]`: grid injection with the battery, kWh.

## Physical constraints

PV-only charging and load-only discharge are enforced structurally:

```text
0 <= charge_pv[t] <= g_export_0[t]
0 <= charge_pv[t] <= P_charge * dt

0 <= discharge_load[t] <= g_import_0[t]
0 <= discharge_load[t] <= P_discharge * dt

g_import[t] = g_import_0[t] - discharge_load[t]
g_export[t] = g_export_0[t] - charge_pv[t] + discharge_grid[t]
```

The customer-first physical LP has `discharge_grid[t] = 0`. Revenue
maximisation and Dynamic injection tariff solve that customer-first schedule
first, freeze `discharge_load[t]` interval by interval, and may then choose a
positive `discharge_grid[t]` only where the preserved schedule leaves no
material final import.

In those export LPs, grid-directed discharge is limited by the remaining
discharge-power allowance:

```text
0 <= discharge_grid[t]
discharge_load[t] + discharge_grid[t] <= P_discharge * dt
```

Real Fluvius quarter-hours can contain both material import and export when the
site reverses direction within the interval. The sample contains 717 such
intervals in 2024. The source values remain separate; they must not be netted or
discarded during normalization.

If a dispatch uses charge and discharge within the same quarter-hour, the
battery must time-share its inverter rather than operate in both directions at
once:

```text
charge_pv[t] / P_charge + discharge_load[t] / P_discharge <= dt
```

For the fixed and dynamic export LPs, replace `discharge_load[t]` in this
constraint with `discharge_load[t] + discharge_grid[t]`.

This constraint is applied when both power ratings are positive. A zero power
rating forces its corresponding energy flow to zero. The time-sharing
formulation permits sequential direction changes inside a quarter-hour, which
is appropriate for a stated upper bound, while preventing a full quarter-hour
of charging and a full quarter-hour of discharging from being claimed in the
same interval. The unknown intra-quarter-hour ordering remains an explicit
upper-bound assumption.

The state transition is:

```text
total_discharge[t] = discharge_load[t] + discharge_grid[t]

soc[t+1] = soc[t]
         + eta_charge * charge_pv[t]
         - total_discharge[t] / eta_discharge
```

with:

```text
0 <= soc[t] <= E_usable
soc[0] = soc_initial
soc[T] = soc_initial
```

The terminal constraint prevents free energy at the end of the simulation.
The chosen starting state represents energy carried into the period and is
reported explicitly.

The shared inverter-time constraint is linear, so the model does not need
binary charge-state variables. More than one schedule may produce the same
best result; this does not change the reported energy or peak totals.

## Losses and customer billing

Charge loss, discharge loss, and total conversion loss are:

```text
charge_loss[t] = charge_pv[t] * (1 - eta_charge)
discharge_loss[t] = total_discharge[t] / eta_discharge - total_discharge[t]
total_loss = sum(charge_loss[t] + discharge_loss[t])
```

Stored-energy throughput and equivalent full cycles are:

```text
stored_throughput = sum(
    eta_charge * charge_pv[t]
    + total_discharge[t] / eta_discharge
)
equivalent_full_cycles = stored_throughput / (2 * E_usable)
```

Equivalent full cycles are zero when usable capacity is zero. This throughput
definition counts a complete charge and discharge of the usable capacity as one
cycle and counts an unmatched ending charge as half-cycle throughput.

Every battery-equipped case also has a hard annual throughput budget:

```text
year_fraction = sum_t interval_hours[t] / hours_in_local_calendar_year(t)
allowed_throughput = 2 * E_usable * max_equivalent_full_cycles_per_year * year_fraction
stored_throughput <= allowed_throughput
```

`max_equivalent_full_cycles_per_year` defaults to 400. A complete local
calendar year, including leap year 2024, has `year_fraction = 1.0`.

The fixed-tariff Revenue maximisation and Dynamic injection tariff cases use
the second AC discharge destination `discharge_grid[t]`. Total AC discharge
enters SoC, inverter time-sharing, losses, and the cycle budget.
`discharge_load[t]` remains the only additional billable customer energy.
The fixed case values grid-directed discharge at the configured peak/off-peak
injection tariff; the dynamic case values it at the aligned quarter-hourly
day-ahead injection price.

Only `discharge_load[t]` is additional billable energy delivered to the
customer. Neither charging energy nor conversion losses are billed.

## PV and grid metrics

Report both the conventional gross and the useful definitions:

```text
total_pv_production = sum(pv[t])
additional_useful_pv = sum(discharge_load[t])

gross_pv_retained_onsite = sum(pv[t] - g_export[t])
useful_pv_delivered = sum(pv_direct[t] + discharge_load[t])

gross_self_consumption_ratio = gross_pv_retained_onsite / sum(pv[t])
useful_self_consumption_ratio = useful_pv_delivered / sum(pv[t])
self_sufficiency_ratio = useful_pv_delivered / sum(load[t])
additional_useful_pv_share = additional_useful_pv / sum(pv[t])
```

The difference between gross PV retained onsite and useful PV delivered is
principally battery conversion loss. The main summary must use the useful definition;
the gross definition is retained for comparison with common industry usage. The main
summary must also show total PV production, the useful self-consumption ratio before
and after the battery, the change in percentage points, and additional useful PV as a
percentage of total PV production. A zero denominator is reported as not applicable,
never as infinity or zero by assumption.

Quarter-hour grid-import power is:

```text
grid_import_kw[t] = g_import[t] / dt
```

Report the maximum for the complete selected period and separately for every
local calendar month in Europe/Brussels.

A monthly peak is the highest average grid-import power recorded during a
15-minute interval in that local calendar month. The selected-period peak
(`annual_peak_kw`) is the single highest 15-minute interval in the selected
data. Its reduction against the no-battery case does not imply the same
reduction in every month. A physical peak reduction is not a customer euro
saving; customer demand tariffs are not modelled.

Report selected-period peak reduction against the no-battery baseline in both
kW and percent:

```text
annual_peak_reduction_kw = annual_peak_0_kw - annual_peak_kw
annual_peak_reduction_pct = 100 * annual_peak_reduction_kw / annual_peak_0_kw
```

If the no-battery selected-period peak is zero, report the percentage as not
applicable.

Also report the average of monthly peaks, using only complete local calendar
months:

```text
average_monthly_peak_kw = mean(monthly_peak_kw for complete local months)
average_monthly_peak_reduction_kw =
    baseline_average_monthly_peak_kw - average_monthly_peak_kw
average_monthly_peak_reduction_pct =
    100 * average_monthly_peak_reduction_kw / baseline_average_monthly_peak_kw
```

A month is complete only when the selected physical intervals cover local month
start through the next local month start, without gaps. Completeness uses those
timezone-aware bounds. Do not assume every month contains `days * 96` intervals:
the March and October clock changes change the physical quarter-hour count.
Partial months remain in the monthly results but are excluded from this average.
If no complete month is present, the average and its reduction are not
applicable. If the no-battery average monthly peak is zero, the percentage is
not applicable.

The technical sum of monthly peaks remains available for compatibility. It is
not energy and is not the main user-facing peak result.

Monthly energy, battery, and Energent PV revenue totals in `monthly_summary.csv`
are calculated from that month's intervals. Monthly percentages use that month's
summed numerator and denominator; they are not averages of interval percentages.
Additive monthly values must sum to the selected-period totals. Energent PV
revenue includes PV energy sold to the customer and PV injected into the grid.
It is not profit, customer bill savings, NPV, or a complete business case.

## Energent revenue

Let `r_customer` be the customer PV-sale rate in EUR/MWh and `r_export[t]` the
local-time grid-injection rate. Convert kWh to MWh before applying either rate.

For the initial tariff schedule, `r_export[t]` is the configured peak rate from
08:00 inclusive to 20:00 exclusive and the configured off-peak rate at all
other times. Classification uses the interval start in Europe/Brussels local
time. Both rates and the peak-period boundaries are configurable. When
`weekends_offpeak` is `true` (the default), every Saturday and Sunday interval
uses the off-peak rate. When it is `false`, the same configured peak hours apply
on weekends.

Baseline revenue is:

```text
R_0 = sum(
    r_customer * pv_direct[t]
    + r_export[t] * g_export_0[t]
) / 1000
```

Battery-case revenue is:

```text
R_battery = sum(
    r_customer * (pv_direct[t] + discharge_load[t])
    + r_export[t] * g_export[t]
) / 1000
```

Therefore:

```text
revenue_uplift = sum(
    r_customer * discharge_load[t]
    + r_export[t] * discharge_grid[t]
    - r_export[t] * charge_pv[t]
) / 1000
```

Losses are automatically borne by Energent because discharged billable energy
is smaller than charging energy. No separate customer charge is created for the
losses.

## Dispatch objectives

The model applies the objectives below in order. A later step may choose among
schedules that achieved the same earlier result, but it may not trade away that
earlier result.

### Self-consumption first

1. Maximize `sum(discharge_load[t])`.
2. Minimize the annual maximum `grid_import_kw` without worsening step 1.
3. Minimize the sum of monthly maximum `grid_import_kw`.

### Peak reduction first

1. Minimize the annual maximum `grid_import_kw`.
2. Minimize the sum of monthly maximum `grid_import_kw`.
3. Maximize `sum(discharge_load[t])`.

### Revenue maximisation

1. Solve the complete Self-consumption priority order above.
2. Freeze `discharge_load[t]` from that customer-first result interval by
   interval.
3. With the remaining battery flexibility, maximize
   `sum(r_export[t] * (discharge_grid[t] - charge_pv[t])) / 1000`.
4. Minimize stored-energy throughput among schedules that retain the best
   fixed-tariff revenue result.

### Dynamic injection tariff

1. Solve the complete Self-consumption priority order above.
2. Freeze `discharge_load[t]` from that customer-first result interval by
   interval.
3. With the remaining battery flexibility, maximize
   `sum(day_ahead_price[t] * (discharge_grid[t] - charge_pv[t])) / 1000`.
4. Minimize stored-energy throughput among schedules that retain the best
   dynamic-injection revenue result.

Both revenue cases remain PV-only: they never charge from the grid. Their
grid-directed battery discharge may turn an originally importing interval
into a final exporting interval only after the preserved customer demand is
fully covered. No interval may retain material final import while also using
positive battery grid discharge.

At every step, the solver must keep the earlier result unchanged apart from a
small recorded allowance for numerical rounding.

## Simple reference controller

Before optimization is introduced, a simple controller provides a check on the
battery physics. It works through the data in time order and does not know what
happens later. It is not one of the four best-case optimized results.

For this controller only, simultaneous measured import and export are treated
conservatively:

```text
net_export_available[t] = max(g_export_0[t] - g_import_0[t], 0)
net_import_need[t] = max(g_import_0[t] - g_export_0[t], 0)
```

The controller charges from `net_export_available` or discharges to
`net_import_need`, never both in the same interval. The matched counterflow is
left unchanged. It processes intervals chronologically without forecasts,
starts empty by default, and reports its terminal state of charge rather than
manufacturing a terminal discharge. Consequently it is a conservative check,
not the best-case self-consumption result. The optimized cases retain the
initial-equals-terminal requirement above.

## Interpretation

All four optimized cases use the complete selected period in advance. They are
best-case results, not forecasts of real operation. Reports must state this in
plain language.
