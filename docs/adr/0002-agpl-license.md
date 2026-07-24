# 2. License under AGPL-3.0

Date: 2026-07-10 · Status: accepted

## Context
The product this commoditizes is sold as closed "story-bible packages" and, increasingly, as
hosted web apps. The goal is to remove the moat, not to hand a free engine to the next reseller
to wrap and sell.

## Decision
License AGPL-3.0-or-later. Anyone may use, modify, and self-host freely. But the network-use
clause requires anyone who offers it as a hosted service to release their complete source under
the same terms.

## Consequences
- Individuals and writers: unaffected, full freedom.
- A would-be reseller running it as a paid SaaS must open their source, which removes the point
  of reselling it. This is the intended anti-commercialization lever.
- Some corporate adopters avoid AGPL; acceptable, they are not the audience.
- Alternative considered: MIT (max spread) — rejected because it lets resellers close and sell,
  the exact outcome we want to prevent.
