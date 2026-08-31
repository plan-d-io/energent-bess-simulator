"""Read the packaged simulator version from the one-line VERSION resource."""

from __future__ import annotations

from importlib.resources import files


class VersionResourceError(RuntimeError):
    """Raised when the packaged VERSION resource is missing or invalid."""


def read_package_version() -> str:
    resource = files(__package__).joinpath("VERSION")
    try:
        raw = resource.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise VersionResourceError("Packaged VERSION resource is missing.") from exc
    lines = raw.splitlines()
    if len(lines) != 1:
        raise VersionResourceError("Packaged VERSION must contain exactly one line.")
    value = lines[0].strip()
    if not value:
        raise VersionResourceError("Packaged VERSION is empty.")
    return value


__version__ = read_package_version()
