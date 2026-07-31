"""Tests for the audit-log `module` taxonomy.

Pure tests (no DB): the module enum stays in lockstep with the permission
catalog, every module has a display label, and — the important regression
guard — no `record_audit()` call site in the app forgets to pass `module`.
"""

from __future__ import annotations

import ast
import pathlib

from app.core.rbac import AUDIT_MODULE_LABELS, AUDITED_MODULES, AuditModule, PermKey

APP_DIR = pathlib.Path(__file__).resolve().parent.parent / "app"


def test_audit_modules_mirror_permission_keys():
    """Every module is a PermKey, except `auth` (no permission gates sign-in)."""
    modules = {m.value for m in AuditModule}
    assert modules - {AuditModule.AUTH.value} == {k.value for k in PermKey}


def test_every_module_has_a_display_label():
    for module in AuditModule:
        assert AUDIT_MODULE_LABELS[module.value]
    assert AUDIT_MODULE_LABELS[AuditModule.AUTH.value] == "Authentication"


def _record_audit_calls() -> list[tuple[pathlib.Path, ast.Call]]:
    calls: list[tuple[pathlib.Path, ast.Call]] = []
    for path in APP_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
            if name == "record_audit":
                calls.append((path, node))
    return calls


def test_every_record_audit_call_passes_a_module():
    """A new audited action must declare its module — else the audit screen
    shows a blank Module column and the filter silently drops the row."""
    calls = _record_audit_calls()
    assert calls, "expected to find record_audit() call sites"

    missing = [
        f"{path.name}:{node.lineno}"
        for path, node in calls
        if not any(kw.arg == "module" for kw in node.keywords)
    ]
    assert not missing, f"record_audit() without module=: {missing}"


def test_audited_modules_match_the_call_sites():
    """The Module dropdown (AUDITED_MODULES) must list exactly the modules that
    are actually audited — no view-only feature leaks in, and a newly-audited
    module can't be forgotten from the filter."""
    used: set[str] = set()
    for _, node in _record_audit_calls():
        for kw in node.keywords:
            if kw.arg == "module" and isinstance(kw.value, ast.Attribute):
                used.add(getattr(AuditModule, kw.value.attr).value)
    listed = {m.value for m in AUDITED_MODULES}
    assert listed == used, f"AUDITED_MODULES {listed} != audited call sites {used}"


def test_record_audit_modules_are_enum_members():
    """Call sites pass `AuditModule.X`, never a bare string (typo-proof)."""
    for path, node in _record_audit_calls():
        for kw in node.keywords:
            if kw.arg != "module":
                continue
            value = kw.value
            where = f"{path.name}:{node.lineno}"
            assert isinstance(value, ast.Attribute), f"{where}: module= is not an enum member"
            assert isinstance(value.value, ast.Name) and value.value.id == "AuditModule"
            assert hasattr(AuditModule, value.attr), f"unknown AuditModule.{value.attr}"
