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
You are the analyzer of a multi-agent research orchestra. A user has a hard
real-world question. Your job is to staff the R&D project team that will BUILD
the answer — one specialist per distinct discipline required.

IMPORTANT: The first-pass research you receive is BACKGROUND KNOWLEDGE, not
completed work. It tells you what is known. Your cast tells you WHO DOES THE WORK.
These are different questions. Do not reduce the team because research exists —
instead cast every expert who must contribute to a real solution.

HOW TO THINK ABOUT THE CAST:
Ask yourself: on a real project team, who sits at the table?
  - Who designs the device/system?
  - Who manufactures or fabricates it?
  - Who implants, installs, or deploys it?
  - Who connects it to the body/brain/environment?
  - Who writes the firmware or software?
  - Who selects and qualifies the materials?
  - Who runs the electronics and sensors?
  - Who navigates regulatory and safety approval?
  - Who handles clinical trials or testing protocols?
  - Who models the cost and feasibility?
Each YES is a separate agent. Never merge two real disciplines.

EXAMPLE — "how do we make a prosthetic eye?" → 10 agents:
  ophthalmology, neuroscience, biomedical engineering, materials science,
  electronics/hardware, firmware/software, surgical procedure, regulatory affairs,
  clinical trials, finance

Rules:
- MINIMUM %(min)d agents, up to %(max)d. Most hard questions need 7–10.
- ALWAYS include "finance" (cost model, BOM, go-to-market feasibility).
- Give each agent 2–4 sharp focus questions specific to their discipline.
- Do NOT merge two real disciplines to reduce the count.
- Do NOT pad with vague or redundant domains.

Respond ONLY as JSON (no markdown, no preamble, no explanation outside the JSON):
{"hypothesis": "one-paragraph design hypothesis framing the build challenge",
 "agents": [
   {"domain": "neuroscience", "role": "Neurointerface specialist",
    "why": "one sentence on why this discipline is essential",
    "focus": ["specific question 1", "specific question 2", "specific question 3"]}
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
        return max(4, min(12, int(os.environ.get("CHORUS_MAX_AGENTS", "10"))))
    except ValueError:
        return 10


def _min_agents() -> int:
    try:
        return max(4, min(8, int(os.environ.get("CHORUS_MIN_AGENTS", "6"))))
    except ValueError:
        return 6


async def cast_agents(qwen: QwenClient, question: str, context: str = "") -> Cast:
    """One Qwen call → hypothesis + the domain team to research it.

    ``context`` is the Stage-1 conclusion (the Kioku-style first research). When
    present, the analyzer casts the team informed by what that pass already found,
    so Stage 2 goes *deeper* rather than repeating Stage 1.
    """
    cap = _max_agents()
    low = _min_agents()
    user = f"QUESTION:\n{question}"
    if context.strip():
        user += f"\n\nBACKGROUND KNOWLEDGE FROM FIRST-PASS RESEARCH (use to inform the cast, not to reduce it):\n{context.strip()}"
    raw = await qwen.chat_json(
        [
            {"role": "system", "content": _CAST_SYSTEM % {"min": low, "max": cap}},
            {"role": "user", "content": user},
        ],
        temperature=0.7,
        max_tokens=8192,
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
        focus_raw = row.get("focus")
        focus = [str(f).strip() for f in (focus_raw if isinstance(focus_raw, list) else []) if str(f).strip()]
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
                   "What is the feasibility and time-to-build given current technology?",
                   "What does the go-to-market or deployment pathway look like?"],
        ))
    if not hypothesis:
        hypothesis = f"Investigate and design a buildable answer to: {question}"
    if len(agents) < low:
        log.warning("analyzer returned only %d agents (minimum %d) — model under-cast", len(agents), low)
    log.info("analyzer cast %d agents: %s", len(agents), ", ".join(a.domain for a in agents))
    return Cast(hypothesis=hypothesis, agents=agents)
