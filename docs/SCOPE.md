# Scope

## Objective

Build an auditable upper-bound simulator for an Energent-owned battery located
behind the same connection point as an Energent-owned PV installation and a
business customer's load.

For a selected period and battery size, the simulator must answer:

- How much additional PV energy can be usefully delivered to the customer?
- How much can grid offtake and quarter-hourly grid-import peaks be reduced?
- How much does Energent's PV revenue change?
- What battery dispatch produced those results?

This must be compared to the current baseline of the customer without a battery.

Results are simulations under stated assumptions, not operational forecasts or
customer bill calculations.

## Commercial boundary

- Energent owns both the PV installation and the battery.
- Direct PV energy delivered to the customer is sold at a configurable tariff,
  initially EUR 130/MWh.
- PV stored in the battery and later delivered to the customer is sold at the
  same tariff.
- The customer is billed only for AC energy actually delivered from the battery.
- Battery conversion losses are borne by Energent and are never billed to the
  customer.
- Remaining PV export is remunerated using a configurable local-time schedule,
  initially EUR 60/MWh on weekdays from 08:00 to 20:00 and EUR 30/MWh at night
  and throughout weekends.
- The peak-period start and end times are configurable. Treating all weekend
  intervals as off-peak is a configurable boolean option and is enabled by
  default.
- The customer's import tariff is unknown. Customer benefits are therefore
  reported in kWh and kW, not euros.

## Current comparison cases

Every unified comparison starts from the same no-battery baseline and produces
five additional cases: one diagnostic rule-based controller and four
battery-optimization cases.

### No battery

Keep the measured baseline without battery dispatch.

### Rule-based control

Approximate a chronological real-world controller without foresight. This is a
diagnostic reference, not a best-case optimized result.

### Self-consumption first

Maximize additional useful PV energy delivered to the customer. Among schedules
with the same useful delivery, prefer a lower annual grid-import peak and then
a lower sum of monthly peaks.

### Peak reduction first

Minimize the annual maximum quarter-hour grid-import power. Among schedules
with the same annual maximum, minimize the sum of monthly maxima, then
maximize useful PV delivery.

This remains PV-only peak shaving. It is not the theoretical peak reduction
that could be achieved by charging from the grid. The battery should only charge from PV.

### Revenue maximisation

Preserve the Self-consumption customer-delivery schedule interval by interval,
then use remaining battery flexibility to maximize grid-injection revenue at
the configured fixed peak/off-peak tariff. Among equally valuable remaining
schedules, minimize stored-energy throughput.

### Dynamic injection tariff

Preserve the same Self-consumption customer-delivery schedule, then use
remaining battery flexibility to maximize grid-injection revenue at aligned
quarter-hourly day-ahead prices. The financial comparison remains against the
fixed-tariff no-battery baseline, so it does not isolate the battery's value
from the tariff change.

## Version 1 inputs

- Fluvius quarter-hour grid offtake export.
- Fluvius quarter-hour grid injection export.
- Fluvius quarter-hour PV production submeter export.
- Battery usable energy capacity, AC charge/discharge power, charge efficiency,
  discharge efficiency, and initial state of charge.
- Customer-sale and injection tariffs.
- Standard or explicitly selected quarter-hourly day-ahead injection prices.
- Maximum equivalent full cycles per year.
- Estimated battery cost per usable kWh for simple-payback screening.
- Simulation period selected from validated overlap.

The supported route uses measured PV. Synthetic regional PV is outside the
current scope, and no Elia PV profile is distributed with the simulator.

## Version 1 outputs

- Baseline and battery grid import/export energy.
- Direct, battery-delivered, and total useful PV supplied to the customer.
- Gross PV retained onsite, battery losses, and PV exported.
- Self-consumption and self-sufficiency metrics with explicit definitions.
- Annual and monthly quarter-hour grid-import peaks.
- Battery charge, discharge, state of charge, throughput, and equivalent full
  cycles.
- Energent revenue before and after the battery, split by customer sales and
  grid export.
- Estimated battery CAPEX and simple payback based on the configured EUR/kWh
  assumption and simulated Energent PV-revenue increase.
- Full quarter-hour dispatch and deterministic representative-week plots.
- Input-quality, configuration, and data-provenance records.
- A revenue-based battery-size sweep with payback, revenue, peak and cycle
  screening across a finite candidate grid.

## Explicit non-goals

- Grid charging.
- Battery export that reduces customer PV supply. Revenue maximisation and the
  dynamic injection tariff may inject remaining stored PV only after the
  customer-first schedule is preserved.
- Customer bill savings in euros.
- Low-voltage or medium-voltage tariff engines.
- Customer-import optimisation at day-ahead, profile-contract or other
  customer offtake prices.
- FCR, aFRR, mFRR or intraday market participation.
- A complete business case, vendor quotation, OPEX, financing, degradation,
  NPV, IRR or discounted payback. The current CAPEX and simple-payback figures
  are screening calculations from a configurable EUR/kWh assumption only.
- Battery degradation cost or lifetime optimization.
- Rolling-horizon control or forecasting.
- Multi-battery, phase-level, or transformer thermal models.
- Reuse of the PHS optimizer or market-specific model classes.

## Planned extensions

- Grid charging once customer import-energy and peak tariffs are known.
- Customer financial settlement.
- Additional intraday and balancing-market modules, implemented individually.
- Rolling-horizon dispatch and forecast error.
