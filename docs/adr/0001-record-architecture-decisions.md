# 1. Record architecture decisions

Date: 2026-07-10 · Status: accepted

## Context
This project is built across many independent agent sessions with no shared memory. Decisions
made in one session get silently relitigated or contradicted in the next.

## Decision
Every non-trivial architecture/policy decision is recorded as a short ADR here, numbered and
append-only. Superseding is done by adding a new ADR that references the old one, not by editing
history. `docs/STATE.md` links the ones currently in force.

## Consequences
A future session can read *why* a thing is the way it is in minutes, and cannot un-decide it
without leaving a record. Cheap insurance against cross-session drift.
