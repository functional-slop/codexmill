"""ORM models (ADR 0026). Phase 1 mirrors the existing library schema so the port is behaviour-
preserving; later phases add the user/settings tables."""

from __future__ import annotations

from sqlalchemy import Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from codexmill.web.db import Base


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


class RateEvent(Base):
    """One generation event per row, for the opt-in per-owner quota (ADR 0022)."""

    __tablename__ = "rate_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner: Mapped[str] = mapped_column(String(320), nullable=False)
    ts: Mapped[str] = mapped_column(String(40), nullable=False)  # UTC ISO-8601

    __table_args__ = (Index("ix_rate_owner_ts", "owner", "ts"),)
