# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Guardianity — Chorus (Track 3)
"""The analyzer — read the question, form the hypothesis, cast the agents.

Given a top-level question ("how can we cure blindness?"), the analyzer decides
*which expert domains* the problem needs and writes a brief for each. Finance is
always cast (every real build has a cost). The result is a small, capped team of
domain specialists that will research in parallel.
"""

from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass, field
from typing import Any

from engine.qwen import LLMError, QwenClient

log = logging.getLogger("chorus.analyzer")

_CAST_SYSTEM = """\
You are the analyzer of a multi-agent research orchestra. Given a hard, real-world
question, decide which EXPERT DOMAINS are needed to truly answer it and, together,
design a buildable solution. Think like assembling a project team for, say, a
prosthetic eye: neurology, optics, materials/mechanics, software, regulatory, etc.

Rules:
- Choose only domains that genuinely add a distinct expert view (no duplicates).
- ALWAYS include a "finance" domain (cost, BOM, feasibility).
- For each domain, give 2–4 sharp focus questions that agent should research.
- Return AT MOST %(max)d domains.

Respond ONLY as JSON:
{"hypothesis": "one-paragraph research hypothesis / framing",
 "agents": [
   {"domain": "neurology", "role": "Neuro-interface specialist",
    "why": "why this domain is needed",
    "focus": ["question 1", "question 2"]}
 ]}"""


@dataclass(frozen=True, slots=True)
class AgentBrief:
    domain: str
    role: str
    why: str
    focus: list[str] = field(default_factory=list)

    def public(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class Cast:
    hypothesis: str
    agents: list[AgentBrief]

    def public(self) -> dict[str, Any]:
        return {"hypothesis": self.hypothesis, "agents": [a.public() for a in self.agents]}


def _max_agents() -> int:
    try:
        return max(2, min(10, int(os.environ.get("CHORUS_MAX_AGENTS", "6"))))
    except ValueError:
        return 6


async def cast_agents(qwen: QwenClient, question: str, context: str = "") -> Cast:
    """One Qwen call → hypothesis + the domain team to research it.

    ``context`` is the Stage-1 conclusion (the Kioku-style first research). When
    present, the analyzer casts the team informed by what that pass already found,
    so Stage 2 goes *deeper* rather than repeating Stage 1.
    """
    cap = _max_agents()
    user = f"QUESTION:\n{question}"
    if context.strip():
        user += f"\n\nFIRST-PASS RESEARCH CONCLUSION (build deeper on this):\n{context.strip()}"
    raw = await qwen.chat_json(
        [
            {"role": "system", "content": _CAST_SYSTEM % {"max": cap}},
            {"role": "user", "content": user},
        ],
        temperature=0.4,
        max_tokens=16384,
    )
    hypothesis = str(raw.get("hypothesis", "")).strip()
    rows = raw.get("agents") if isinstance(raw, dict) else None
    if not isinstance(rows, list) or not rows:
        raise LLMError("analyzer produced no agents")

    agents: list[AgentBrief] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        domain = str(row.get("domain", "")).strip().lower()
        if not domain or domain in seen:
            continue
        seen.add(domain)
        focus = [str(f).strip() for f in (row.get("focus") or []) if str(f).strip()]
        agents.append(AgentBrief(
            domain=domain,
            role=str(row.get("role", domain)).strip() or domain,
            why=str(row.get("why", "")).strip(),
            focus=focus[:4],
        ))
        if len(agents) >= cap:
            break

    # Finance is always present — every real build has a cost.
    if "finance" not in seen and len(agents) < cap:
        agents.append(AgentBrief(
            domain="finance", role="Cost & feasibility analyst",
            why="Every buildable solution needs a cost model and feasibility check.",
            focus=["What are the major cost drivers and an estimated budget range?",
                   "What is the feasibility and time-to-build given current technology?"],
        ))
    if not hypothesis:
        hypothesis = f"Investigate and design a buildable answer to: {question}"
    log.info("analyzer cast %d agents: %s", len(agents), ", ".join(a.domain for a in agents))
    return Cast(hypothesis=hypothesis, agents=agents)
