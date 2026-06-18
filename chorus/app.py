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
from engine.qwen import QwenClient
from engine.store import open_store
from engine.tenants import KiokuEngine, TenantRegistry

from chorus.orchestra import Orchestra
from chorus.synthesis import build_pdf_bytes

log = logging.getLogger("chorus.app")
logging.basicConfig(level=os.environ.get("CHORUS_LOG_LEVEL", "INFO"))


class StartRequest(BaseModel):
    question: str = Field(min_length=4, max_length=2000)


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
        app.state.orchestra = Orchestra(app.state.engine)
        yield
        eng = app.state.engine
        await eng.drain_background()
        store = getattr(eng, "_store", None)
        if store is not None:
            store.close()
        await eng.qwen.aclose()
        await eng.aclose_brains()

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
        run = request.app.state.orchestra.start(body.question, qwen=brain(request))
        return {"run_id": run.run_id, "question": run.question, "status": run.status}

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
