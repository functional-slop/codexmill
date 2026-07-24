"""The doc-consistency gate must pass in the suite too (not just pre-commit) — CI blocks drift.

This is the mechanical guard against the project's recurring failure mode: a session reading stale
docs and losing the plot. See scripts/check_docs.py.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parent.parent


def _load_gate() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_docs", ROOT / "scripts" / "check_docs.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_docs_agree_with_code() -> None:
    gate = _load_gate()
    errors: list[str] = []
    gate.check_stage_count(errors)
    gate.check_stale_routes(errors)
    gate.check_brittle_facts(errors)
    assert not errors, "doc drift detected:\n  " + "\n  ".join(errors)
