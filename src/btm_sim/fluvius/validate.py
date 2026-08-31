"""Physical-balance and selected-period validation."""

from __future__ import annotations

from typing import Any, Sequence

import pandas as pd

from btm_sim.fluvius.constants import (
    DOCUMENTED_TOLERANCE_KWH,
    FLOAT_EPS_KWH,
    MATERIAL_IMBALANCE_KWH,
    TZ_NAME,
)
from btm_sim.fluvius.issues import IssueLog

SITE_BOUNDARY_ISSUE_CODES = frozenset({"NEGATIVE_LOAD", "EXPORT_EXCEEDS_PV"})


def reconstruct_load(
    frame: pd.DataFrame,
    issues: IssueLog,
    *,
    acknowledge_site_boundary: bool,
    emit_issues: bool = True,
) -> pd.DataFrame:
    out = frame.copy()
    pv = out["pv_production_kwh"]
    imp = out["grid_import_baseline_kwh"]
    exp = out["grid_export_baseline_kwh"]
    load = pv + imp - exp

    n_float = int((load.abs() <= FLOAT_EPS_KWH).sum())
    load = load.mask(load.abs() <= FLOAT_EPS_KWH, 0.0)

    tiny_negative = (load < 0) & (load >= -DOCUMENTED_TOLERANCE_KWH)
    n_rounded = int(tiny_negative.sum())
    if n_rounded:
        if emit_issues:
            issues.warning(
                "MINOR_NEGATIVE_LOAD",
                (
                    f"{n_rounded} reconstructed load values were within "
                    f"{DOCUMENTED_TOLERANCE_KWH} kWh of zero and were rounded to zero"
                ),
                count=n_rounded,
                tolerance_kwh=DOCUMENTED_TOLERANCE_KWH,
            )
        load = load.mask(tiny_negative, 0.0)

    material_negative = load < -DOCUMENTED_TOLERANCE_KWH
    n_neg = int(material_negative.sum())
    if n_neg and emit_issues:
        stamps = _mask_timestamp_details(out, material_negative)
        negative = load[material_negative]
        _balance_issue(
            issues,
            acknowledge_site_boundary,
            "NEGATIVE_LOAD",
            f"{n_neg} intervals have reconstructed site load below -{DOCUMENTED_TOLERANCE_KWH} kWh",
            count=n_neg,
            min_kwh=float(load.min()),
            total_negative_load_kwh=float((-negative).sum()),
            first_local_timestamp=stamps["first_local_timestamp"],
            last_local_timestamp=stamps["last_local_timestamp"],
            affected_local_dates=stamps["affected_local_dates"],
            examples=stamps["examples"],
        )

    both = (imp > MATERIAL_IMBALANCE_KWH) & (exp > MATERIAL_IMBALANCE_KWH)
    n_both = int(both.sum())

    export_gt_pv = (exp - pv) > MATERIAL_IMBALANCE_KWH
    n_exp = int(export_gt_pv.sum())
    if n_exp and emit_issues:
        stamps = _mask_timestamp_details(out, export_gt_pv)
        excess = (exp - pv)[export_gt_pv]
        _balance_issue(
            issues,
            acknowledge_site_boundary,
            "EXPORT_EXCEEDS_PV",
            (
                f"{n_exp} intervals have grid export exceeding PV production by more than "
                f"{MATERIAL_IMBALANCE_KWH} kWh"
            ),
            count=n_exp,
            threshold_kwh=MATERIAL_IMBALANCE_KWH,
            max_excess_kwh=float((exp - pv).max()),
            total_excess_kwh=float(excess.sum()),
            first_local_timestamp=stamps["first_local_timestamp"],
            last_local_timestamp=stamps["last_local_timestamp"],
            affected_local_dates=stamps["affected_local_dates"],
            examples=stamps["examples"],
        )

    out["site_load_kwh"] = load
    out.attrs["n_float_eps_zeroed"] = n_float
    out.attrs["n_rounded_negative_load"] = n_rounded
    out.attrs["n_simultaneous_import_export"] = n_both
    return out


def simultaneous_import_export_diagnostic(frame: pd.DataFrame | None) -> dict[str, float | int | str]:
    """Informational count of quarter-hours with material import and export."""
    n_intervals = 0
    if frame is not None and not frame.empty:
        imp = frame["grid_import_baseline_kwh"]
        exp = frame["grid_export_baseline_kwh"]
        n_intervals = int(((imp > MATERIAL_IMBALANCE_KWH) & (exp > MATERIAL_IMBALANCE_KWH)).sum())
    return {
        "n_intervals": n_intervals,
        "threshold_kwh": MATERIAL_IMBALANCE_KWH,
        "note": (
            "Fluvius offtake and injection are directional energy totals over a "
            "quarter-hour, not instantaneous power. Both can be positive when the "
            "site changes direction within the interval. The source values are kept "
            "separate; they are not netted, clipped, or treated as a site-boundary "
            "exception."
        ),
    }


