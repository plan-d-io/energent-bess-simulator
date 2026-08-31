"""Hand-computable Energent PV settlement identities."""

from datetime import datetime, timezone

import pytest

from btm_sim.battery.config import BatteryConfig
from btm_sim.compare.metrics import attach_baseline_dispatch
from btm_sim.config.schema import TariffConfig
from btm_sim.settlement.ledger import settle_dispatch
from tests.lp_frames import qh_frame

UTC = timezone.utc


def _weekday_frame(rows: list[dict]):
    # Wednesday 2024-01-03 07:00 UTC = 08:00 Brussels (start of peak).
    return qh_frame(rows, start=datetime(2024, 1, 3, 7, 0, tzinfo=UTC))


def test_direct_and_battery_energy_use_the_same_customer_rate():
    frame = _weekday_frame(
        [
            {"imp": 0.0, "exp": 1.0, "pv": 2.0},  # pv_direct 1 kWh, export 1
            {"imp": 1.0, "exp": 0.0, "pv": 0.0},
        ]
    )
    work = attach_baseline_dispatch(frame, BatteryConfig(10, 8, 8, 1.0, 1.0, 0.0))
    work.loc[0, "charge_pv_kwh"] = 1.0
    work.loc[0, "grid_export_kwh"] = 0.0
    work.loc[1, "discharge_load_kwh"] = 1.0
    work.loc[1, "grid_import_kwh"] = 0.0
    settled = settle_dispatch(work, TariffConfig())
    assert settled.ledger["direct_customer_sale_kwh"].iloc[0] == pytest.approx(1.0)
    assert settled.ledger["battery_customer_sale_kwh"].iloc[1] == pytest.approx(1.0)
    assert settled.ledger["direct_customer_sale_eur"].iloc[0] == pytest.approx(130.0 * 1.0 / 1000.0)
    assert settled.ledger["battery_customer_sale_eur"].iloc[1] == pytest.approx(130.0 * 1.0 / 1000.0)


def test_charge_and_losses_are_never_customer_sales():
    frame = _weekday_frame(
        [
            {"imp": 0.0, "exp": 2.0, "pv": 2.0},
            {"imp": 1.0, "exp": 0.0, "pv": 0.0},
        ]
    )
    work = attach_baseline_dispatch(frame, BatteryConfig(10, 8, 8, 0.5, 0.5, 0.0))
    work.loc[0, "charge_pv_kwh"] = 2.0
    work.loc[0, "charge_loss_kwh"] = 1.0
    work.loc[0, "grid_export_kwh"] = 0.0
    work.loc[1, "discharge_load_kwh"] = 0.5
    work.loc[1, "discharge_loss_kwh"] = 0.5
    work.loc[1, "grid_import_kwh"] = 0.5
    settled = settle_dispatch(work, TariffConfig())
    assert settled.totals["battery_customer_sales_mwh"] == pytest.approx(0.0005)
    assert settled.totals["battery_customer_sales_eur"] == pytest.approx(130.0 * 0.5 / 1000.0)
    assert settled.ledger["battery_customer_sale_kwh"].sum() == pytest.approx(0.5)
    assert settled.ledger["direct_customer_sale_kwh"].sum() == pytest.approx(0.0)


def test_foregone_export_uses_charging_interval_rate():
    # Interval 0 is 08:00 peak (60); interval 1 is 08:15 still peak.
    # Use an off-peak first interval: 07:45 = 06:45 UTC.
    frame = qh_frame(
        [
            {"imp": 0.0, "exp": 1.0, "pv": 1.0},
            {"imp": 1.0, "exp": 0.0, "pv": 0.0},
        ],
        start=datetime(2024, 1, 3, 6, 45, tzinfo=UTC),
    )
    work = attach_baseline_dispatch(frame, BatteryConfig(10, 8, 8, 1.0, 1.0, 0.0))
    work.loc[0, "charge_pv_kwh"] = 1.0
    work.loc[0, "grid_export_kwh"] = 0.0
    work.loc[1, "discharge_load_kwh"] = 1.0
    work.loc[1, "grid_import_kwh"] = 0.0
    settled = settle_dispatch(work, TariffConfig())
    assert settled.ledger["tariff_class"].iloc[0] == "offpeak"
    assert settled.totals["foregone_export_eur"] == pytest.approx(30.0 * 1.0 / 1000.0)
    assert settled.totals["extra_customer_sale_eur"] == pytest.approx(130.0 * 1.0 / 1000.0)
    assert settled.totals["uplift_eur"] == pytest.approx((130.0 - 30.0) / 1000.0)


