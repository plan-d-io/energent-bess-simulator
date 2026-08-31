"""Check a Windows source installation without running an optimization."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import struct
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MATPLOTLIB_CACHE = ROOT / ".cache" / "matplotlib"
MATPLOTLIB_CACHE.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(MATPLOTLIB_CACHE))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check_demo(folder: Path) -> None:
    manifest_path = folder / "demo_manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    for record in payload.get("files") or []:
        path = folder / str(record["name"])
        if not path.is_file():
            raise RuntimeError(f"Saved demonstration file is missing: {path}")
        if path.stat().st_size != int(record["bytes"]):
            raise RuntimeError(f"Saved demonstration size does not match its manifest: {path}")
        if _sha256(path) != str(record["sha256"]):
            raise RuntimeError(f"Saved demonstration hash does not match its manifest: {path}")


def main() -> int:
    failures: list[str] = []

    if sys.version_info[:2] != (3, 13):
        failures.append(f"Python 3.13 is required; found {sys.version.split()[0]}")
    if struct.calcsize("P") * 8 != 64:
        failures.append("64-bit Python is required")

    try:
        import btm_sim
        import highspy
        import streamlit

        print(f"Simulator: {btm_sim.__version__}")
        print(f"HiGHS: {highspy.Highs().version()}")
        print(f"Streamlit: {streamlit.__version__}")
    except Exception as exc:  # pragma: no cover - exercised by installation failures
        failures.append(f"Required package import failed: {exc}")

    if importlib.util.find_spec("gurobipy") is None:
        print("Gurobi: not installed (not required)")
    else:
        print("Gurobi: installed separately (not used by production runs)")

    try:
        from btm_sim.config import load_central_defaults
        from btm_sim.market import load_day_ahead_prices
        import pandas as pd

        defaults = load_central_defaults()
        print(f"Defaults: {defaults.path}")
        prices = load_day_ahead_prices(pd.Series([pd.Timestamp("2024-01-01T00:00:00Z")]))
        print(f"Day-ahead prices: {prices.source_path}")
    except Exception as exc:
        failures.append(f"Project data check failed: {exc}")

    for name in ("ganda_cars_2024_compare", "ganda_cars_2024_sweep"):
        try:
            _check_demo(ROOT / "ui" / "demo_artifacts" / name)
        except Exception as exc:
            failures.append(str(exc))

    if not (ROOT / "ui" / "app.py").is_file():
        failures.append("The V2 Streamlit entry point is missing: ui/app.py")

    if failures:
        print("\nInstallation check failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print("Installation check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
