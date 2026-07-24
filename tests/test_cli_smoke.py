"""Golden-path smoke test. Drives the REAL CLI the way a user would (subprocess, offline fake
backend) and inspects the produced bundle. This is what "verify by running the app" means;
there is deliberately no reimplementation of pipeline logic here. See docs/VERIFY.md."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_generate_produces_bundle(tmp_path: Path) -> None:
    out = tmp_path / "out"
    env = {**os.environ, "CODEXMILL_BACKEND": "fake"}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "codexmill",
            "generate",
            "--spec",
            "examples/minimal.yaml",
            "--out",
            str(out),
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, f"CLI failed: {result.stderr}"

    bundles = list(out.glob("*.md"))
    assert len(bundles) == 1, f"expected one bundle, got {bundles}"

    text = bundles[0].read_text(encoding="utf-8")
    for section in (
        "# Story Bible",
        "## Premise",
        "## KDP Metadata",
        "## Worldbuilding",
        "## Characters",
        "## Structure",
        "## Chapter Breakdowns",
        "## Writing Prompts",
    ):
        assert section in text, f"missing section {section!r} in bundle"

    # Structure + chapter-breakdown stages produced the requested chapters.
    assert "### Chapter 1:" in text
    assert "### Chapter 3:" in text
    assert "**Scene beats:**" in text


def test_series_produces_bundle(tmp_path: Path) -> None:
    out = tmp_path / "out"
    env = {**os.environ, "CODEXMILL_BACKEND": "fake"}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "codexmill",
            "series",
            "--spec",
            "examples/series.yaml",
            "--out",
            str(out),
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, f"CLI failed: {result.stderr}"
    bundles = list(out.glob("*-series.md"))
    assert len(bundles) == 1, f"expected one series bundle, got {bundles}"

    text = bundles[0].read_text(encoding="utf-8")
    for section in ("## Series Arc", "## Shared Worldbuilding", "## Recurring Cast"):
        assert section in text, f"missing section {section!r}"
    # examples/series.yaml asks for 3 books
    assert "# Book 1:" in text and "# Book 3:" in text
