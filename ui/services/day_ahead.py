"""Secondary day-ahead price filename for Step 1. Public market path only."""

from __future__ import annotations

from btm_sim.market import standard_day_ahead_prices_path

STANDARD_BASENAME = "da_prices_qh.parquet"


def day_ahead_filename() -> str | None:
    try:
        return standard_day_ahead_prices_path().name
    except Exception:
        return None
