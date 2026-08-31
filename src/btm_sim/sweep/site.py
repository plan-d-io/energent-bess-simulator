"""Site analysis and automatic power-grid generation for the revenue sweep."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

from btm_sim.fluvius.constants import INTERVAL_HOURS, TZ_NAME
from btm_sim.fluvius.pipeline import ingest_fluvius, materialize_period
from btm_sim.fluvius.validate import requires_site_boundary_acknowledgement
from btm_sim.sweep.candidates import (
    MODE_AUTOMATIC,
    SweepCandidate,
    parse_durations,
)
from btm_sim.sweep.exceptions import SweepRequestError

QUANTILE_METHOD = "linear"
IMPORT_PCTL = 0.995
SURPLUS_PCTL = 0.995
DAILY_PCTL = 0.95
MIN_ENGINEERING_STEP_KW = 5.0
DIAGNOSTIC_NO_SHIFTING = "no_revenue_shifting_opportunity"
CANDIDATE_GENERATION_METHOD = "11a.1_engineering_125_with_lower_range"


@dataclass(frozen=True)
class SiteAnalysis:
    quantile_method: str
    n_intervals: int
    has_positive_import: bool
    has_positive_surplus: bool
    no_revenue_shifting_opportunity: bool
    diagnostic: str | None
    max_import_kw: float | None
    p995_import_kw: float | None
    max_surplus_kw: float | None
    p995_surplus_kw: float | None
    total_import_kwh: float
    total_surplus_kwh: float
    median_daily_import_kwh: float | None
    p95_daily_import_kwh: float | None
    median_daily_surplus_kwh: float | None
    p95_daily_surplus_kwh: float | None
    n_local_days: int
    reference_power_kw: float | None
    power_step_kw: float | None
    rounded_reference_power_kw: float | None
    power_grid_kw: tuple[float, ...]
    durations_hours: tuple[float, ...]
    automatic_candidates: tuple[SweepCandidate, ...]
    candidate_generation_method: str = CANDIDATE_GENERATION_METHOD

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["automatic_candidates"] = [item.to_dict() for item in self.automatic_candidates]
        payload["power_grid_kw"] = list(self.power_grid_kw)
        payload["durations_hours"] = list(self.durations_hours)
        return payload


@dataclass(frozen=True)
class SelectedPeriodInspection:
    """Read-only selected-period inspection for UI preflight. No DataFrame."""

    ok: bool
    requires_site_boundary_acknowledgement: bool
    period_id: str
    selected_period: dict[str, Any] | None
    fatal: tuple[dict[str, Any], ...]
    warnings: tuple[dict[str, Any], ...]
    report: dict[str, Any]
    site_analysis: SiteAnalysis | None

    @property
    def automatic_candidates(self) -> tuple[SweepCandidate, ...]:
        if self.site_analysis is None:
            return ()
        return self.site_analysis.automatic_candidates

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "requires_site_boundary_acknowledgement": self.requires_site_boundary_acknowledgement,
            "period_id": self.period_id,
            "selected_period": self.selected_period,
            "fatal": [dict(item) for item in self.fatal],
            "warnings": [dict(item) for item in self.warnings],
            "report": self.report,
            "site_analysis": None if self.site_analysis is None else self.site_analysis.to_dict(),
            "automatic_candidates": [item.to_dict() for item in self.automatic_candidates],
        }


def analyse_site(frame: pd.DataFrame, durations_hours: Sequence[float]) -> SiteAnalysis:
    """Return deterministic site metrics and the automatic candidate list."""
    work = _prepare_frame(frame)
    durations = parse_durations(list(durations_hours), name="durations")
    dt = (
        work["interval_hours"].to_numpy(dtype=float)
        if "interval_hours" in work.columns
        else np.full(len(work), INTERVAL_HOURS, dtype=float)
    )
    import_kwh = work["grid_import_baseline_kwh"].to_numpy(dtype=float)
    surplus_kwh = work["grid_export_baseline_kwh"].to_numpy(dtype=float)
    import_kw = import_kwh / dt
    surplus_kw = surplus_kwh / dt
    positive_import = import_kw[import_kw > 0]
    positive_surplus = surplus_kw[surplus_kw > 0]
    has_import = positive_import.size > 0
    has_surplus = positive_surplus.size > 0
    daily = _daily_energy(work, import_kwh, surplus_kwh)
    no_opportunity = not has_import or not has_surplus
    max_import = float(positive_import.max()) if has_import else None
    max_surplus = float(positive_surplus.max()) if has_surplus else None
    p995_import = _quantile(positive_import, IMPORT_PCTL) if has_import else None
    p995_surplus = _quantile(positive_surplus, SURPLUS_PCTL) if has_surplus else None
    reference = None if no_opportunity else max(float(p995_import), float(p995_surplus))
    if no_opportunity or reference is None or not math.isfinite(reference) or reference <= 0:
        grid: tuple[float, ...] = ()
        step = None
        rounded = None
        candidates: tuple[SweepCandidate, ...] = ()
    else:
        grid, step, rounded = generate_power_grid(reference)
        candidates = _candidates_from_grid(grid, durations, daily["p95_import"], daily["p95_surplus"])
    return SiteAnalysis(
        quantile_method=QUANTILE_METHOD,
        n_intervals=int(len(work)),
        has_positive_import=has_import,
        has_positive_surplus=has_surplus,
        no_revenue_shifting_opportunity=no_opportunity,
        diagnostic=DIAGNOSTIC_NO_SHIFTING if no_opportunity else None,
        max_import_kw=max_import,
        p995_import_kw=p995_import,
        max_surplus_kw=max_surplus,
        p995_surplus_kw=p995_surplus,
        total_import_kwh=float(import_kwh.sum()),
        total_surplus_kwh=float(surplus_kwh.sum()),
        median_daily_import_kwh=daily["median_import"],
        p95_daily_import_kwh=daily["p95_import"],
        median_daily_surplus_kwh=daily["median_surplus"],
        p95_daily_surplus_kwh=daily["p95_surplus"],
        n_local_days=int(daily["n_days"]),
        reference_power_kw=None if reference is None else float(reference),
        power_step_kw=step,
        rounded_reference_power_kw=rounded,
        power_grid_kw=grid,
        durations_hours=durations,
        automatic_candidates=candidates,
        candidate_generation_method=CANDIDATE_GENERATION_METHOD,
    )


def generate_power_grid(reference_kw: float) -> tuple[tuple[float, ...], float, float]:
    """Build the 1/2/5 × 10^n power grid, including one extra upper-range step.

    The main step, rounded reference, and upper guard are unchanged from Brief
    11A. The lower end also includes every 1/2/5 × 10^n value from 5 kW through
    that main step.
    """
    if not math.isfinite(reference_kw) or reference_kw <= 0:
        raise SweepRequestError("Automatic reference power must be a positive finite number")
    step = ceil_engineering_step(reference_kw / 6.0, minimum=MIN_ENGINEERING_STEP_KW)
    n_steps = max(1, int(math.ceil(reference_kw / step - 1e-12)))
    rounded = _clean(n_steps * step)
    powers = list(lower_engineering_powers(step))
    powers.extend(_clean(index * step) for index in range(1, n_steps + 1))
    powers.append(_clean(rounded + step))
    unique = tuple(sorted({power for power in powers if power > 0}))
    return unique, step, rounded


def lower_engineering_powers(
    step_kw: float,
    *,
    minimum: float = MIN_ENGINEERING_STEP_KW,
) -> tuple[float, ...]:
    """Every 1, 2, 5 × 10^n value from ``minimum`` through the main step, inclusive."""
    if not math.isfinite(step_kw) or step_kw <= 0:
        return (float(minimum),)
    values: list[float] = []
    exponent = 0
    while True:
        progressed = False
        for mantissa in (1.0, 2.0, 5.0):
            value = _clean(mantissa * (10.0**exponent))
            if value + 1e-12 < minimum:
                continue
            if value > float(step_kw) + 1e-12:
                return tuple(values)
            values.append(value)
            progressed = True
        if not progressed and exponent > 8:
            return tuple(values)
        exponent += 1


def ceil_engineering_step(value: float, *, minimum: float = MIN_ENGINEERING_STEP_KW) -> float:
    """Smallest 1, 2, or 5 × 10^n that is at least ``value``, and at least ``minimum``."""
    if not math.isfinite(value) or value <= 0:
        return float(minimum)
    exponent = math.floor(math.log10(value))
    mantissa = value / (10.0**exponent)
    for step in (1.0, 2.0, 5.0):
        if mantissa <= step + 1e-12:
            return max(float(minimum), _clean(step * (10.0**exponent)))
    return max(float(minimum), _clean(10.0 ** (exponent + 1)))


def inspect_selected_period(
    fluvius_paths: Sequence[str | Path],
    period_id: str,
    *,
    allow_unvalidated: bool = False,
    acknowledge_site_boundary: bool = False,
    durations_hours: Sequence[float] | None = None,
) -> SelectedPeriodInspection:
    """Ingest and materialize a period without raising on validation failure.

    Streamlit should cache this result. It does not run Gurobi or write a sweep
    folder. ``preflight_sweep_candidates`` remains the raising SiteAnalysis API.
    """
    result = _normalize_selected_period(
        fluvius_paths,
        period_id,
        allow_unvalidated=allow_unvalidated,
        acknowledge_site_boundary=acknowledge_site_boundary,
    )
    fatals = tuple(_issue_payload(item) for item in result.issues.fatals)
    warnings = tuple(_issue_payload(item) for item in result.issues.warnings)
    requires_ack = requires_site_boundary_acknowledgement(item["code"] for item in fatals)
    analysis = None
    if result.ok and result.frame is not None:
        durations = (2.0, 4.0) if durations_hours is None else durations_hours
        analysis = analyse_site(result.frame, durations)
    selected = None if result.selected_period is None else result.selected_period.to_dict()
    return SelectedPeriodInspection(
        ok=bool(result.ok and result.frame is not None),
        requires_site_boundary_acknowledgement=requires_ack,
        period_id=str(period_id),
        selected_period=selected,
        fatal=fatals,
        warnings=warnings,
        report=dict(result.report),
        site_analysis=analysis,
    )


def preflight_sweep_candidates(
    fluvius_paths: Sequence[str | Path],
    period_id: str,
    *,
    allow_unvalidated: bool = False,
    acknowledge_site_boundary: bool = False,
    durations_hours: Sequence[float] | None = None,
) -> SiteAnalysis:
    """Ingest and materialize a period, then analyse it. No Gurobi and no sweep folder."""
    inspection = inspect_selected_period(
        fluvius_paths,
        period_id,
        allow_unvalidated=allow_unvalidated,
        acknowledge_site_boundary=acknowledge_site_boundary,
        durations_hours=durations_hours,
    )
    if not inspection.ok or inspection.site_analysis is None:
        raise _request_error_from_inspection(inspection)
    return inspection.site_analysis


def materialize_selected_period(
    fluvius_paths: Sequence[str | Path],
    period_id: str,
    *,
    allow_unvalidated: bool = False,
    acknowledge_site_boundary: bool = False,
):
    result = _normalize_selected_period(
        fluvius_paths,
        period_id,
        allow_unvalidated=allow_unvalidated,
        acknowledge_site_boundary=acknowledge_site_boundary,
    )
    if not result.ok or result.frame is None:
        raise _request_error_from_result(result, period_id)
    return result


def _normalize_selected_period(
    fluvius_paths: Sequence[str | Path],
    period_id: str,
    *,
    allow_unvalidated: bool,
    acknowledge_site_boundary: bool,
):
    paths = [Path(path) for path in fluvius_paths]
    if len(paths) != 3:
        raise SweepRequestError(
            f"Provide exactly three Fluvius CSV exports, got {len(paths)}",
            category="invalid_request",
        )
    ingest = ingest_fluvius(
        paths,
        allow_unvalidated=allow_unvalidated,
        acknowledge_site_boundary=acknowledge_site_boundary,
    )
    return materialize_period(
        ingest,
        period_id,
        allow_unvalidated=allow_unvalidated,
        acknowledge_site_boundary=acknowledge_site_boundary,
    )


def _issue_payload(item: Any) -> dict[str, Any]:
    payload = item.to_dict() if hasattr(item, "to_dict") else dict(item)
    payload.setdefault("details", {})
    return payload


def _request_error_from_inspection(inspection: SelectedPeriodInspection) -> SweepRequestError:
    first = inspection.fatal[0] if inspection.fatal else None
    text = first["message"] if first is not None else "Normalization failed"
    code = None if first is None else first.get("code")
    category = "invalid_period" if code == "UNKNOWN_PERIOD" else "invalid_input"
    return SweepRequestError(
        text,
        category=category,
        issues=list(inspection.fatal),
        details={
            "requires_site_boundary_acknowledgement": inspection.requires_site_boundary_acknowledgement,
            "period_id": inspection.period_id,
            "fatal_codes": [item["code"] for item in inspection.fatal],
            "warnings": list(inspection.warnings),
            "inspection": inspection.to_dict(),
        },
    )


def _request_error_from_result(result: Any, period_id: str) -> SweepRequestError:
    fatals = [_issue_payload(item) for item in result.issues.fatals]
    warnings = [_issue_payload(item) for item in result.issues.warnings]
    first = fatals[0] if fatals else None
    text = first["message"] if first is not None else "Normalization failed"
    code = None if first is None else first.get("code")
    category = "invalid_period" if code == "UNKNOWN_PERIOD" else "invalid_input"
    requires_ack = requires_site_boundary_acknowledgement(item["code"] for item in fatals)
    return SweepRequestError(
        text,
        category=category,
        issues=fatals,
        details={
            "requires_site_boundary_acknowledgement": requires_ack,
            "period_id": str(period_id),
            "fatal_codes": [item["code"] for item in fatals],
            "warnings": warnings,
        },
    )


def site_analysis_from_mapping(values: dict[str, Any]) -> SiteAnalysis:
    raw_candidates = values.get("automatic_candidates") or []
    candidates = tuple(
        SweepCandidate(
            candidate_id=str(item["candidate_id"]),
            power_kw=float(item["power_kw"]),
            usable_energy_kwh=float(item["usable_energy_kwh"]),
            duration_hours=float(item["duration_hours"]),
            exceeds_p95_daily_pv_surplus=bool(item.get("exceeds_p95_daily_pv_surplus", False)),
            exceeds_p95_daily_import=bool(item.get("exceeds_p95_daily_import", False)),
            source=str(item.get("source") or MODE_AUTOMATIC),
        )
        for item in raw_candidates
    )
    return SiteAnalysis(
        quantile_method=str(values.get("quantile_method") or QUANTILE_METHOD),
        n_intervals=int(values.get("n_intervals") or 0),
        has_positive_import=bool(values.get("has_positive_import")),
        has_positive_surplus=bool(values.get("has_positive_surplus")),
        no_revenue_shifting_opportunity=bool(values.get("no_revenue_shifting_opportunity")),
        diagnostic=values.get("diagnostic"),
        max_import_kw=_optional_float(values.get("max_import_kw")),
        p995_import_kw=_optional_float(values.get("p995_import_kw")),
        max_surplus_kw=_optional_float(values.get("max_surplus_kw")),
        p995_surplus_kw=_optional_float(values.get("p995_surplus_kw")),
        total_import_kwh=float(values.get("total_import_kwh") or 0.0),
        total_surplus_kwh=float(values.get("total_surplus_kwh") or 0.0),
        median_daily_import_kwh=_optional_float(values.get("median_daily_import_kwh")),
        p95_daily_import_kwh=_optional_float(values.get("p95_daily_import_kwh")),
        median_daily_surplus_kwh=_optional_float(values.get("median_daily_surplus_kwh")),
        p95_daily_surplus_kwh=_optional_float(values.get("p95_daily_surplus_kwh")),
        n_local_days=int(values.get("n_local_days") or 0),
        reference_power_kw=_optional_float(values.get("reference_power_kw")),
        power_step_kw=_optional_float(values.get("power_step_kw")),
        rounded_reference_power_kw=_optional_float(values.get("rounded_reference_power_kw")),
        power_grid_kw=tuple(float(item) for item in values.get("power_grid_kw") or ()),
        durations_hours=tuple(float(item) for item in values.get("durations_hours") or ()),
        automatic_candidates=candidates,
        candidate_generation_method=str(
            values.get("candidate_generation_method") or CANDIDATE_GENERATION_METHOD
        ),
    )


def _candidates_from_grid(
    powers: Sequence[float],
    durations: Sequence[float],
    p95_import: float | None,
    p95_surplus: float | None,
) -> tuple[SweepCandidate, ...]:
    from btm_sim.sweep.candidates import _candidate_id, _exceeds, _round_qty

    items: list[SweepCandidate] = []
    index = 1
    for duration in durations:
        for power in powers:
            energy = _round_qty(float(power) * float(duration))
            items.append(
                SweepCandidate(
                    candidate_id=_candidate_id(index, float(power), energy),
                    power_kw=float(power),
                    usable_energy_kwh=energy,
                    duration_hours=float(duration),
                    exceeds_p95_daily_pv_surplus=_exceeds(energy, p95_surplus),
                    exceeds_p95_daily_import=_exceeds(energy, p95_import),
                    source=MODE_AUTOMATIC,
                )
            )
            index += 1
    return tuple(items)


def _prepare_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or len(frame) == 0:
        raise SweepRequestError("Site analysis requires a non-empty selected-period frame")
    missing = [column for column in ("grid_import_baseline_kwh", "grid_export_baseline_kwh") if column not in frame.columns]
    if missing:
        raise SweepRequestError("Normalized frame is missing " + ", ".join(missing))
    work = frame.copy()
    if "timestamp_utc" in work.columns:
        work = work.sort_values("timestamp_utc").reset_index(drop=True)
    if "interval_hours" not in work.columns:
        work["interval_hours"] = INTERVAL_HOURS
    return work


def _daily_energy(frame: pd.DataFrame, import_kwh: np.ndarray, surplus_kwh: np.ndarray) -> dict[str, Any]:
    if "timestamp_local" not in frame.columns:
        raise SweepRequestError("Normalized frame is missing timestamp_local")
    local = pd.to_datetime(frame["timestamp_local"])
    if getattr(local.dt, "tz", None) is None:
        local = local.dt.tz_localize(TZ_NAME)
    else:
        local = local.dt.tz_convert(TZ_NAME)
    days = pd.Series(local.dt.strftime("%Y-%m-%d").to_numpy(), name="local_date")
    table = pd.DataFrame(
        {
            "local_date": days,
            "import_kwh": import_kwh,
            "surplus_kwh": surplus_kwh,
        }
    )
    grouped = table.groupby("local_date", sort=True).sum(numeric_only=True)
    import_daily = grouped["import_kwh"].to_numpy(dtype=float)
    surplus_daily = grouped["surplus_kwh"].to_numpy(dtype=float)
    return {
        "n_days": int(len(grouped)),
        "median_import": _quantile(import_daily, 0.5) if len(import_daily) else None,
        "p95_import": _quantile(import_daily, DAILY_PCTL) if len(import_daily) else None,
        "median_surplus": _quantile(surplus_daily, 0.5) if len(surplus_daily) else None,
        "p95_surplus": _quantile(surplus_daily, DAILY_PCTL) if len(surplus_daily) else None,
    }


def _quantile(values: np.ndarray, probability: float) -> float:
    return float(np.quantile(np.asarray(values, dtype=float), probability, method=QUANTILE_METHOD))


def _clean(value: float) -> float:
    return float(round(value, 10))


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)
