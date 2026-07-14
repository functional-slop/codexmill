"""The real entrypoint. Tests and verification drive THIS, never a scratch script."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click
import yaml

from codexmill.config import Settings
from codexmill.llm import make_backend
from codexmill.pipeline import build
from codexmill.render import render_bible, render_series, slugify
from codexmill.schemas import SeriesSpec, Spec
from codexmill.series import build_series


def _load_spec(path: Path) -> Spec:
    data: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise click.ClickException(f"spec {path} must be a YAML mapping")
    return Spec.model_validate(data)


def _load_series_spec(path: Path) -> SeriesSpec:
    data: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise click.ClickException(f"spec {path} must be a YAML mapping")
    return SeriesSpec.model_validate(data)


@click.group()
def main() -> None:
    """codexmill — generate a story bible from a one-line premise."""


@main.command()
@click.option(
    "--spec",
    "spec_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to a YAML spec (see examples/minimal.yaml).",
)
@click.option(
    "--out",
    "out_dir",
    default="out",
    type=click.Path(file_okay=False, path_type=Path),
    help="Directory to write the bundle into.",
)
def generate(spec_path: Path, out_dir: Path) -> None:
    """Generate a story-bible Markdown bundle from SPEC."""
    spec = _load_spec(spec_path)
    settings = Settings.from_env()
    backend = make_backend(settings)
    bible = build(spec, backend)

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{slugify(bible.premise.logline)}.md"
    out_path.write_text(render_bible(bible), encoding="utf-8")
    click.echo(f"wrote {out_path}")


@main.command()
@click.option(
    "--spec",
    "spec_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to a YAML series spec (see examples/series.yaml).",
)
@click.option(
    "--out",
    "out_dir",
    default="out",
    type=click.Path(file_okay=False, path_type=Path),
    help="Directory to write the series bundle into.",
)
def series(spec_path: Path, out_dir: Path) -> None:
    """Generate a multi-book series bible (shared world + carried cast) from SPEC."""
    spec = _load_series_spec(spec_path)
    backend = make_backend(Settings.from_env())
    series_bible = build_series(spec, backend)

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{slugify(series_bible.plan.series_title)}-series.md"
    out_path.write_text(render_series(series_bible), encoding="utf-8")
    click.echo(f"wrote {out_path}  ({len(series_bible.books)} books)")


@main.command()
@click.option("--host", default="127.0.0.1", help="Interface to bind.")
@click.option("--port", default=8000, type=int, help="Port to serve on.")
def serve(host: str, port: int) -> None:
    """Launch the web UI (a form + one-click generation) at http://HOST:PORT."""
    import uvicorn

    click.echo(f"CodexMill web UI on http://{host}:{port}")
    uvicorn.run("codexmill.web.app:app", host=host, port=port)
