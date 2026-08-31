"""Candidate modes, validation, and the frozen ordered list."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Literal, Sequence

from btm_sim.sweep.exceptions import SweepRequestError

CandidateMode = Literal["automatic", "manual_range", "explicit"]
MODE_AUTOMATIC = "automatic"
MODE_MANUAL_RANGE = "manual_range"
MODE_EXPLICIT = "explicit"
ALLOWED_MODES = (MODE_AUTOMATIC, MODE_MANUAL_RANGE, MODE_EXPLICIT)
MAX_CANDIDATES = 100
MIN_CANDIDATES = 1
POWER_ENERGY_DECIMALS = 9


@dataclass(frozen=True)
class SweepCandidate:
    candidate_id: str
    power_kw: float
    usable_energy_kwh: float
    duration_hours: float
    exceeds_p95_daily_pv_surplus: bool
    exceeds_p95_daily_import: bool
    source: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def size_label(self) -> str:
        return f"{_display_number(self.power_kw)} kW / {_display_number(self.usable_energy_kwh)} kWh"


@dataclass(frozen=True)
class CandidateBuild:
    candidates: tuple[SweepCandidate, ...]
    mode: str
    removed_duplicates: tuple[dict[str, float], ...]
    durations_hours: tuple[float, ...]


def candidate_from_mapping(values: dict[str, Any]) -> SweepCandidate:
    return SweepCandidate(
        candidate_id=str(values["candidate_id"]),
        power_kw=float(values["power_kw"]),
        usable_energy_kwh=float(values["usable_energy_kwh"]),
        duration_hours=float(values["duration_hours"]),
        exceeds_p95_daily_pv_surplus=bool(values.get("exceeds_p95_daily_pv_surplus", False)),
        exceeds_p95_daily_import=bool(values.get("exceeds_p95_daily_import", False)),
        source=str(values.get("source") or MODE_EXPLICIT),
    )


def parse_mode(value: str | None) -> str:
    mode = MODE_AUTOMATIC if value is None else str(value).strip()
    if mode not in ALLOWED_MODES:
        raise SweepRequestError(
            f"Unknown sweep mode {mode!r}; expected one of {', '.join(ALLOWED_MODES)}",
            category="invalid_request",
        )
    return mode


def parse_durations(raw: Any, *, name: str = "durations") -> tuple[float, ...]:
    if raw is None:
        raise SweepRequestError(f"{name} must not be empty", category="invalid_configuration")
    if isinstance(raw, str):
        parts = [part.strip() for part in raw.split(",") if part.strip()]
        values = [_positive_finite(part, name) for part in parts]
    elif isinstance(raw, (list, tuple)):
        values = [_positive_finite(item, name) for item in raw]
    else:
        raise SweepRequestError(f"{name} must be a list of positive numbers", category="invalid_configuration")
    if not values:
        raise SweepRequestError(f"{name} must not be empty", category="invalid_configuration")
    rounded = [round(value, 12) for value in values]
    if len(set(rounded)) != len(rounded):
        raise SweepRequestError(f"{name} values must be unique", category="invalid_configuration")
    return tuple(sorted(values))


def build_candidates(
    *,
    mode: str,
    durations_hours: Sequence[float],
    automatic_candidates: Sequence[SweepCandidate],
    site_p95_daily_import_kwh: float | None,
    site_p95_daily_surplus_kwh: float | None,
    min_power_kw: float | None = None,
    max_power_kw: float | None = None,
    power_increment_kw: float | None = None,
    explicit_pairs: Sequence[tuple[float, float]] | None = None,
    no_revenue_shifting_opportunity: bool = False,
) -> CandidateBuild:
    """Build the ordered, de-duplicated candidate list for one request mode."""
    mode = parse_mode(mode)
    durations = parse_durations(list(durations_hours), name="durations")
    raw: list[tuple[float, float, float, str]] = []
    if mode == MODE_AUTOMATIC:
        if no_revenue_shifting_opportunity or not automatic_candidates:
            raise SweepRequestError(
                "Automatic sizing cannot infer useful battery sizes because the "
                "selected period has no_revenue_shifting_opportunity. Supply an "
                "explicit candidate list, or choose a period with both import and "
                "PV surplus.",
                category="invalid_request",
            )
        for item in automatic_candidates:
            raw.append((item.power_kw, item.usable_energy_kwh, item.duration_hours, MODE_AUTOMATIC))
    elif mode == MODE_MANUAL_RANGE:
        raw.extend(
            _manual_range_pairs(
                durations,
                min_power_kw=min_power_kw,
                max_power_kw=max_power_kw,
                power_increment_kw=power_increment_kw,
            )
        )
    else:
        raw.extend(_explicit_pairs(explicit_pairs))

    cleaned: list[tuple[float, float, float, str]] = []
    removed: list[dict[str, float]] = []
    seen: set[tuple[float, float]] = set()
    for power, energy, duration, source in raw:
        key = (_round_qty(power), _round_qty(energy))
        if key in seen:
            removed.append({"power_kw": power, "usable_energy_kwh": energy})
            continue
        seen.add(key)
        cleaned.append((power, energy, duration, source))

    if mode != MODE_EXPLICIT:
        cleaned.sort(key=lambda item: (item[2], item[0], item[1]))
    if len(cleaned) < MIN_CANDIDATES:
        raise SweepRequestError("The resolved candidate list must contain at least one battery size")
    if len(cleaned) > MAX_CANDIDATES:
        raise SweepRequestError(
            f"The resolved candidate list has {len(cleaned)} sizes; the limit is {MAX_CANDIDATES}",
            category="invalid_request",
        )
    candidates = tuple(
        SweepCandidate(
            candidate_id=_candidate_id(index, power, energy),
            power_kw=power,
            usable_energy_kwh=energy,
            duration_hours=duration,
            exceeds_p95_daily_pv_surplus=_exceeds(energy, site_p95_daily_surplus_kwh),
            exceeds_p95_daily_import=_exceeds(energy, site_p95_daily_import_kwh),
            source=source,
        )
        for index, (power, energy, duration, source) in enumerate(cleaned, start=1)
    )
    return CandidateBuild(
        candidates=candidates,
        mode=mode,
        removed_duplicates=tuple(removed),
        durations_hours=durations,
    )


def attach_daily_diagnostics(
    candidates: Sequence[SweepCandidate],
    *,
    site_p95_daily_import_kwh: float | None,
    site_p95_daily_surplus_kwh: float | None,
) -> tuple[SweepCandidate, ...]:
    return tuple(
        SweepCandidate(
            candidate_id=item.candidate_id,
            power_kw=item.power_kw,
            usable_energy_kwh=item.usable_energy_kwh,
            duration_hours=item.duration_hours,
            exceeds_p95_daily_pv_surplus=_exceeds(item.usable_energy_kwh, site_p95_daily_surplus_kwh),
            exceeds_p95_daily_import=_exceeds(item.usable_energy_kwh, site_p95_daily_import_kwh),
            source=item.source,
        )
        for item in candidates
    )


def _manual_range_pairs(
    durations: Sequence[float],
    *,
    min_power_kw: float | None,
    max_power_kw: float | None,
    power_increment_kw: float | None,
) -> list[tuple[float, float, float, str]]:
    minimum = _positive_finite(min_power_kw, "min_power_kw")
    maximum = _positive_finite(max_power_kw, "max_power_kw")
    increment = _positive_finite(power_increment_kw, "power_increment_kw")
    if maximum + 1e-12 < minimum:
        raise SweepRequestError("max_power_kw must be at least min_power_kw", category="invalid_request")
    powers: list[float] = []
    steps = int(math.floor((maximum - minimum) / increment + 1e-9)) + 1
    for index in range(steps):
        power = _round_qty(minimum + index * increment)
        if power <= 0:
            continue
        if power > maximum + 1e-9:
            break
        powers.append(power)
    if not powers:
        raise SweepRequestError("manual_range did not produce any positive power steps")
    pairs: list[tuple[float, float, float, str]] = []
    for duration in durations:
        for power in powers:
            energy = _round_qty(power * duration)
            pairs.append((power, energy, float(duration), MODE_MANUAL_RANGE))
    return pairs


def _explicit_pairs(
    explicit_pairs: Sequence[tuple[float, float]] | None,
) -> list[tuple[float, float, float, str]]:
    if not explicit_pairs:
        raise SweepRequestError(
            "explicit mode requires at least one (power_kw, usable_energy_kwh) candidate",
            category="invalid_request",
        )
    pairs: list[tuple[float, float, float, str]] = []
    for item in explicit_pairs:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise SweepRequestError(
                "Each explicit candidate must be (power_kw, usable_energy_kwh)",
                category="invalid_request",
            )
        power = _positive_finite(item[0], "power_kw")
        energy = _positive_finite(item[1], "usable_energy_kwh")
        duration = energy / power
        if not math.isfinite(duration) or duration <= 0:
            raise SweepRequestError(
                "Each explicit candidate must have a positive derived duration",
                category="invalid_request",
            )
        pairs.append((power, energy, duration, MODE_EXPLICIT))
    return pairs


def parse_explicit_pairs(raw: Any) -> tuple[tuple[float, float], ...]:
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)):
        raise SweepRequestError("explicit candidates must be a list", category="invalid_request")
    pairs: list[tuple[float, float]] = []
    for item in raw:
        if isinstance(item, str):
            parts = [part.strip() for part in item.split(",")]
            if len(parts) != 2:
                raise SweepRequestError(
                    "Each --candidate value must be POWER,ENERGY",
                    category="invalid_request",
                )
            pairs.append((_positive_finite(parts[0], "power_kw"), _positive_finite(parts[1], "usable_energy_kwh")))
        elif isinstance(item, dict):
            pairs.append(
                (
                    _positive_finite(item.get("power_kw"), "power_kw"),
                    _positive_finite(item.get("usable_energy_kwh"), "usable_energy_kwh"),
                )
            )
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            pairs.append((_positive_finite(item[0], "power_kw"), _positive_finite(item[1], "usable_energy_kwh")))
        else:
            raise SweepRequestError(
                "Each explicit candidate must be (power_kw, usable_energy_kwh)",
                category="invalid_request",
            )
    return tuple(pairs)


def _candidate_id(index: int, power_kw: float, energy_kwh: float) -> str:
    return f"c{index:03d}_{_id_number(power_kw)}kW_{_id_number(energy_kwh)}kWh"


def _id_number(value: float) -> str:
    text = f"{value:.6g}"
    return text.replace("+", "").replace(".", "p")


def _display_number(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.6g}"


def _positive_finite(value: Any, name: str) -> float:
    if value is None:
        raise SweepRequestError(f"{name} is required", category="invalid_request")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise SweepRequestError(f"{name} must be a finite number, got {value!r}", category="invalid_request") from exc
    if not math.isfinite(number):
        raise SweepRequestError(f"{name} must be a finite number, got {value!r}", category="invalid_request")
    if number <= 0:
        raise SweepRequestError(f"{name} must be > 0", category="invalid_request")
    return number


def _round_qty(value: float) -> float:
    return float(round(value, POWER_ENERGY_DECIMALS))


def _exceeds(energy_kwh: float, threshold: float | None) -> bool:
    if threshold is None:
        return False
    return energy_kwh > float(threshold) + 1e-12


def iter_candidate_dicts(candidates: Iterable[SweepCandidate]) -> list[dict[str, Any]]:
    return [item.to_dict() for item in candidates]
