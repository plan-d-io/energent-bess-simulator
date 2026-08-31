from __future__ import annotations

from types import SimpleNamespace

from btm_sim.market import PriceDataError

from ui.services.price_coverage import (
    clear_price_coverage_cache,
    price_coverage_cache_key,
    price_coverage_for_payloads,
)


def setup_function() -> None:
    clear_price_coverage_cache()


def _payloads() -> tuple[tuple[str, bytes], ...]:
    return (("offtake.csv", b"aaa"), ("injection.csv", b"bbb"), ("pv.csv", b"ccc"))


class _Frame:
    def __init__(self, stamps: list[str]) -> None:
        self._stamps = stamps
        self.columns = ["timestamp_utc"]

    def __getitem__(self, key: str) -> list[str]:
        assert key == "timestamp_utc"
        return list(self._stamps)


def test_price_adapter_passes_exact_timestamps_and_no_output_dir() -> None:
    recorded: dict[str, object] = {}
    stamps = ["2024-01-01T00:00:00Z", "2024-01-01T00:15:00Z"]

    def normalize(paths, **kwargs):
        recorded["normalize_kwargs"] = kwargs
        recorded["n_paths"] = len(paths)
        return SimpleNamespace(frame=_Frame(stamps), ok=True)

    def load_prices(timestamps):
        recorded["timestamps"] = list(timestamps)
        return SimpleNamespace(
            aligned_audit=lambda: {
                "source_path": r"C:\data\market\da_prices_qh.parquet",
                "source_sha256": "abc",
                "manifest_path": r"C:\data\market\MANIFEST.json",
                "selected_row_count": 2,
                "coverage_utc": ["2015-01-01T00:00:00Z", "2026-01-01T00:00:00Z"],
                "native_resolution_counts": {"PT15M": 2},
                "hourly_values_repeated": False,
            }
        )

    snapshot = price_coverage_for_payloads(
        _payloads(),
        "2024",
        allow_unvalidated=True,
        acknowledge_site_boundary=False,
        normalize=normalize,
        load_prices=load_prices,
    )
    assert recorded["n_paths"] == 3
    assert recorded["normalize_kwargs"]["output_dir"] is None
    assert recorded["normalize_kwargs"]["period"] == "2024"
    assert recorded["timestamps"] == stamps
    assert snapshot["covered"] is True
    assert snapshot["selected_row_count"] == 2
    assert snapshot["source_basename"] == "da_prices_qh.parquet"
    assert snapshot["one_battery_unavailable"] is False
    assert "frame" not in snapshot
    assert ":\\" not in str(snapshot)
    assert "abc" not in str(snapshot.values())


def test_price_data_error_is_non_blocking_unavailable() -> None:
    def normalize(paths, **kwargs):
        return SimpleNamespace(frame=_Frame(["2024-01-01T00:00:00Z"]), ok=True)

    def load_prices(_timestamps):
        raise PriceDataError("missing timestamps")

    snapshot = price_coverage_for_payloads(
        _payloads(),
        "2024",
        allow_unvalidated=False,
        acknowledge_site_boundary=False,
        normalize=normalize,
        load_prices=load_prices,
    )
    assert snapshot["covered"] is False
    assert snapshot["unavailable"] is True
    assert snapshot["one_battery_unavailable"] is True
    assert snapshot["error"]["exception_type"] == "PriceDataError"


def test_price_cache_key_changes_with_period_acks_inputs_and_dataset() -> None:
    signature = (("a.csv", 1, "a"), ("b.csv", 1, "b"), ("c.csv", 1, "c"))
    base = price_coverage_cache_key(
        signature,
        "2024",
        allow_unvalidated=True,
        acknowledge_site_boundary=False,
        simulator_version="0.1.0",
        dataset_identity="da_prices_qh.parquet",
    )
    assert base != price_coverage_cache_key(
        signature,
        "2025",
        allow_unvalidated=True,
        acknowledge_site_boundary=False,
        simulator_version="0.1.0",
        dataset_identity="da_prices_qh.parquet",
    )
    assert base != price_coverage_cache_key(
        signature,
        "2024",
        allow_unvalidated=False,
        acknowledge_site_boundary=False,
        simulator_version="0.1.0",
        dataset_identity="da_prices_qh.parquet",
    )
    assert base != price_coverage_cache_key(
        signature,
        "2024",
        allow_unvalidated=True,
        acknowledge_site_boundary=True,
        simulator_version="0.1.0",
        dataset_identity="da_prices_qh.parquet",
    )
    assert base != price_coverage_cache_key(
        (("a.csv", 1, "z"), ("b.csv", 1, "b"), ("c.csv", 1, "c")),
        "2024",
        allow_unvalidated=True,
        acknowledge_site_boundary=False,
        simulator_version="0.1.0",
        dataset_identity="da_prices_qh.parquet",
    )
    assert base != price_coverage_cache_key(
        signature,
        "2024",
        allow_unvalidated=True,
        acknowledge_site_boundary=False,
        simulator_version="0.1.0",
        dataset_identity="other.parquet",
    )
