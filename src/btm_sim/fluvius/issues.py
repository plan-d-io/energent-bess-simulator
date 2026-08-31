from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Severity = Literal["fatal", "warning"]


@dataclass
class Issue:
    severity: Severity
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
        }
        if self.details:
            payload["details"] = self.details
        return payload


class IssueLog:
    def __init__(self) -> None:
        self.items: list[Issue] = []

    def fatal(self, code: str, message: str, **details: Any) -> None:
        self.items.append(Issue("fatal", code, message, details))

    def warning(self, code: str, message: str, **details: Any) -> None:
        self.items.append(Issue("warning", code, message, details))

    @property
    def fatals(self) -> list[Issue]:
        return [item for item in self.items if item.severity == "fatal"]

    @property
    def warnings(self) -> list[Issue]:
        return [item for item in self.items if item.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.fatals

    def extend(self, other: IssueLog) -> None:
        self.items.extend(other.items)
