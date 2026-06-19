# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Guardianity — Chorus (Track 3)
"""Chorus's FastAPI app — multi-agent research orchestration.

Routes:
  GET  /api/health                     liveness + brain
  POST /api/chorus/start  {question}   begin an orchestration run (background)
  GET  /api/chorus/{id}                full run state
  GET  /api/chorus/{id}/stream         SSE: analyzer, agents, coordinator, report
  GET  /api/chorus/{id}/pdf            the final unified document as PDF

The Qwen key rides the per-request ``X-Qwen-Key`` header (per-tab, never
persisted); Qwen is the main brain. The memory substrate + researcher under
``engine/`` are copied from Kioku — this project is standalone.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from engine.config import REPO_ROOT, settings
from engine.qwen import LLMError, QwenClient
from engine.store import open_store
from engine.tenants import KiokuEngine, MindFull, TenantRegistry

from chorus.orchestra import Orchestra
from chorus.synthesis import build_pdf_bytes


# Talk-to-it: a research expert that owns the run's whole investigation as its
# knowledge — mirrors the Kioku Researcher's chat so the suite feels like one tool.
_CHAT_SYSTEM = """\
You are the lead researcher of a multi-domain orchestra. The investigation below
is YOUR work — the first-pass conclusion, every domain agent's findings, and the
final unified report. Answer the user from this knowledge, concretely and
honestly. When asked what you found or remember, draw from it; never say you lack
memory.
Recalled memory from earlier in this conversation (may be empty):
{pack}\
"""


def _build_run_context(run) -> str:
    """Inject the whole run — Stage 1 conclusion, domain reports, final document —
    into every chat turn, so the answer is grounded regardless of recall."""
    data = run.public()
    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"INVESTIGATION — question: {data.get('question','')}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    s1 = (data.get("stage1") or {}).get("report", "").strip()
    if s1:
        lines += ["\nSTAGE 1 — first-research conclusion:", s1]
    if data.get("hypothesis"):
        lines += ["\nHYPOTHESIS:", data["hypothesis"]]
    for r in data.get("reports") or []:
        if not (r.get("summary") or r.get("claims")):
            continue
        lines.append(f"\n[{r.get('domain')}] {r.get('role')}: {r.get('summary','')}")
        for c in (r.get("claims") or []):
            lines.append(f"  • {c}")
    rep = (data.get("report") or "").strip()
    if rep:
        lines += ["\nFINAL UNIFIED DOCUMENT:", rep]
    return "\n".join(lines)

log = logging.getLogger("chorus.app")
logging.basicConfig(level=os.environ.get("CHORUS_LOG_LEVEL", "INFO"))


class StartRequest(BaseModel):
    question: str = Field(min_length=4, max_length=2000)
    # Stage 1 count — how many sub-questions the first research expands to and you
    # watch answered one by one. Default 10 (good demo).
    questions: int = Field(default=10, ge=2, le=50)
    # Total question ceiling for the whole run (Stage 1 + agents + follow-ups).
    # Scale it up (100, 1000, 10000) for a deeper run.
    budget: int = Field(default=100, ge=2, le=10000)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None


def build_engine() -> KiokuEngine:
    cfg = settings()
    store = open_store(cfg.data_dir)
    qwen = QwenClient(cfg.llm)
    registry = TenantRegistry(store, qwen, cfg, message_cap=int(os.environ.get("CHORUS_MESSAGE_CAP", "1000000")))
    engine = KiokuEngine(registry)
    engine._store = store  # type: ignore[attr-defined]
    return engine


def create_app(engine: KiokuEngine | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.engine = engine or build_engine()
        # Chorus's OWN durable store (chats + engrams). Its own Neon DSN if set,
        # else a local SQLite file under chorus_data/. Never shared with Kioku/Aria.
        from engine.research.persistence import ResearchDB
        db_dsn = (
            os.environ.get("DATABASE_URL")
            or os.environ.get("CHORUS_DB")
            or str(REPO_ROOT / "chorus_data" / "chorus.db")
        )
        app.state.db = ResearchDB(db_dsn)
        # Durability: every committed engram is persisted to Chorus's own store.
        app.state.engine.persistor.append(
            lambda tenant, engram: app.state.db.save_engram(tenant, engram)
        )
        app.state.orchestra = Orchestra(app.state.engine)
        yield
        eng = app.state.engine
        await eng.drain_background()
        store = getattr(eng, "_store", None)
        if store is not None:
            store.close()
        await eng.qwen.aclose()
        await eng.aclose_brains()
        db = getattr(app.state, "db", None)
        if db is not None:
            db.close()

    app = FastAPI(title="Chorus", version="0.1.0", lifespan=lifespan)
    origins = os.environ.get("CHORUS_WEB_ORIGIN", "http://localhost:8002").split(",")
    app.add_middleware(
        CORSMiddleware, allow_origins=[o.strip() for o in origins],
        allow_methods=["GET", "POST"], allow_headers=["*"],
    )

    def brain(request: Request) -> QwenClient:
        return request.app.state.engine.qwen_for(request.headers.get("X-Qwen-Key"))

    def _require(request: Request, run_id: str):
        run = request.app.state.orchestra.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="no such run")
        return run

    @app.get("/api/health")
    async def health(request: Request) -> dict:
        eng = request.app.state.engine
        store = getattr(eng, "_store", None)
        return {"ok": True, "service": "chorus", "version": "0.1.0",
                "backend": store.stats().backend if store else "unknown",
                "brain": eng.qwen.config.provider}

    @app.post("/api/chorus/start")
    async def start(request: Request, body: StartRequest) -> dict:
        run = request.app.state.orchestra.start(
            body.question, qwen=brain(request),
            stage1_questions=body.questions, question_budget=body.budget,
        )
        return {"run_id": run.run_id, "question": run.question, "status": run.status,
                "stage1_questions": run.stage1_questions, "question_budget": run.question_budget}

    @app.get("/api/chorus/{run_id}")
    async def get_run(request: Request, run_id: str) -> dict:
        return _require(request, run_id).public()

    @app.get("/api/chorus/{run_id}/stream")
    async def stream(request: Request, run_id: str) -> StreamingResponse:
        run = _require(request, run_id)

        async def event_source():
            for event in run.recent_events:
                yield f"data: {json.dumps(event)}\n\n"
            if run.is_terminal:
                yield f"data: {json.dumps({'stage': run.status, 'terminal': True})}\n\n"
                return
            q = run.subscribe()
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.wait_for(q.get(), timeout=15.0)
                        yield f"data: {json.dumps(event)}\n\n"
                        if event.get("stage") in ("done", "error"):
                            break
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
            finally:
                run.unsubscribe(q)

        return StreamingResponse(
            event_source(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/chorus/{run_id}/ask")
    async def ask(request: Request, run_id: str, body: AskRequest) -> dict:
        run = _require(request, run_id)
        engine: KiokuEngine = request.app.state.engine
        mind = engine.registry.named_mind(run.tenant)  # the run's shared mind
        db = getattr(request.app.state, "db", None)

        history: list[dict] = []
        if db is not None and body.session_id:
            prior = db.load_chats(session_id=body.session_id, limit=20)
            history = [{"role": m["role"], "content": m["content"]} for m in prior[-16:]]

        try:
            result = await engine.turn(
                mind, body.question, session_id=body.session_id,
                send_to_both=False, qwen=brain(request),
                extra_context=_build_run_context(run),
                history=history, system_override=_CHAT_SYSTEM,
            )
        except MindFull as e:
            raise HTTPException(status_code=429, detail=str(e)) from e
        except LLMError as e:
            raise HTTPException(status_code=502, detail=f"LLM unavailable: {e}") from e

        if db is not None:
            db.save_chat(run.tenant, run_id, result.session_id, "user", body.question)
            db.save_chat(run.tenant, run_id, result.session_id, "assistant", result.kioku_reply)
        return {
            "answer": result.kioku_reply,
            "session_id": result.session_id,
            "recalled": result.pack.hit_list(),
            "run_status": run.status,
            "has_context": True,
            "history_turns": len(history) // 2,
        }

    @app.get("/api/chorus/{run_id}/memory")
    async def memory(request: Request, run_id: str) -> dict:
        """The run's living memory — every engram committed into its OWN shared
        mind (Stage-1 findings, each agent's position, the final document).
        Isolated per run; never shared with Kioku or Aria."""
        run = _require(request, run_id)
        mind = request.app.state.engine.registry.named_mind(run.tenant)
        engrams = mind.index.live_engrams()
        engrams.sort(key=lambda e: e.ts, reverse=True)
        return {
            "total": len(engrams), "tenant": run.tenant,
            "engrams": [
                {"engram_id": e.engram_id, "meaning": e.meaning, "message": e.message, "ts": e.ts}
                for e in engrams[:12]
            ],
        }

    @app.get("/api/chorus/{run_id}/pdf")
    async def pdf(request: Request, run_id: str) -> Response:
        run = _require(request, run_id)
        if run.status != "done":
            raise HTTPException(status_code=409, detail=f"run is '{run.status}', not finished")
        data = build_pdf_bytes(run.public())
        return Response(content=data, media_type="application/pdf",
                        headers={"Content-Disposition": 'attachment; filename="chorus-report.pdf"'})

    web_dir = REPO_ROOT / "web"
    if web_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="web")
    return app


def get_app() -> FastAPI:
    return create_app()
