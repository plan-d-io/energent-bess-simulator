"""Validated battery configuration for version 1 PV-only dispatch."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass


class BatteryConfigError(ValueError):
    """Invalid battery parameters."""


@dataclass(frozen=True)
class BatteryConfig:
    """AC-side battery parameters. Charge and discharge ratings stay separate."""

    e_usable_kwh: float
    p_charge_kw: float
    p_discharge_kw: float
    eta_charge: float
    eta_discharge: float
    soc_initial_kwh: float = 0.0
    max_equivalent_full_cycles_per_year: float = 400.0

    def __post_init__(self) -> None:
        for name, value in (
            ("e_usable_kwh", self.e_usable_kwh),
            ("p_charge_kw", self.p_charge_kw),
            ("p_discharge_kw", self.p_discharge_kw),
            ("soc_initial_kwh", self.soc_initial_kwh),
            ("max_equivalent_full_cycles_per_year", self.max_equivalent_full_cycles_per_year),
        ):
            number = float(value)
            if not math.isfinite(number):
                raise BatteryConfigError(f"{name} must be a finite number, got {value}")
            object.__setattr__(self, name, number)
            if number < 0:
                raise BatteryConfigError(f"{name} must be >= 0, got {value}")
        for name, value in (("eta_charge", self.eta_charge), ("eta_discharge", self.eta_discharge)):
            number = float(value)
            if not math.isfinite(number):
                raise BatteryConfigError(f"{name} must be a finite number, got {value}")
            object.__setattr__(self, name, number)
            if not 0 < number <= 1:
                raise BatteryConfigError(f"{name} must be in (0, 1], got {value}")
        if self.soc_initial_kwh - self.e_usable_kwh > 1e-12:
            raise BatteryConfigError(
                f"soc_initial_kwh {self.soc_initial_kwh} exceeds e_usable_kwh {self.e_usable_kwh}"
            )
        if self.e_usable_kwh == 0 and self.soc_initial_kwh != 0:
            raise BatteryConfigError("soc_initial_kwh must be 0 when usable capacity is 0")

    @classmethod
    def with_symmetric_power(
        cls,
        e_usable_kwh: float,
        power_kw: float,
        eta_charge: float,
        eta_discharge: float,
        soc_initial_kwh: float = 0.0,
        max_equivalent_full_cycles_per_year: float = 400.0,
    ) -> BatteryConfig:
        return cls(
            e_usable_kwh=e_usable_kwh,
            p_charge_kw=power_kw,
            p_discharge_kw=power_kw,
            eta_charge=eta_charge,
            eta_discharge=eta_discharge,
            soc_initial_kwh=soc_initial_kwh,
            max_equivalent_full_cycles_per_year=max_equivalent_full_cycles_per_year,
        )

    def to_dict(self) -> dict[str, float]:
        return asdict(self)
