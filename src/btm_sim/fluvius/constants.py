"""Documented Fluvius ingestion constants.

Numerical thresholds are not named in docs/DATA_CONTRACT.md. The values below
are the implementation defaults and are copied into every validation report.
"""

from __future__ import annotations

from datetime import timedelta
from zoneinfo import ZoneInfo

TZ_NAME = "Europe/Brussels"
TZ = ZoneInfo(TZ_NAME)
INTERVAL_HOURS = 0.25
INTERVAL = timedelta(minutes=15)
PV_SOURCE_MEASURED = "measured_fluvius"

REQUIRED_COLUMNS = (
    "Van (datum)",
    "Van (tijdstip)",
    "Tot (datum)",
    "Tot (tijdstip)",
    "Register",
    "Volume",
    "Eenheid",
    "Validatiestatus",
)

OPTIONAL_METADATA_COLUMNS = ("EAN-code", "Meter", "Metertype", "Omschrijving")

ROLE_REGISTERS = {
    "offtake": "Afname Actief",
    "injection": "Injectie Actief",
    "pv": "Productie Actief",
}
REGISTER_TO_ROLE = {register: role for role, register in ROLE_REGISTERS.items()}
REQUIRED_UNIT = "kWh"

STATUS_TO_QUALITY = {
    "Gevalideerd": "validated",
    "Ongevalideerd": "unvalidated",
    "Geen gegevens": "unavailable",
}
QUALITY_RANK = {"validated": 0, "unvalidated": 1, "unavailable": 2}

# Float noise vs documented rounding vs material physical-balance failure.
FLOAT_EPS_KWH = 1e-9
DOCUMENTED_TOLERANCE_KWH = 0.001
MATERIAL_IMBALANCE_KWH = 0.05

CANONICAL_COLUMNS = (
    "timestamp_utc",
    "timestamp_local",
    "interval_hours",
    "grid_import_baseline_kwh",
    "grid_export_baseline_kwh",
    "pv_production_kwh",
    "site_load_kwh",
    "offtake_quality",
    "injection_quality",
    "pv_quality",
    "quality_flag",
    "pv_source",
)
