"""Solver-neutral preparation shared by the physical battery LPs."""

from __future__ import annotations

import pandas as pd

from btm_sim.fluvius.constants import TZ_NAME


def local_month_groups(frame: pd.DataFrame) -> tuple[list[str], list[list[int]]]:
    """Group sorted interval rows by Europe/Brussels calendar month labels."""
    local = pd.to_datetime(frame["timestamp_local"])
    if getattr(local.dt, "tz", None) is None:
        local = local.dt.tz_localize(TZ_NAME)
    else:
        local = local.dt.tz_convert(TZ_NAME)
    labels = [f"{year:04d}-{month:02d}" for year, month in zip(local.dt.year, local.dt.month, strict=True)]
    groups: dict[str, list[int]] = {}
    order: list[str] = []
    for index, label in enumerate(labels):
        if label not in groups:
            groups[label] = []
            order.append(label)
        groups[label].append(index)
    return order, [groups[label] for label in order]
