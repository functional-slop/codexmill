"""ORM models (ADR 0026 + 0025). The library tables mirror the pre-ORM schema; the users table is
the multi-user/UAC model."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from codexmill.web.db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(UTC)


class Bible(Base):
    """A saved story bible or series (``kind``), owner-scoped, with usage totals."""

    __tablename__ = "bibles"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)  # UTC ISO-8601
    title: Mapped[str] = mapped_column(Text, nullable=False)
    genre: Mapped[str] = mapped_column(Text, nullable=False)
    spec_json: Mapped[str] = mapped_column(Text, nullable=False)
    bible_json: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="book")
    tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    gen_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # Which model produced this (the most recent generate/regenerate). A token count is meaningless
    # without it once users are on different models/keys. Empty for rows predating this column.
    model: Mapped[str] = mapped_column(String(120), nullable=False, default="")


class RateEvent(Base):
    """One generation event per row, for the opt-in per-owner quota (ADR 0022)."""

    __tablename__ = "rate_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner: Mapped[str] = mapped_column(String(320), nullable=False)
    ts: Mapped[str] = mapped_column(String(40), nullable=False)  # UTC ISO-8601

    __table_args__ = (Index("ix_rate_owner_ts", "owner", "ts"),)


class User(Base):
    """A local or OIDC-provisioned account (ADR 0025). Identity for OIDC users is the immutable
    ``(oidc_iss, oidc_sub)`` pair; ownership of bibles references ``id`` (a stable UUID) so a rename
    or email change never orphans data. ``password_hash`` is nullable so the root account can be put
    into a blank-login recovery state."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    username: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="user")  # root|admin|user
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    oidc_iss: Mapped[str | None] = mapped_column(String(320), nullable=True)
    oidc_sub: Mapped[str | None] = mapped_column(String(320), nullable=True)
    permissions: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    # Per-user "bring your own" cloud AI key (ADR 0027): {provider, base_url, model, api_key} with
    # api_key sealed at rest. provider is allow-listed (web.providers) and base_url is derived from
    # it, so a user can't point the server at an arbitrary endpoint. Read/written only via the
    # session-scoped Users.*_user_llm helpers; None means "no personal key, use the shared AI".
    llm: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True, default=None)
    # Per-identity session-revocation token: rotating it invalidates every issued cookie (ADR 0024).
    session_epoch: Mapped[str] = mapped_column(String(32), nullable=False, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (UniqueConstraint("oidc_iss", "oidc_sub", name="uq_users_oidc"),)
