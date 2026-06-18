# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Guardianity — Chorus (Track 3)
"""One domain specialist agent.

Given its brief (domain + focus questions) and the shared hypothesis, the agent
researches each focus question on the web (reusing the copied Kioku researcher's
``study``), reasons within its specialty, and lays a structured report on the
table. Its findings are committed to the **shared run mind**, so other agents and
the coordinator can recall them — the whole team builds on one memory.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Awaitable

from engine.qwen import LLMError, QwenClient
from engine.tenants import KiokuEngine, Mind
from engine.research.researcher import Finding, Researcher
from engine.research.websearch import WebSearch

from chorus.analyzer import AgentBrief

log = logging.getLogger("chorus.agent")

Progress = Callable[[str, dict], Awaitable[None]]

_AGENT_SYSTEM = """\
You are a %(role)s — the %(domain)s specialist on a multi-domain research team
working on this shared hypothesis:

%(hypothesis)s

You have researched your focus questions (findings below). Speaking strictly from
your domain expertise, lay your contribution on the table for the team. Be
concrete and buildable. Where you depend on another domain, name it.

Respond ONLY as JSON:
{"summary": "2-4 sentence domain take",
 "claims": ["specific, defensible claim", "..."],
 "risks": ["domain risk or open problem", "..."],
 "dependencies": ["what you need from other domains", "..."],
 "cost_notes": "only if you are the finance domain, else empty string"}"""


@dataclass
class DomainReport:
    domain: str
    role: str
    summary: str = ""
    claims: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    cost_notes: str = ""
    grounded: int = 0
    error: str = ""

    def public(self) -> dict[str, Any]:
        return asdict(self)


class DomainAgent:
    """A single specialist bound to the shared mind and the run's brain."""

    def __init__(self, engine: KiokuEngine, mind: Mind, qwen: QwenClient,
                 web: WebSearch, hypothesis: str, brief: AgentBrief,
                 progress: Progress | None = None) -> None:
        self.engine = engine
        self.mind = mind
        self.qwen = qwen
        self.brief = brief
        self.hypothesis = hypothesis
        self._progress = progress
        # Reuse the copied researcher for web-grounded study against the shared mind.
        self._researcher = Researcher(engine, mind, web, qwen=qwen)

    async def _emit(self, stage: str, detail: dict) -> None:
        if self._progress:
            try:
                await self._progress(stage, {"domain": self.brief.domain, **detail})
            except Exception:  # noqa: BLE001
                log.exception("agent progress sink failed")

    async def work(self) -> DomainReport:
        rep = DomainReport(domain=self.brief.domain, role=self.brief.role)
        await self._emit("agent_start", {"role": self.brief.role, "focus": self.brief.focus})

        # 1. Research each focus question (grounded, committed to shared memory).
        findings: list[Finding] = []
        for i, q in enumerate(self.brief.focus or [f"Key considerations for {self.brief.domain}"]):
            f = Finding(id=i + 1, question=f"[{self.brief.domain}] {q}")
            try:
                f = await self._researcher.study(f)
            except Exception as e:  # noqa: BLE001 — one bad question must not sink the agent
                log.warning("agent %s study failed: %s", self.brief.domain, e)
                f.answer = f"[error] {e}"
            findings.append(f)
        rep.grounded = sum(1 for f in findings if f.grounded)

        # 2. Synthesize this domain's position as structured JSON.
        corpus = "\n\n".join(f"FOCUS: {f.question}\n{f.answer}" for f in findings)
        sys = _AGENT_SYSTEM % {
            "role": self.brief.role, "domain": self.brief.domain, "hypothesis": self.hypothesis,
        }
        try:
            data = await self.qwen.chat_json(
                [{"role": "system", "content": sys},
                 {"role": "user", "content": f"YOUR FINDINGS:\n{corpus}"}],
                temperature=0.35, max_tokens=16384,
            )
            rep.summary = str(data.get("summary", "")).strip()
            rep.claims = [str(c).strip() for c in (data.get("claims") or []) if str(c).strip()]
            rep.risks = [str(c).strip() for c in (data.get("risks") or []) if str(c).strip()]
            rep.dependencies = [str(c).strip() for c in (data.get("dependencies") or []) if str(c).strip()]
            rep.cost_notes = str(data.get("cost_notes", "")).strip()
        except LLMError as e:
            rep.error = str(e)
            log.warning("agent %s synthesis failed: %s", self.brief.domain, e)

        # 3. Commit the domain position to the shared memory pool.
        await self.engine.remember(
            self.mind,
            f"[{self.brief.domain}] domain position by {self.brief.role}",
            rep.summary + "\nClaims: " + "; ".join(rep.claims) + "\nRisks: " + "; ".join(rep.risks),
            session_id=None, importance_floor=0.7, qwen=self.qwen,
        )
        await self._emit("agent_done", {"grounded": rep.grounded, "summary": rep.summary})
        return rep
