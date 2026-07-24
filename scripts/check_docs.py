#!/usr/bin/env python3
"""Doc-consistency gate. Runs in pre-commit + the test suite so documentation drift can't silently
ship — the failure mode this project kept hitting (a session reads stale docs and loses the plot).

It disproves three drift categories mechanically:

  1. STAGE COUNT   — any "N stages" / word-number "stages" claim about the pipeline must equal the
     real number of pipeline stages (``codexmill.pipeline.STAGE_LABELS``).
  2. STALE ROUTES  — any ``/api/...`` or ``/auth/...`` path a current-state doc cites must be a real
     route in the web app, UNLESS the line marks it removed/gone (so "we deleted /api/x" is fine).
  3. BRITTLE FACTS — the handover docs (STATE.md, CONTINUATION.md) must NOT hardcode git commit
     hashes or "N commits ahead" counts; those go stale on the very next commit. Describe state
     qualitatively instead.

Fast: regex + one cheap import, no app instantiation, no pytest collection.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "src" / "codexmill" / "web" / "app.py"

NUM_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}
REMOVAL_WORDS = re.compile(
    r"\b(remov|delet|gone|drop|no longer|deprecat|retire|404|replaced)", re.I
)


def _stage_count() -> int:
    sys.path.insert(0, str(ROOT / "src"))
    from codexmill.pipeline import STAGE_LABELS

    return len(STAGE_LABELS)


def _actual_routes() -> set[str]:
    src = APP.read_text()
    raw = re.findall(r'@app\.(?:get|post|put|delete)\("([^"]+)"', src)
    # normalize path params ({bid} -> {}) so docs may use any placeholder name
    return {re.sub(r"\{[^}]+\}", "{}", p) for p in raw}


def _docs() -> list[Path]:
    files = [ROOT / "README.md", ROOT / "CHANGELOG.md"]
    files += sorted((ROOT / "docs").glob("*.md"))
    files += sorted((ROOT / "docs" / "adr").glob("*.md"))
    return [f for f in files if f.exists()]


def check_stage_count(errors: list[str]) -> None:
    n = _stage_count()
    pat = re.compile(r"\b(\d+|" + "|".join(NUM_WORDS) + r")[- ]stages?\b", re.I)
    for f in _docs():
        if f.name == "CHANGELOG.md":
            continue  # append-only history: old entries describe the pipeline as it was then
        for i, line in enumerate(f.read_text().splitlines(), 1):
            # the story pipeline, not the publish/staging pipeline (a different, 3-stage thing)
            if re.search(r"publish|stag(e|ing) (repo|pipeline|2)|sanitiz", line, re.I):
                continue
            for m in pat.finditer(line):
                tok = m.group(1).lower()
                claimed = int(tok) if tok.isdigit() else NUM_WORDS[tok]
                # "one/1 stage" is usually "a single stage" phrasing, not a pipeline-size claim
                if claimed != 1 and claimed != n:
                    errors.append(
                        f"{f.relative_to(ROOT)}:{i}: claims {claimed}-stage pipeline but there are "
                        f"{n} (codexmill.pipeline.STAGE_LABELS)"
                    )


def check_stale_routes(errors: list[str]) -> None:
    routes = _actual_routes()
    pat = re.compile(r"(/(?:api|auth)/[A-Za-z0-9_/{}-]+)")
    # Only the current-state docs — ADRs + CHANGELOG legitimately cite routes as they were then.
    current = [ROOT / "README.md", ROOT / "docs" / "STATE.md", ROOT / "docs" / "CONTINUATION.md"]
    for f in [c for c in current if c.exists()]:
        for i, line in enumerate(f.read_text().splitlines(), 1):
            if REMOVAL_WORDS.search(line):
                continue  # "removed /api/x" is legitimately citing something gone
            for m in pat.finditer(line):
                path = re.sub(r"\{[^}]+\}", "{}", m.group(1).rstrip("/.,)`"))
                if path not in routes and not any(r.startswith(path) for r in routes):
                    errors.append(
                        f"{f.relative_to(ROOT)}:{i}: cites route {m.group(1)} that no longer exists"
                    )


def check_brittle_facts(errors: list[str]) -> None:
    hash_pat = re.compile(r"`[0-9a-f]{7,40}`")  # git hashes in backticks
    ahead_pat = re.compile(r"\d+\s+commits?\s+ahead", re.I)
    for name in ("STATE.md", "CONTINUATION.md"):
        f = ROOT / "docs" / name
        if not f.exists():
            continue
        for i, line in enumerate(f.read_text().splitlines(), 1):
            if hash_pat.search(line):
                errors.append(
                    f"docs/{name}:{i}: hardcoded git hash — goes stale next commit; describe "
                    f"state qualitatively"
                )
            if ahead_pat.search(line):
                errors.append(
                    f"docs/{name}:{i}: hardcoded 'N commits ahead' count — goes stale; describe it"
                )


def main() -> int:
    errors: list[str] = []
    check_stage_count(errors)
    check_stale_routes(errors)
    check_brittle_facts(errors)
    if errors:
        print("Doc-consistency gate FAILED:\n", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        print(f"\n{len(errors)} issue(s) — make the docs and code agree.", file=sys.stderr)
        return 1
    print("Doc-consistency gate passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
