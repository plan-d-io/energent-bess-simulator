"""Detect offtake, injection, and PV production from active-energy registers."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from btm_sim.fluvius.constants import REGISTER_TO_ROLE, REQUIRED_UNIT, ROLE_REGISTERS
from btm_sim.fluvius.csv_io import parse_ean, parse_volume
from btm_sim.fluvius.issues import IssueLog


@dataclass(frozen=True)
class RoleSeries:
    role: str
    register: str
    ean: str | None
    unit: str
    frame: pd.DataFrame
    unused_peers: list[dict]


def _series_key(frame: pd.DataFrame) -> pd.Series:
    ean = frame["EAN-code"].map(parse_ean) if "EAN-code" in frame.columns else None
    if ean is None:
        return pd.Series([""] * len(frame), index=frame.index)
    return ean.fillna("")


def detect_roles(frames: list[pd.DataFrame], issues: IssueLog) -> dict[str, RoleSeries]:
    if not frames:
        issues.fatal("NO_INPUTS", "No Fluvius export frames were provided")
        return {}

    combined = pd.concat(frames, ignore_index=True)
    combined["Register"] = combined["Register"].astype(str).str.strip()
    combined["Eenheid"] = combined["Eenheid"].astype(str).str.strip()
    combined["_ean"] = _series_key(combined)

    unused: list[dict] = []
    selected: dict[str, RoleSeries] = {}

    grouped = combined.groupby("Register", dropna=False)
    for register, group in grouped:
        role = REGISTER_TO_ROLE.get(str(register))
        if role is None:
            eans = sorted({ean for ean in group["_ean"].tolist() if ean})
            unused.append(
                {
                    "register": str(register),
                    "eans": eans,
                    "n_rows": int(len(group)),
                    "units": sorted({unit for unit in group["Eenheid"].tolist() if unit}),
                }
            )
            continue

        by_ean = list(group.groupby("_ean", dropna=False))
        if len(by_ean) != 1:
            issues.fatal(
                "AMBIGUOUS_REGISTER",
                (
                    f"More than one candidate series for required register "
                    f"{register!r}; explicit selection is required"
                ),
                register=str(register),
                role=role,
                eans=[ean or None for ean, _ in by_ean],
            )
            continue

        ean_value, series = by_ean[0]
        units = sorted({unit for unit in series["Eenheid"].tolist() if unit})
        if units != [REQUIRED_UNIT]:
            issues.fatal(
                "UNEXPECTED_UNIT",
                f"{register} must use unit {REQUIRED_UNIT}, found {units or ['<empty>']}",
                register=str(register),
                role=role,
                units=units,
            )
            continue

        work = series.drop(columns=["_ean"]).copy()
        work["energy_kwh"] = work["Volume"].map(parse_volume)
        work["ean"] = parse_ean(ean_value) if ean_value else None
        selected[role] = RoleSeries(
            role=role,
            register=str(register),
            ean=parse_ean(ean_value) if ean_value else None,
            unit=REQUIRED_UNIT,
            frame=work,
            unused_peers=[],
        )

    for role, register in ROLE_REGISTERS.items():
        if role not in selected and not any(
            item.code == "AMBIGUOUS_REGISTER" and item.details.get("role") == role
            for item in issues.fatals
        ):
            issues.fatal(
                "MISSING_REGISTER",
                f"Required register {register!r} was not found in the supplied exports",
                role=role,
                register=register,
            )

    if unused:
        issues.warning(
            "UNUSED_REGISTERS",
            "Additional unused registers or EANs were ignored",
            unused=unused,
        )

    eans = {role: series.ean for role, series in selected.items() if series.ean}
    if len(set(eans.values())) > 1:
        issues.warning(
            "EAN_MISMATCH",
            "The selected offtake, injection, and PV series use different EAN codes; series are joined by UTC interval, not EAN",
            eans=eans,
        )

    return selected
