# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Guardianity — Chorus (Track 3)
"""The orchestra — analyze → cast → parallel research → coordinate → synthesize.

Owns a run and its event bus (SSE). The domain agents fan out with
``asyncio.gather`` against one shared memory pool; the coordinator reconciles them
over up to ``CHORUS_MAX_ROUNDS`` rounds; the lead synthesizes one build-grade
document. Everything is announced live so the UI can light up agents in parallel.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

from engine.engram import new_ulid
from engine.qwen import LLMError, QwenClient
from engine.research.researcher import Finding
from engine.research.websearch import WebSearch
from engine.tenants import KiokuEngine

from chorus.agent import DomainAgent, DomainReport
from chorus.analyzer import cast_agents
from chorus.coordinator import review as coordinator_review
from chorus.synthesis import synthesize

log = logging.getLogger("chorus.orchestra")


def _max_rounds() -> int:
    try:
        return max(0, min(4, int(os.environ.get("CHORUS_MAX_ROUNDS", "2"))))
    except ValueError:
        return 2


@dataclass
class Run:
    run_id: str
    question: str
    status: str = "idle"     # idle|analyzing|researching|coordinating|synthesizing|done|error
    hypothesis: str = ""
    reports: list[DomainReport] = field(default_factory=list)
    reviews: list[dict] = field(default_factory=list)
    report: str = ""
    grounded_total: int = 0
    error: str = ""
    created_ts: float = field(default_factory=time.time)
    tenant: str = ""
    _subs: set[asyncio.Queue] = field(default_factory=set)
    _recent: list[dict] = field(default_factory=list)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=256); self._subs.add(q); return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    async def emit(self, event: dict) -> None:
        self._recent.append(event); del self._recent[:-128]
        for q in list(self._subs):
            try: q.put_nowait(event)
            except asyncio.QueueFull: pass

    @property
    def recent_events(self) -> list[dict]: return list(self._recent)
    @property
    def is_terminal(self) -> bool: return self.status in ("done", "error")

    def public(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id, "question": self.question, "status": self.status,
            "hypothesis": self.hypothesis, "reports": [r.public() for r in self.reports],
            "reviews": self.reviews, "report": self.report,
            "grounded_total": self.grounded_total, "error": self.error,
            "created_ts": self.created_ts,
        }


class Orchestra:
    """Owns runs and drives the orchestration over the copied Kioku engine."""

    def __init__(self, engine: KiokuEngine) -> None:
        self.engine = engine
        self._runs: dict[str, Run] = {}
        self._tasks: set[asyncio.Task] = set()

    def get(self, run_id: str) -> Run | None:
        return self._runs.get(run_id)

    def start(self, question: str, qwen: QwenClient | None) -> Run:
        run = Run(run_id="r_" + new_ulid()[:12], question=question.strip(),
                  tenant="chorus:" + new_ulid()[:10])
        self._runs[run.run_id] = run
        task = asyncio.create_task(self._run(run, qwen))
        self._tasks.add(task); task.add_done_callback(self._tasks.discard)
        return run

    async def _run(self, run: Run, qwen: QwenClient | None) -> None:
        qwen = qwen or self.engine.qwen
        web = WebSearch()
        # One shared mind for the whole orchestra — every agent reads/writes it.
        mind = self.engine.registry.named_mind(run.tenant)
        try:
            # 1. Analyze + cast.
            run.status = "analyzing"
            await run.emit({"stage": "analyzing", "question": run.question})
            cast = await cast_agents(qwen, run.question)
            run.hypothesis = cast.hypothesis
            await run.emit({"stage": "cast", "hypothesis": cast.hypothesis,
                            "agents": [a.public() for a in cast.agents]})

            # 2. Domain agents research in parallel against the shared memory.
            run.status = "researching"
            await run.emit({"stage": "researching", "count": len(cast.agents)})
            agents = [DomainAgent(self.engine, mind, qwen, web, cast.hypothesis, b,
                                  progress=run.emit) for b in cast.agents]
            run.reports = list(await asyncio.gather(*(a.work() for a in agents)))
            run.grounded_total = sum(r.grounded for r in run.reports)

            # 3. Coordinator reconciles over up to MAX_ROUNDS.
            for rnd in range(_max_rounds()):
                run.status = "coordinating"
                await run.emit({"stage": "coordinating", "round": rnd + 1})
                rv = await coordinator_review(qwen, run.hypothesis, run.reports)
                run.reviews.append(rv.public())
                await run.emit({"stage": "review", "round": rnd + 1, **rv.public()})
                if rv.ready or not rv.followups:
                    break
                await self._run_followups(run, mind, qwen, web, rv.followups)

            # 4. Synthesize the unified document.
            run.status = "synthesizing"
            await run.emit({"stage": "synthesizing"})
            last_review = run.reviews[-1] if run.reviews else {}
            from chorus.coordinator import Review
            rv_obj = Review(**{k: last_review.get(k, []) if k != "ready" else last_review.get("ready", True)
                               for k in ("agreements", "conflicts", "gaps", "followups", "ready")}) \
                if last_review else Review()
            run.report = await synthesize(qwen, run.question, run.hypothesis, run.reports, rv_obj)
            await self.engine.remember(
                mind, f"Final Chorus report on '{run.question}'", run.report,
                session_id=None, importance_floor=0.95, qwen=qwen,
            )
            run.status = "done"
            await run.emit({"stage": "done", "grounded_total": run.grounded_total})
        except LLMError as e:
            run.status = "error"; run.error = str(e)
            await run.emit({"stage": "error", "error": str(e)})
        except Exception as e:  # noqa: BLE001
            run.status = "error"; run.error = str(e)
            log.exception("chorus run %s failed", run.run_id)
            await run.emit({"stage": "error", "error": str(e)})
        finally:
            await web.aclose()

    async def _run_followups(self, run: Run, mind, qwen, web, followups: list[dict]) -> None:
        """Each follow-up is researched (grounded, into shared memory) and folded
        back into the matching domain report's claims."""
        from engine.research.researcher import Researcher
        researcher = Researcher(self.engine, mind, web, qwen=qwen)
        for fu in followups:
            domain = str(fu.get("domain", "")).strip().lower()
            question = str(fu.get("question", "")).strip()
            if not question:
                continue
            await run.emit({"stage": "followup", "domain": domain, "question": question})
            f = Finding(id=0, question=f"[{domain} follow-up] {question}")
            try:
                f = await researcher.study(f)
            except Exception as e:  # noqa: BLE001
                f.answer = f"[error] {e}"
            for r in run.reports:
                if r.domain == domain:
                    r.claims.append(f"(follow-up) {f.answer}")
                    break