def test_baseline_battery_uplift_and_component_identities():
    frame = qh_frame(
        [
            {"imp": 0.0, "exp": 1.0, "pv": 1.0},
            {"imp": 1.0, "exp": 0.0, "pv": 0.0},
        ],
        start=datetime(2024, 1, 3, 6, 45, tzinfo=UTC),
    )
    baseline = attach_baseline_dispatch(frame, BatteryConfig(10, 8, 8, 1.0, 1.0, 0.0))
    battery = baseline.copy()
    battery.loc[0, "charge_pv_kwh"] = 1.0
    battery.loc[0, "grid_export_kwh"] = 0.0
    battery.loc[1, "discharge_load_kwh"] = 1.0
    battery.loc[1, "grid_import_kwh"] = 0.0
    tariffs = TariffConfig()
    r0 = settle_dispatch(baseline, tariffs).totals
    rb = settle_dispatch(battery, tariffs).totals
    assert r0["total_energent_pv_revenue_eur"] == pytest.approx(30.0 / 1000.0)
    assert rb["total_energent_pv_revenue_eur"] == pytest.approx(130.0 / 1000.0)
    assert rb["uplift_eur"] == pytest.approx(0.10)
    assert rb["revenue_change_eur"] == pytest.approx(0.10)
    assert rb["revenue_change_pct"] == pytest.approx(100.0 * 0.10 / 0.03)
    assert rb["battery_grid_injection_revenue_eur"] == pytest.approx(0.0)
    assert rb["uplift_eur"] == pytest.approx(rb["extra_customer_sale_eur"] - rb["foregone_export_eur"])
    assert rb["baseline_total_energent_pv_revenue_eur"] == pytest.approx(r0["total_energent_pv_revenue_eur"])


def test_battery_grid_injection_enters_uplift_identity():
    frame = qh_frame(
        [
            {"imp": 0.0, "exp": 2.0, "pv": 2.0},
            {"imp": 1.0, "exp": 0.0, "pv": 0.0},
        ],
        start=datetime(2024, 1, 3, 6, 45, tzinfo=UTC),
    )
    battery = attach_baseline_dispatch(frame, BatteryConfig(10, 8, 8, 1.0, 1.0, 0.0))
    battery.loc[0, "charge_pv_kwh"] = 2.0
    battery.loc[0, "grid_export_kwh"] = 0.0
    battery.loc[1, "discharge_load_kwh"] = 1.0
    battery.loc[1, "discharge_grid_kwh"] = 1.0
    battery.loc[1, "grid_import_kwh"] = 0.0
    battery.loc[1, "grid_export_kwh"] = 1.0
    rb = settle_dispatch(battery, TariffConfig()).totals
    extra = 130.0 / 1000.0
    foregone = 30.0 * 2.0 / 1000.0
    injection = 60.0 / 1000.0
    assert rb["extra_customer_sale_eur"] == pytest.approx(extra)
    assert rb["foregone_export_eur"] == pytest.approx(foregone)
    assert rb["battery_grid_injection_revenue_eur"] == pytest.approx(injection)
    assert rb["uplift_eur"] == pytest.approx(extra - foregone + injection)
    assert rb["revenue_change_eur"] == pytest.approx(rb["uplift_eur"])


def test_zero_baseline_revenue_percentage_is_not_applicable():
    frame = qh_frame([{"imp": 1.0, "exp": 0.0, "pv": 0.0}])
    settled = settle_dispatch(attach_baseline_dispatch(frame, BatteryConfig(1, 4, 4, 1.0, 1.0, 0.0)), TariffConfig())
    assert settled.totals["total_energent_pv_revenue_eur"] == pytest.approx(0.0)
    assert settled.totals["revenue_change_pct"] is None
