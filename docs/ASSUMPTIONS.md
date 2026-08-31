# Assumptions and Decisions

This file records modelling decisions that must be visible in every run. Update
it when a decision changes; do not silently change defaults in code.

### Ownership and settlement

- Energent owns the PV installation and battery.
- The battery may charge only from PV that would otherwise be exported.
- The battery may discharge only to onsite customer demand that would otherwise
  be supplied from the grid.
- Battery discharge may not be exported.
- Direct and battery-delivered PV are both sold to the customer at the same
  configurable rate.
- Customer billing is based on delivered AC energy. Charge and discharge losses
  are Energent's responsibility.

### Default tariffs

- Customer PV sale: EUR 130/MWh.
- Peak-period injection from 08:00 inclusive to 20:00 exclusive: EUR 60/MWh.
- Off-peak injection outside those hours: EUR 30/MWh.
- `weekends_offpeak` defaults to `true`, making every Saturday and Sunday
  interval off-peak. When it is `false`, the configured peak hours also apply
  on weekends.
- Tariff classification uses the interval start in Europe/Brussels local time.
- Both rates, the peak-period start/end times, and `weekends_offpeak` are
  user-configurable. Rates are stated in EUR/MWh excluding any unmodelled taxes,
  levies, guarantees of origin, or certificates.

### Physical model

- Resolution is one physical quarter-hour represented in UTC.
- Battery power is an AC-side limit in kW.
- Battery capacity is user-facing usable stored energy in kWh.
- Charge and discharge efficiencies are separate and configurable.
- Default initial state of charge is 0 kWh. Standalone expert commands may
  accept an explicit non-zero starting value.
- Optimized cases require terminal state of charge equal to initial
  state of charge.
- The pre-optimization simple reference controller is the documented
  exception: it starts empty by default and reports its terminal state without
  assigning value to stored energy that remains undelivered.
- No standby loss, auxiliary load, minimum power, start-up cost, or ramp-rate
  limitation is included in version 1.
- Optimized cases use the complete selected period in advance. Reports label
  them as best-case results, not forecasts.

### Metering boundary

- Grid offtake and injection describe the same site connection point.
- The selected `Productie Actief` series describes PV production behind that
  connection point.
- The only material onsite generator is the selected PV installation.
- Gross load is reconstructed as PV production plus grid offtake minus grid
  injection.
- Fluvius offtake and injection are directional energy totals over a
  quarter-hour, not instantaneous power. Simultaneous import and export in one
  interval is expected when the site changes direction and does not require
  acknowledgement.
- EAN values identify individual meters and are not used to match the three
  files.

### Data quality

- `Gevalideerd` non-null readings are accepted.
- `Ongevalideerd` non-null readings may be used only with a visible warning and
  recorded acknowledgement.
- `Geen gegevens`, blank volumes, ambiguous register selection, and unresolved
  gaps are unavailable data, never zero consumption or production.
- The supplied sample has one complete but unvalidated day on 2024-10-02.

### Reporting

- The unified comparison requires initial state of charge 0 kWh for every
  case so energy carried in from before the selected period is not counted as
  additional PV. A non-zero comparison setting is rejected rather than silently
  replaced.
- Customer value is reported physically in kWh and kW until customer tariffs
  are available.
- No financial value is assigned to peak reduction in version 1.
- Energent revenue excludes battery CAPEX, OPEX, degradation, and financing.
- Equivalent full cycles and throughput are reported, and an annual equivalent
  full-cycle limit constrains every battery-equipped case after proration.
- Equivalent full cycles are not a degradation model. Depth of discharge,
  temperature, C-rate, time at high charge, and calendar ageing remain outside
  the current model.

## Deliberately unresolved until a later phase

- Customer import-energy and demand tariffs.
- Whether and when grid charging is commercially beneficial.
- Battery degradation model and cycle cost.
- Synthetic or modelled PV production in place of the measured PV series.
- Market access, aggregator fees, imbalance settlement, and reserve products.
