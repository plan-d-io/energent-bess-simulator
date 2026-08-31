"""Builders for compact synthetic Fluvius CSV fixtures."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from btm_sim.fluvius.constants import INTERVAL, ROLE_REGISTERS, TZ_NAME

TZ = ZoneInfo(TZ_NAME)
UTC = timezone.utc
COLUMNS = [
    "Van (datum)",
    "Van (tijdstip)",
    "Tot (datum)",
    "Tot (tijdstip)",
    "EAN-code",
    "Meter",
    "Metertype",
    "Register",
    "Volume",
    "Eenheid",
    "Validatiestatus",
    "Omschrijving",
]


def qh_range(start: datetime, n: int) -> list[datetime]:
    return [start + i * INTERVAL for i in range(n)]


def format_volume(value: float | None) -> str:
    if value is None:
        return ""
    text = f"{value:.3f}".replace(".", ",")
    return text


def wall_clock(
    ts_utc: datetime,
    *,
    date_sep: str = "-",
    pad_hours: bool = True,
    include_seconds: bool = True,
) -> tuple[str, str]:
    local = ts_utc.astimezone(TZ)
    date_text = local.strftime(f"%d{date_sep}%m{date_sep}%Y")
    if pad_hours:
        time_text = local.strftime("%H:%M:%S" if include_seconds else "%H:%M")
    elif include_seconds:
        time_text = f"{local.hour}:{local.minute:02d}:{local.second:02d}"
    else:
        time_text = f"{local.hour}:{local.minute:02d}"
    return date_text, time_text


def rows_for_series(
    utc_starts: list[datetime],
    volumes: list[float | None],
    statuses: list[str],
    *,
    register: str,
    unit: str = "kWh",
    ean: str = "111",
    description: str = "",
    date_sep: str = "-",
    pad_hours: bool = True,
    include_seconds: bool = True,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for start, volume, status in zip(utc_starts, volumes, statuses, strict=True):
        end = start + INTERVAL
        van_d, van_t = wall_clock(
            start, date_sep=date_sep, pad_hours=pad_hours, include_seconds=include_seconds
        )
        tot_d, tot_t = wall_clock(
            end, date_sep=date_sep, pad_hours=pad_hours, include_seconds=include_seconds
        )
        rows.append(
            {
                "Van (datum)": van_d,
                "Van (tijdstip)": van_t,
                "Tot (datum)": tot_d,
                "Tot (tijdstip)": tot_t,
                "EAN-code": f'="{ean}"',
                "Meter": "",
                "Metertype": "AMR-meter",
                "Register": register,
                "Volume": format_volume(volume),
                "Eenheid": unit,
                "Validatiestatus": status,
                "Omschrijving": description,
            }
        )
    return rows


def write_fluvius_csv(path: Path, rows: list[dict[str, str]]) -> Path:
    lines = [";".join(COLUMNS)]
    for row in rows:
        lines.append(";".join(row.get(column, "") for column in COLUMNS))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_role_export(
    path: Path,
    role: str,
    utc_starts: list[datetime],
    volumes: list[float | None],
    statuses: list[str] | str = "Gevalideerd",
    *,
    ean: str = "111",
    extra_reactive: bool = True,
    extra_rows: list[dict[str, str]] | None = None,
    date_sep: str = "-",
    pad_hours: bool = True,
    include_seconds: bool = True,
) -> Path:
    if isinstance(statuses, str):
        statuses = [statuses] * len(utc_starts)
    register = ROLE_REGISTERS[role]
    clock_kw = {
        "date_sep": date_sep,
        "pad_hours": pad_hours,
        "include_seconds": include_seconds,
    }
    rows = rows_for_series(utc_starts, volumes, statuses, register=register, ean=ean, **clock_kw)
    if extra_reactive:
        reactive_register = register.replace("Actief", "Reactief")
        rows.extend(
            rows_for_series(
                utc_starts,
                [0.0] * len(utc_starts),
                statuses,
                register=reactive_register,
                unit="kVArh",
                ean=ean,
                **clock_kw,
            )
        )
    if extra_rows:
        rows.extend(extra_rows)
    return write_fluvius_csv(path, rows)


def write_site(
    directory: Path,
    utc_starts: list[datetime],
    *,
    import_kwh: list[float | None],
    export_kwh: list[float | None],
    pv_kwh: list[float | None],
    statuses: list[str] | str = "Gevalideerd",
    offtake_ean: str = "100",
    injection_ean: str = "200",
    pv_ean: str = "300",
    hulp_ean: str = "301",
    names: tuple[str, str, str] = ("offtake.csv", "injection.csv", "pv.csv"),
    date_sep: str = "-",
    pad_hours: bool = True,
    include_seconds: bool = True,
) -> tuple[Path, Path, Path]:
    clock_kw = {
        "date_sep": date_sep,
        "pad_hours": pad_hours,
        "include_seconds": include_seconds,
    }
    offtake = write_role_export(
        directory / names[0],
        "offtake",
        utc_starts,
        import_kwh,
        statuses,
        ean=offtake_ean,
        **clock_kw,
    )
    injection = write_role_export(
        directory / names[1],
        "injection",
        utc_starts,
        export_kwh,
        statuses,
        ean=injection_ean,
        **clock_kw,
    )
    extra = rows_for_series(
        utc_starts,
        [0.0] * len(utc_starts),
        ["Gevalideerd"] * len(utc_starts) if isinstance(statuses, str) else statuses,
        register="Hulpverbruik Actief",
        ean=hulp_ean,
        description="auxiliary",
        **clock_kw,
    )
    pv = write_role_export(
        directory / names[2],
        "pv",
        utc_starts,
        pv_kwh,
        statuses,
        ean=pv_ean,
        extra_rows=extra,
        **clock_kw,
    )
    return offtake, injection, pv


SPRING_STARTS = [
    datetime(2024, 3, 31, 0, 0, tzinfo=UTC),
    datetime(2024, 3, 31, 0, 15, tzinfo=UTC),
    datetime(2024, 3, 31, 0, 30, tzinfo=UTC),
    datetime(2024, 3, 31, 0, 45, tzinfo=UTC),  # local 01:45-03:00
    datetime(2024, 3, 31, 1, 0, tzinfo=UTC),
]

AUTUMN_STARTS = [
    datetime(2024, 10, 26, 23, 45, tzinfo=UTC),  # 01:45 CEST
    datetime(2024, 10, 27, 0, 0, tzinfo=UTC),  # 02:00 CEST
    datetime(2024, 10, 27, 0, 15, tzinfo=UTC),
    datetime(2024, 10, 27, 0, 30, tzinfo=UTC),
    datetime(2024, 10, 27, 0, 45, tzinfo=UTC),  # 02:45 CEST -> 02:00 CET
    datetime(2024, 10, 27, 1, 0, tzinfo=UTC),  # 02:00 CET
    datetime(2024, 10, 27, 1, 15, tzinfo=UTC),
    datetime(2024, 10, 27, 1, 30, tzinfo=UTC),
    datetime(2024, 10, 27, 1, 45, tzinfo=UTC),
    datetime(2024, 10, 27, 2, 0, tzinfo=UTC),  # 03:00 CET
]


def balanced_site(
    utc_starts: list[datetime],
    *,
    import_kwh: float = 1.0,
    export_kwh: float = 0.0,
    pv_kwh: float = 0.5,
) -> tuple[list[float], list[float], list[float]]:
    n = len(utc_starts)
    return [import_kwh] * n, [export_kwh] * n, [pv_kwh] * n
