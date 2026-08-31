"""Headless Fluvius ingestion service used by the CLI and later UIs."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from btm_sim import __version__
from btm_sim.fluvius.constants import (
    CANONICAL_COLUMNS,
    DOCUMENTED_TOLERANCE_KWH,
    INTERVAL_HOURS,
    MATERIAL_IMBALANCE_KWH,
    PV_SOURCE_MEASURED,
    QUALITY_RANK,
    STATUS_TO_QUALITY,
    TZ_NAME,
)
from btm_sim.fluvius.csv_io import read_fluvius_csv, sha256_file
from btm_sim.fluvius.intervals import (
    DATE_FORMAT_CODES,
    DATE_FORMAT_COLUMN,
    assert_unique_utc,
    collect_date_values,
    convert_series_intervals,
    detect_date_format,
)
from btm_sim.fluvius.issues import IssueLog
from btm_sim.fluvius.periods import PeriodOffer, discover_periods, resolve_period_id
from btm_sim.fluvius.roles import RoleSeries, detect_roles
from btm_sim.fluvius.validate import (
    enforce_selected_period,
    reconstruct_load,
    simultaneous_import_export_diagnostic,
)

ENERGY_COLUMNS = {
    "offtake": "grid_import_baseline_kwh",
    "injection": "grid_export_baseline_kwh",
    "pv": "pv_production_kwh",
}
QUALITY_COLUMNS = {
    "offtake": "offtake_quality",
    "injection": "injection_quality",
    "pv": "pv_quality",
}


@dataclass
class IngestResult:
    issues: IssueLog
    sources: list[dict[str, Any]]
    roles: dict[str, dict[str, Any]]
    usable: pd.DataFrame | None
    periods: list[PeriodOffer]
    dst: dict[str, Any]
    allow_unvalidated: bool
    acknowledge_site_boundary: bool

    @property
    def ok(self) -> bool:
        return self.issues.ok


@dataclass
class NormalizationResult:
    issues: IssueLog
    frame: pd.DataFrame | None
    periods: list[PeriodOffer]
    selected_period: PeriodOffer | None
    report: dict[str, Any]
    ingest: IngestResult
    output_paths: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.issues.ok and self.frame is not None


def ingest_fluvius(
    inputs: Sequence[str | Path],
    *,
    allow_unvalidated: bool = False,
    acknowledge_site_boundary: bool = False,
) -> IngestResult:
    issues = IssueLog()
    paths = [Path(path) for path in inputs]
    sources: list[dict[str, Any]] = []
    frames: list[pd.DataFrame] = []

    for path in paths:
        source = {
            "path": str(path),
            "sha256": sha256_file(path) if path.exists() else None,
        }
        frame = read_fluvius_csv(path, issues)
        if frame is None:
            sources.append(source)
            continue
        date_format = detect_date_format(collect_date_values(frame), issues, path=path)
        frame = frame.copy()
        frame[DATE_FORMAT_COLUMN] = date_format or ""
        source.update(
            {
                "n_rows": int(len(frame)),
                "registers": sorted(frame["Register"].dropna().unique().tolist()),
                "eans": sorted(
                    {
                        str(value)
                        for value in frame.get("EAN-code", pd.Series(dtype=str)).dropna().unique()
                    }
                ),
                "date_format": date_format,
            }
        )
        sources.append(source)
        frames.append(frame)

    roles = detect_roles(frames, issues) if frames else {}
    role_meta = {
        name: {
            "register": series.register,
            "ean": series.ean,
            "unit": series.unit,
            "n_rows": int(len(series.frame)),
            "date_format": _role_date_format(series),
        }
        for name, series in roles.items()
    }

    dst = {
        "n_spring_skipped_wall_clock": 0,
        "n_autumn_repeated_wall_clock": 0,
    }
    if len(roles) < 3 or any(item.code in DATE_FORMAT_CODES for item in issues.fatals):
        return IngestResult(
            issues=issues,
            sources=sources,
            roles=role_meta,
            usable=None,
            periods=[],
            dst=dst,
            allow_unvalidated=allow_unvalidated,
            acknowledge_site_boundary=acknowledge_site_boundary,
        )

    converted: dict[str, pd.DataFrame] = {}
    for name, series in roles.items():
        table = _prepare_role(series, issues)
        dst["n_spring_skipped_wall_clock"] += int(table.attrs.get("n_spring_skipped_wall_clock", 0))
        dst["n_autumn_repeated_wall_clock"] += int(table.attrs.get("n_autumn_repeated_wall_clock", 0))
        converted[name] = table

    if not issues.ok:
        return IngestResult(
            issues=issues,
            sources=sources,
            roles=role_meta,
            usable=None,
            periods=[],
            dst=dst,
            allow_unvalidated=allow_unvalidated,
            acknowledge_site_boundary=acknowledge_site_boundary,
        )

    aligned = _align_roles(converted)
    usable = aligned.loc[
        aligned["grid_import_baseline_kwh"].notna()
        & aligned["grid_export_baseline_kwh"].notna()
        & aligned["pv_production_kwh"].notna()
    ].copy()
    usable = reconstruct_load(
        usable,
        issues,
        acknowledge_site_boundary=acknowledge_site_boundary,
        emit_issues=False,
    )
    usable["quality_flag"] = [
        _combine_quality(off, inj, pv)
        for off, inj, pv in zip(
            usable["offtake_quality"],
            usable["injection_quality"],
            usable["pv_quality"],
            strict=True,
        )
    ]
    usable["pv_source"] = PV_SOURCE_MEASURED
    usable["interval_hours"] = INTERVAL_HOURS
    usable = usable.sort_values("timestamp_utc").reset_index(drop=True)
    periods = discover_periods(usable)

    if periods:
        full_years = [offer for offer in periods if offer.complete_calendar_year]
        partial = [offer for offer in periods if offer.kind == "partial_calendar_year"]
        if partial:
            issues.warning(
                "PARTIAL_CALENDAR_YEARS",
                "Partial calendar years are available and are not labelled as complete years",
                years=[offer.id for offer in partial],
            )
        if not full_years:
            issues.warning(
                "NO_COMPLETE_CALENDAR_YEAR",
                "No complete calendar year is covered by the common measured overlap",
            )

    return IngestResult(
        issues=issues,
        sources=sources,
        roles=role_meta,
        usable=usable,
        periods=periods,
        dst=dst,
        allow_unvalidated=allow_unvalidated,
        acknowledge_site_boundary=acknowledge_site_boundary,
    )


def materialize_period(
    ingest: IngestResult,
    period_id: str,
    *,
    allow_unvalidated: bool | None = None,
    acknowledge_site_boundary: bool | None = None,
) -> NormalizationResult:
    allow = ingest.allow_unvalidated if allow_unvalidated is None else allow_unvalidated
    acknowledge = (
        ingest.acknowledge_site_boundary
        if acknowledge_site_boundary is None
        else acknowledge_site_boundary
    )
    issues = IssueLog()
    issues.extend(ingest.issues)

    selected = resolve_period_id(ingest.periods, period_id)
    if selected is None:
        issues.fatal(
            "UNKNOWN_PERIOD",
            f"Period {period_id!r} is not among the discovered continuous common periods",
            requested=period_id,
            available=[offer.id for offer in ingest.periods],
        )
        return NormalizationResult(
            issues=issues,
            frame=None,
            periods=ingest.periods,
            selected_period=None,
            report=_build_report(ingest, issues, None, None, allow, acknowledge),
            ingest=ingest,
        )

    if ingest.usable is None:
        issues.fatal("NO_USABLE_DATA", "No usable Fluvius intervals were ingested")
        frame = None
    else:
        mask = selected.contains(ingest.usable["timestamp_utc"])
        frame = ingest.usable.loc[mask, list(CANONICAL_COLUMNS)].copy().reset_index(drop=True)
        frame = reconstruct_load(
            frame,
            issues,
            acknowledge_site_boundary=acknowledge,
            emit_issues=True,
        )
        enforce_selected_period(
            frame,
            issues,
            allow_unvalidated=allow,
            expected_intervals=selected.n_intervals,
        )
        if not issues.ok:
            frame = None
        else:
            frame = frame.loc[:, list(CANONICAL_COLUMNS)]

    return NormalizationResult(
        issues=issues,
        frame=frame,
        periods=ingest.periods,
        selected_period=selected,
        report=_build_report(ingest, issues, selected, frame, allow, acknowledge),
        ingest=ingest,
    )


def normalize_fluvius(
    inputs: Sequence[str | Path],
    *,
    period: str = "common",
    allow_unvalidated: bool = False,
    acknowledge_site_boundary: bool = False,
    output_dir: str | Path | None = None,
) -> NormalizationResult:
    ingest = ingest_fluvius(
        inputs,
        allow_unvalidated=allow_unvalidated,
        acknowledge_site_boundary=acknowledge_site_boundary,
    )
    result = materialize_period(
        ingest,
        period,
        allow_unvalidated=allow_unvalidated,
        acknowledge_site_boundary=acknowledge_site_boundary,
    )
    if output_dir is not None:
        write_run_outputs(result, output_dir)
    return result


def write_run_outputs(result: NormalizationResult, output_dir: str | Path) -> dict[str, Path]:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    report_path = directory / "validation_report.json"
    report_path.write_text(json.dumps(result.report, indent=2) + "\n", encoding="utf-8")
    paths = {"validation_report": report_path}
    if result.frame is not None:
        parquet_path = directory / "normalized_input.parquet"
        result.frame.to_parquet(parquet_path, index=False)
        paths["normalized_input"] = parquet_path
    result.output_paths = {key: str(path) for key, path in paths.items()}
    result.report["outputs"] = result.output_paths
    report_path.write_text(json.dumps(result.report, indent=2) + "\n", encoding="utf-8")
    return paths


def ingest_report(ingest: IngestResult) -> dict[str, Any]:
    report = _build_report(
        ingest,
        ingest.issues,
        None,
        None,
        ingest.allow_unvalidated,
        ingest.acknowledge_site_boundary,
    )
    report["ok"] = ingest.ok
    return report


def _role_date_format(series: RoleSeries) -> str | None:
    frame = series.frame
    if DATE_FORMAT_COLUMN not in frame.columns:
        return None
    unique = sorted(
        {
            str(value)
            for value in frame[DATE_FORMAT_COLUMN].tolist()
            if value and str(value) not in {"", "nan", "None"}
        }
    )
    if len(unique) == 1:
        return unique[0]
    return None


def _prepare_role(series: RoleSeries, issues: IssueLog) -> pd.DataFrame:
    table = convert_series_intervals(
        series.frame,
        issues,
        role=series.role,
        date_format=_role_date_format(series),
    )
    spring = int(table.attrs.get("n_spring_skipped_wall_clock", 0))
    autumn = int(table.attrs.get("n_autumn_repeated_wall_clock", 0))
    assert_unique_utc(table["timestamp_utc"], issues, role=series.role)
    qualities: list[str] = []
    energies: list[float] = []
    n_unknown = 0
    n_negative = 0
    for status, energy in zip(table["Validatiestatus"], table["energy_kwh"], strict=True):
        quality = STATUS_TO_QUALITY.get(str(status).strip())
        if quality is None:
            n_unknown += 1
            quality = "unavailable"
        if quality == "unavailable":
            energy = float("nan")
        elif pd.notna(energy) and energy < -1e-9:
            n_negative += 1
        qualities.append(quality)
        energies.append(energy)
    if n_unknown:
        issues.fatal(
            "UNKNOWN_VALIDATION_STATUS",
            f"{series.role} has {n_unknown} unrecognized Validatiestatus values",
            role=series.role,
            count=n_unknown,
        )
    if n_negative:
        issues.fatal(
            "NEGATIVE_SOURCE_ENERGY",
            f"{series.role} has {n_negative} negative kWh readings",
            role=series.role,
            count=n_negative,
        )
    table[ENERGY_COLUMNS[series.role]] = energies
    table[QUALITY_COLUMNS[series.role]] = qualities
    table["timestamp_utc"] = pd.to_datetime(table["timestamp_utc"], utc=True)
    table = table.sort_values("timestamp_utc")
    table.attrs["n_spring_skipped_wall_clock"] = spring
    table.attrs["n_autumn_repeated_wall_clock"] = autumn
    return table


def _align_roles(converted: dict[str, pd.DataFrame]) -> pd.DataFrame:
    offtake = converted["offtake"][
        ["timestamp_utc", "timestamp_local", ENERGY_COLUMNS["offtake"], QUALITY_COLUMNS["offtake"]]
    ]
    injection = converted["injection"][
        ["timestamp_utc", ENERGY_COLUMNS["injection"], QUALITY_COLUMNS["injection"]]
    ]
    pv = converted["pv"][["timestamp_utc", ENERGY_COLUMNS["pv"], QUALITY_COLUMNS["pv"]]]
    aligned = offtake.merge(injection, on="timestamp_utc", how="outer").merge(
        pv, on="timestamp_utc", how="outer"
    )
    missing_local = aligned["timestamp_local"].isna() & aligned["timestamp_utc"].notna()
    if bool(missing_local.any()):
        aligned.loc[missing_local, "timestamp_local"] = aligned.loc[
            missing_local, "timestamp_utc"
        ].dt.tz_convert(TZ_NAME)
    return aligned


def _combine_quality(offtake: str, injection: str, pv: str) -> str:
    rank = max(
        QUALITY_RANK.get(offtake, 2),
        QUALITY_RANK.get(injection, 2),
        QUALITY_RANK.get(pv, 2),
    )
    for name, value in QUALITY_RANK.items():
        if value == rank:
            return name
    return "unavailable"


def _format_utc_offset(value: timedelta) -> str:
    total_minutes = int(value.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    hours, minutes = divmod(abs(total_minutes), 60)
    return f"{sign}{hours:02d}:{minutes:02d}"


def _detected_dst_transitions(frame: pd.DataFrame | None) -> list[dict[str, Any]]:
    """Return UTC-offset changes detected in a continuous canonical timeline."""
    if frame is None or frame.empty or "timestamp_utc" not in frame:
        return []

    utc = (
        pd.to_datetime(frame["timestamp_utc"], utc=True, errors="coerce")
        .dropna()
        .drop_duplicates()
        .sort_values()
        .reset_index(drop=True)
    )
    if len(utc) < 2:
        return []

    local = utc.dt.tz_convert(TZ_NAME)
    transitions: list[dict[str, Any]] = []
    step = pd.Timedelta(minutes=15)
    for index in range(1, len(utc)):
        if utc.iloc[index] - utc.iloc[index - 1] != step:
            continue
        before = local.iloc[index - 1]
        after = local.iloc[index]
        offset_before = before.utcoffset()
        offset_after = after.utcoffset()
        if offset_before is None or offset_after is None or offset_before == offset_after:
            continue
        spring = offset_after > offset_before
        transitions.append(
            {
                "date_local": after.strftime("%Y-%m-%d"),
                "kind": "spring_forward" if spring else "autumn_backward",
                "utc_offset_before": _format_utc_offset(offset_before),
                "utc_offset_after": _format_utc_offset(offset_after),
                "physical_quarter_hours_in_local_day": 92 if spring else 100,
            }
        )
    return transitions


def _dst_report(
    ingest: IngestResult,
    selected: PeriodOffer | None,
    frame: pd.DataFrame | None,
) -> dict[str, Any]:
    timeline = frame
    if timeline is None and ingest.usable is not None:
        timeline = ingest.usable
        if selected is not None:
            timeline = timeline.loc[selected.contains(timeline["timestamp_utc"])]

    report = dict(ingest.dst)
    report["transitions"] = _detected_dst_transitions(timeline)
    report["note"] = (
        "Transitions are detected from UTC-offset changes in the continuous "
        "Europe/Brussels timeline. Raw parser counters are retained for diagnostics."
    )
    return report


def _build_report(
    ingest: IngestResult,
    issues: IssueLog,
    selected: PeriodOffer | None,
    frame: pd.DataFrame | None,
    allow_unvalidated: bool,
    acknowledge_site_boundary: bool,
) -> dict[str, Any]:
    unvalidated_dates: list[str] = []
    n_unvalidated = 0
    if frame is not None:
        mask = frame["quality_flag"].eq("unvalidated")
        n_unvalidated = int(mask.sum())
        unvalidated_dates = sorted(
            {
                ts.tz_convert("Europe/Brussels").strftime("%Y-%m-%d")
                for ts in frame.loc[mask, "timestamp_local"]
            }
        )
    return {
        "ok": issues.ok and frame is not None,
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "software_version": __version__,
        "thresholds": {
            "documented_tolerance_kwh": DOCUMENTED_TOLERANCE_KWH,
            "material_imbalance_kwh": MATERIAL_IMBALANCE_KWH,
            "note": (
                "DATA_CONTRACT.md requires a documented numerical tolerance but does not "
                "name the kWh values; these defaults match Fluvius millikWh resolution "
                "and a 0.05 kWh material-flow threshold."
            ),
        },
        "unvalidated_policy": {
            "allow_unvalidated": allow_unvalidated,
            "acknowledged": bool(allow_unvalidated),
            "n_unvalidated_in_selected_period": n_unvalidated,
            "dates": unvalidated_dates,
        },
        "site_boundary_policy": {
            "acknowledge_site_boundary": acknowledge_site_boundary,
        },
        "simultaneous_import_export": simultaneous_import_export_diagnostic(
            frame if frame is not None else ingest.usable
        ),
        "sources": ingest.sources,
        "roles": ingest.roles,
        "dst": _dst_report(ingest, selected, frame),
        "periods": [offer.to_dict() for offer in ingest.periods],
        "selected_period": None if selected is None else selected.to_dict(),
        "n_rows": None if frame is None else int(len(frame)),
        "fatal": [item.to_dict() for item in issues.fatals],
        "warnings": [item.to_dict() for item in issues.warnings],
    }
