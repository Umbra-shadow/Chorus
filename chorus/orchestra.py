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

from chorus.agent import Budget, DomainAgent, DomainReport
from chorus.analyzer import cast_agents
from chorus.coordinator import review as coordinator_review
from chorus.synthesis import synthesize

log = logging.getLogger("chorus.orchestra")


def _max_rounds() -> int:
    try:
        return max(0, min(4, int(os.environ.get("CHORUS_MAX_ROUNDS", "2"))))
    except ValueError:
        return 2


def _question_budget() -> int:
    """Iteration limit: the most research questions the whole orchestra may study
    in one run — the ceiling that keeps a run from quietly burning the API key.
    Default 100, override with CHORUS_QUESTION_BUDGET."""
    try:
        return max(1, min(1000, int(os.environ.get("CHORUS_QUESTION_BUDGET", "100"))))
    except ValueError:
        return 100


def _stage1_questions() -> int:
    """How many sub-questions the Stage-1 (Kioku-style) first research expands to
    before its conclusion is handed to the agent orchestra. Default 8."""
    try:
        return max(2, min(40, int(os.environ.get("CHORUS_STAGE1_QUESTIONS", "8"))))
    except ValueError:
        return 8


@dataclass
class Run:
    run_id: str
    question: str
    status: str = "idle"     # idle|stage1|analyzing|researching|coordinating|synthesizing|done|error
    stage1_questions: int = 8
    stage1: dict = field(default_factory=dict)   # the Kioku-style first research
    hypothesis: str = ""
    reports: list[DomainReport] = field(default_factory=list)
    reviews: list[dict] = field(default_factory=list)
    report: str = ""
    grounded_total: int = 0
    questions_used: int = 0
    question_budget: int = 0
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
            "stage1_questions": self.stage1_questions, "stage1": self.stage1,
            "hypothesis": self.hypothesis, "reports": [r.public() for r in self.reports],
            "reviews": self.reviews, "report": self.report,
            "grounded_total": self.grounded_total, "error": self.error,
            "questions_used": self.questions_used, "question_budget": self.question_budget,
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

    def start(self, question: str, qwen: QwenClient | None, *,
              stage1_questions: int | None = None, question_budget: int | None = None) -> Run:
        run = Run(run_id="r_" + new_ulid()[:12], question=question.strip(),
                  tenant="chorus:" + new_ulid()[:10],
                  stage1_questions=stage1_questions or _stage1_questions(),
                  question_budget=question_budget or _question_budget())
        self._runs[run.run_id] = run
        task = asyncio.create_task(self._run(run, qwen))
        self._tasks.add(task); task.add_done_callback(self._tasks.discard)
        return run

    async def _run(self, run: Run, qwen: QwenClient | None) -> None:
        qwen = qwen or self.engine.qwen
        web = WebSearch()
        # The iteration limit shared across the whole orchestra (from the UI field).
        budget = Budget(run.question_budget or _question_budget())
        run.question_budget = budget.total
        # One shared mind for the whole orchestra — every agent reads/writes it.
        mind = self.engine.registry.named_mind(run.tenant)
        try:
            # STAGE 1 — the first research (Kioku-style): expand the question into
            # sub-questions, study them on the web, synthesize a conclusion. In
            # Chorus this conclusion is NOT the final answer — it seeds the team.
            run.status = "stage1"
            await run.emit({"stage": "stage1_begin", "question": run.question})
            run.stage1 = await self._stage1(run, mind, qwen, web, budget)
            run.questions_used = budget.used
            conclusion = run.stage1.get("report", "")

            # STAGE 2 — analyze the conclusion + cast the domain team (deeper).
            run.status = "analyzing"
            await run.emit({"stage": "analyzing", "question": run.question})
            cast = await cast_agents(qwen, run.question, context=conclusion)
            run.hypothesis = cast.hypothesis
            await run.emit({"stage": "cast", "hypothesis": cast.hypothesis,
                            "agents": [a.public() for a in cast.agents],
                            "question_budget": budget.total})

            # Domain agents research in parallel against the shared memory,
            # all drawing from one shared question budget.
            run.status = "researching"
            await run.emit({"stage": "researching", "count": len(cast.agents)})
            agents = [DomainAgent(self.engine, mind, qwen, web, cast.hypothesis, b,
                                  budget, progress=run.emit) for b in cast.agents]
            run.reports = list(await asyncio.gather(*(a.work() for a in agents)))
            run.grounded_total = sum(r.grounded for r in run.reports)
            run.questions_used = budget.used

            # 3. Coordinator reconciles over up to MAX_ROUNDS.
            for rnd in range(_max_rounds()):
                run.status = "coordinating"
                await run.emit({"stage": "coordinating", "round": rnd + 1})
                rv = await coordinator_review(qwen, run.hypothesis, run.reports)
                run.reviews.append(rv.public())
                await run.emit({"stage": "review", "round": rnd + 1, **rv.public()})
                if rv.ready or not rv.followups or budget.exhausted:
                    break
                await self._run_followups(run, mind, qwen, web, rv.followups, budget)
                run.questions_used = budget.used

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
            await run.emit({"stage": "done", "grounded_total": run.grounded_total,
                            "questions_used": budget.used, "question_budget": budget.total})
        except LLMError as e:
            run.status = "error"; run.error = str(e)
            await run.emit({"stage": "error", "error": str(e)})
        except Exception as e:  # noqa: BLE001
            run.status = "error"; run.error = str(e)
            log.exception("chorus run %s failed", run.run_id)
            await run.emit({"stage": "error", "error": str(e)})
        finally:
            await web.aclose()

    async def _stage1(self, run: Run, mind, qwen, web, budget: Budget) -> dict:
        """The Kioku-style first research, re-emitted as stage1_* events and
        drawing its sub-question studies from the shared budget."""
        from engine.research.researcher import Researcher

        n = min(run.stage1_questions or _stage1_questions(), max(1, budget.remaining))

        async def sink(stage: str, detail: dict) -> None:
            await run.emit({"stage": "stage1", "sub": stage, **detail})

        researcher = Researcher(self.engine, mind, web, progress=sink, qwen=qwen)
        try:
            result = await researcher.run(run.question, n=n)
        except Exception as e:  # noqa: BLE001 — Stage 1 must not sink the whole run
            log.warning("stage 1 failed: %s", e)
            result = {"topic": run.question, "report": "", "questions": [],
                      "findings": [], "grounded_count": 0}
        # Account the sub-questions studied against the shared budget.
        studied = len(result.get("findings", []))
        budget.used = min(budget.total, budget.used + studied)
        await run.emit({"stage": "stage1_done", "questions": len(result.get("questions", [])),
                        "grounded": result.get("grounded_count", 0)})
        return result

    async def _run_followups(self, run: Run, mind, qwen, web, followups: list[dict],
                             budget: Budget) -> None:
        """Each follow-up is researched (grounded, into shared memory) and folded
        back into the matching domain report's claims — drawing from the shared
        question budget so follow-ups can't run away with the API key."""
        from engine.research.researcher import Researcher
        researcher = Researcher(self.engine, mind, web, qwen=qwen)
        for fu in followups:
            domain = str(fu.get("domain", "")).strip().lower()
            question = str(fu.get("question", "")).strip()
            if not question:
                continue
            if not budget.take():
                await run.emit({"stage": "budget_exhausted", "used": budget.used, "total": budget.total})
                break
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
