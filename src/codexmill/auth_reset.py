"""Offline account recovery (ADR 0025):  python -m codexmill.auth_reset

Filesystem-as-root-of-trust: whoever can run this on the host can regain access, no email needed.
Resets a local account's password (``--password``), or sets a random temporary one and prints it
(``--blank``), using the same database the server uses (``CODEXMILL_DATABASE_URL``, else the SQLite
file under ``CODEXMILL_CONFIG_DIR``).
Setting a password also rotates the session epoch, so any existing sessions are invalidated.

Examples:
  python -m codexmill.auth_reset --list
  python -m codexmill.auth_reset --password 'new-strong-password'   # resets the root account
  python -m codexmill.auth_reset --username alice --blank   # set + print a random temp password
"""

from __future__ import annotations

import argparse
import secrets
import sys

from codexmill.web.db import make_engine, resolve_url
from codexmill.web.models import User
from codexmill.web.users import Users


def _pick_target(users: Users, username: str | None) -> User | None:
    if username:
        return users.by_username(username)
    return next((u for u in users.list_all() if u.role == "root"), None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="codexmill.auth_reset", description="Recover access to a CodexMill local account."
    )
    parser.add_argument("--username", help="account to act on (default: the root account)")
    parser.add_argument("--list", action="store_true", help="list accounts and exit")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--password", help="set this as the new password")
    group.add_argument(
        "--blank",
        action="store_true",
        help="set a random temporary password and print it (quick recovery without choosing one)",
    )
    args = parser.parse_args(argv)

    users = Users(make_engine(resolve_url()))
    accounts = users.list_all()

    if args.list or not (args.password or args.blank):
        if not accounts:
            print("No accounts exist yet (finish first-run setup in the web UI).")
        for u in accounts:
            print(f"{u.username}\trole={u.role}\tactive={u.is_active}\tid={u.id}")
        if not (args.password or args.blank):
            return 0

    target = _pick_target(users, args.username)
    if target is None:
        print("account not found", file=sys.stderr)
        return 1

    if args.blank:
        temp = secrets.token_urlsafe(12)
        users.set_password(target.id, temp)
        print(
            f"Set a temporary password for '{target.username}':\n\n    {temp}\n\n"
            "Log in with it, then change it immediately. Existing sessions were invalidated."
        )
    else:
        users.set_password(target.id, args.password)
        print(f"Reset the password for '{target.username}'. Existing sessions were invalidated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
