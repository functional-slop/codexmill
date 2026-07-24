"""Offline recovery CLI (ADR 0025): blank-login recovery and password reset via
python -m codexmill.auth_reset, against the same DB the server uses."""

from __future__ import annotations

from pathlib import Path

import pytest

from codexmill import auth_reset
from codexmill.web.db import make_engine, url_for_path
from codexmill.web.users import Users


def test_blank_sets_temp_password_and_reset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import re

    url = url_for_path(tmp_path / "r.db")
    monkeypatch.setenv("CODEXMILL_DATABASE_URL", url)
    users = Users(make_engine(url))
    users.create("root", "orig-pw-1234", role="root")

    # --blank sets a random temporary password and prints it; a null-hash blank login is NOT allowed
    assert auth_reset.main(["--blank"]) == 0
    m = re.search(r"\n {4}(\S+)\n", capsys.readouterr().out)
    assert m, "temp password not printed"
    temp = m.group(1)
    assert users.verify("root", temp) is not None  # the printed temp password works
    assert users.verify("root", "") is None  # blank does not
    assert users.verify("root", "orig-pw-1234") is None  # the old one was replaced

    # --password sets a chosen one
    assert auth_reset.main(["--password", "brand-new-pw-5678"]) == 0
    assert users.verify("root", "brand-new-pw-5678") is not None


def test_targets_root_by_default_and_lists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    url = url_for_path(tmp_path / "r.db")
    monkeypatch.setenv("CODEXMILL_DATABASE_URL", url)
    users = Users(make_engine(url))
    users.create("root", "root-pw-1234", role="root")
    users.create("alice", "alice-pw-1234", role="user")

    assert auth_reset.main(["--list"]) == 0
    # no --username -> the root account is the target
    assert auth_reset.main(["--password", "reset-root-9999"]) == 0
    assert users.verify("root", "reset-root-9999") is not None
    assert users.verify("alice", "alice-pw-1234") is not None  # untouched
