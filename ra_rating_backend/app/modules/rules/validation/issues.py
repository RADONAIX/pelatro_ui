"""Issues and reports for the canonical validator.

Separate from ``schemas.ValidationIssue`` (the legacy model's HTTP shape) for one
reason: these rows get *persisted*, to ``ra_rule.rule_validation_issue``. The
legacy model cached a four-field summary on the rule
(``{"valid": false, "error_count": 3}``), which can colour the catalogue's
Validation column but cannot answer "which rules failed which check" — the
question an operator actually asks when a publish is blocked across 400 rules.

Every issue carries a ``path`` into the draft so the builder can highlight the
condition or action that failed, and a ``hint`` that says what to do about it.
A message without a hint sends the reader to the documentation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.modules.rules.constants import ValidationSeverity
from app.modules.rules.vocabulary.modes import ValidationState


@dataclass(frozen=True, slots=True)
class Issue:
    severity: str
    code: str
    message: str
    #: Dotted path into the draft — ``conditions[2].values[0]``, ``actions[0].params.rate``.
    path: str = ""
    hint: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "path": self.path,
            "hint": self.hint,
        }


def error(code: str, message: str, path: str = "", hint: str = "") -> Issue:
    return Issue(ValidationSeverity.ERROR, code, message, path, hint)


def warn(code: str, message: str, path: str = "", hint: str = "") -> Issue:
    return Issue(ValidationSeverity.WARNING, code, message, path, hint)


@dataclass(slots=True)
class Report:
    issues: list[Issue] = field(default_factory=list)

    def extend(self, more: list[Issue]) -> None:
        self.issues.extend(more)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == ValidationSeverity.ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == ValidationSeverity.WARNING]

    @property
    def valid(self) -> bool:
        """No errors. Warnings do not block — an author must be able to save a
        rule that is merely broad, or the wizard cannot be used incrementally."""
        return not self.errors

    @property
    def state(self) -> str:
        """The cached verdict for the catalogue's Validation column."""
        if self.errors:
            return ValidationState.ERROR
        if self.warnings:
            return ValidationState.WARNING
        return ValidationState.PASS

    def as_list(self) -> list[dict[str, str]]:
        return [i.as_dict() for i in self.issues]