def requires_site_boundary_acknowledgement(fatal_codes: Sequence[str]) -> bool:
    """True when the only blocking codes are acknowledgeable site-boundary issues."""
    codes = {str(code) for code in fatal_codes if code}
    return bool(codes) and codes <= SITE_BOUNDARY_ISSUE_CODES


def _mask_timestamp_details(frame: pd.DataFrame, mask: pd.Series) -> dict[str, Any]:
    affected = frame.loc[mask].copy()
    if "timestamp_utc" in affected.columns:
        affected["_utc"] = pd.to_datetime(affected["timestamp_utc"], utc=True)
        affected = affected.sort_values("_utc")
        examples = (
            affected["_utc"].dt.tz_convert("UTC").dt.strftime("%Y-%m-%dT%H:%M:%SZ").head(5).tolist()
        )
    else:
        examples = []
    local = affected["timestamp_local"] if "timestamp_local" in affected.columns else pd.Series(dtype=object)
    first_local = None
    last_local = None
    dates: list[str] = []
    if len(local):
        first_local = pd.Timestamp(local.iloc[0]).isoformat()
        last_local = pd.Timestamp(local.iloc[-1]).isoformat()
        dates = sorted(
            {
                pd.Timestamp(ts).tz_convert(TZ_NAME).strftime("%Y-%m-%d")
                if getattr(pd.Timestamp(ts), "tzinfo", None) is not None
                else pd.Timestamp(ts).strftime("%Y-%m-%d")
                for ts in local
            }
        )
    return {
        "examples": examples,
        "first_local_timestamp": first_local,
        "last_local_timestamp": last_local,
        "affected_local_dates": dates,
    }


def _balance_issue(
    issues: IssueLog,
    acknowledge: bool,
    code: str,
    message: str,
    **details,
) -> None:
    if acknowledge:
        issues.warning(
            code,
            message + " (acknowledged as a site-boundary exception)",
            acknowledged_site_boundary=True,
            **details,
        )
    else:
        issues.fatal(code, message, **details)


def enforce_selected_period(
    frame: pd.DataFrame,
    issues: IssueLog,
    *,
    allow_unvalidated: bool,
    expected_intervals: int,
) -> None:
    if frame.empty:
        issues.fatal("EMPTY_PERIOD", "Selected period contains no usable intervals")
        return

    utc = frame["timestamp_utc"]
    if not utc.is_monotonic_increasing or not utc.is_unique:
        issues.fatal(
            "NON_MONOTONIC_UTC",
            "Canonical UTC index is not unique and strictly increasing",
        )

    step = pd.Timedelta(minutes=15)
    diffs = utc.diff().iloc[1:]
    n_gaps = int((diffs != step).sum())
    if n_gaps:
        issues.fatal(
            "UNRESOLVED_GAPS",
            f"Selected period has {n_gaps} unresolved quarter-hour gaps",
            n_gaps=n_gaps,
        )

    span_intervals = int((utc.iloc[-1] - utc.iloc[0]) / step) + 1
    if len(frame) != expected_intervals or span_intervals != expected_intervals:
        issues.fatal(
            "PERIOD_INTERVAL_COUNT",
            (
                f"Selected period has {len(frame)} intervals "
                f"(span {span_intervals}), expected {expected_intervals}"
            ),
            actual=len(frame),
            expected=expected_intervals,
        )

    null_energy = (
        frame["grid_import_baseline_kwh"].isna()
        | frame["grid_export_baseline_kwh"].isna()
        | frame["pv_production_kwh"].isna()
        | frame["site_load_kwh"].isna()
    )
    unavailable = frame["quality_flag"].eq("unavailable") | null_energy
    n_unavailable = int(unavailable.sum())
    if n_unavailable:
        issues.fatal(
            "UNAVAILABLE_IN_PERIOD",
            f"Selected period contains {n_unavailable} null or Geen gegevens readings",
            count=n_unavailable,
        )

    unvalidated = frame["quality_flag"].eq("unvalidated")
    n_unvalidated = int(unvalidated.sum())
    if n_unvalidated and not allow_unvalidated:
        issues.fatal(
            "UNVALIDATED_NOT_ALLOWED",
            (
                f"Selected period contains {n_unvalidated} non-null Ongevalideerd "
                "intervals; pass allow_unvalidated to use them"
            ),
            count=n_unvalidated,
        )
    elif n_unvalidated:
        dates = sorted(
            {
                ts.tz_convert("Europe/Brussels").strftime("%Y-%m-%d")
                for ts in frame.loc[unvalidated, "timestamp_local"]
            }
        )
        issues.warning(
            "UNVALIDATED_USED",
            (
                f"{n_unvalidated} non-null Ongevalideerd intervals were used because "
                "allow_unvalidated is enabled"
            ),
            count=n_unvalidated,
            dates=dates,
            acknowledged=True,
        )
