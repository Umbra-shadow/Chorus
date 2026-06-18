# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Guardianity — Chorus (Track 3)
"""The coordinator — the leader who reconciles the orchestra.

Reads every domain report, surfaces agreements, contradictions, and gaps, and
emits a targeted follow-up prompt for any agent whose contribution needs to be
sharpened or reconciled. The orchestra runs these follow-ups for up to
``CHORUS_MAX_ROUNDS`` rounds before synthesis.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any

from engine.qwen import LLMError, QwenClient

from chorus.agent import DomainReport

log = logging.getLogger("chorus.coordinator")

_REVIEW_SYSTEM = """\
You are the coordinator (lead researcher) of a multi-domain team. Below are the
domain reports for a shared hypothesis. Your job: judge whether the team is ready
to produce a unified, buildable answer.

Identify:
- agreements: cross-domain points the team converges on,
- conflicts: contradictions between domains (name the domains),
- gaps: missing pieces no one covered.

Then decide, per domain, whether it needs a FOLLOW-UP (a precise question to
resolve a conflict or fill a gap). Only request follow-ups that genuinely matter.

Respond ONLY as JSON:
{"agreements": ["..."],
 "conflicts": [{"between": ["domainA","domainB"], "issue": "..."}],
 "gaps": ["..."],
 "followups": [{"domain": "domainA", "question": "precise follow-up"}],
 "ready": true}"""


@dataclass
class Review:
    agreements: list[str] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    followups: list[dict] = field(default_factory=list)
    ready: bool = True

    def public(self) -> dict[str, Any]:
        return asdict(self)


def _reports_block(reports: list[DomainReport]) -> str:
    blocks = []
    for r in reports:
        blocks.append(
            f"### {r.domain} ({r.role})\n"
            f"summary: {r.summary}\n"
            f"claims: {'; '.join(r.claims)}\n"
            f"risks: {'; '.join(r.risks)}\n"
            f"dependencies: {'; '.join(r.dependencies)}"
            + (f"\ncost: {r.cost_notes}" if r.cost_notes else "")
        )
    return "\n\n".join(blocks)


async def review(qwen: QwenClient, hypothesis: str, reports: list[DomainReport]) -> Review:
    try:
        data = await qwen.chat_json(
            [{"role": "system", "content": _REVIEW_SYSTEM},
             {"role": "user", "content": f"HYPOTHESIS:\n{hypothesis}\n\nREPORTS:\n{_reports_block(reports)}"}],
            temperature=0.3, max_tokens=16384,
        )
    except LLMError as e:
        log.warning("coordinator review failed, proceeding to synthesis: %s", e)
        return Review(ready=True)

    rv = Review(
        agreements=[str(x).strip() for x in (data.get("agreements") or []) if str(x).strip()],
        conflicts=[c for c in (data.get("conflicts") or []) if isinstance(c, dict)],
        gaps=[str(x).strip() for x in (data.get("gaps") or []) if str(x).strip()],
        followups=[f for f in (data.get("followups") or []) if isinstance(f, dict) and f.get("domain")],
        ready=bool(data.get("ready", True)),
    )
    return rv
