# Chorus — Architecture

![Chorus architecture diagram](architecture.png)

_Source: [`architecture.mmd`](architecture.mmd) (Mermaid)_

## Overview

Chorus is a **multi-agent research orchestra**. The user asks one hard question;
Chorus assembles a cast of domain-expert agents, has them research in parallel
with shared memory, then a coordinator reconciles their findings before
synthesizing a build-grade document.

---

## Layers

### 1 · Web Arena — `web/`

Dependency-free SPA served by FastAPI's `StaticFiles`. Two panels:

- **Left — Orchestra chat**: post-convene follow-up questions answered from the
  orchestra's private memory.
- **Right — Research panel**: submit a question, watch Stage 1 sub-questions light
  up, then agents activate in parallel.

Key is entered once and stored in `localStorage`; sent per-request as
`X-Qwen-Key` in the `Authorization` header — never persisted server-side.

### 2 · Engine — `engine/`

Shared substrate copied from the Kioku project (no runtime dependency):

- **`qwen.py`** — `QwenClient`: thin HTTPX wrapper against the Qwen Cloud
  OpenAI-compatible endpoint, with retry and `max_tokens ≤ 8192` enforcement.
- **`config.py`** — reads `QWEN_BASE_URL`, `QWEN_MODEL`, `QWEN_EMBED_MODEL` from
  `.env`; selects DashScope international endpoint by default.
- **`researcher.py`** — Stage 1 Researcher: DuckDuckGo search → Qwen summarise
  → engrams committed to memory; returns the researched sub-questions and a
  first-pass hypothesis.
- **`memory/`** — Cadran-substrate PyStore: sparse-file virtual hardware, shared
  across all agents in a run.

### 3 · Chorus App — `chorus/`

| Module | Role |
|---|---|
| `analyzer.py` | Reads question + Stage 1 findings → single Qwen JSON call → casts `N` domain agents (name, focus, personality) |
| `agent.py` | One domain specialist: DuckDuckGo search + Qwen reasoning → writes `position` to shared memory |
| `coordinator.py` | Reads all agents' positions → Qwen call → reconciles agreements, conflicts, gaps; may emit follow-up questions |
| `synthesis.py` | Takes coordinator review + all evidence → Qwen call → Markdown + cost-model document; optionally exports PDF via fpdf2 |
| `orchestra.py` | Async orchestrator: runs Stage 1 → analyzer → agents (parallel `asyncio.gather`) → coordinator → synthesis; streams SSE events to UI |
| `app.py` | FastAPI: `/api/convene` (SSE stream), `/api/chat` (post-convene Q&A), `/api/health`, static file serving |

---

## Data flow

```
User question
    │
    ▼
┌──────────────────────────────────────────────────────────────┐
│  Stage 1 · Researcher (engine/researcher.py)                  │
│  DuckDuckGo ──► Qwen summarise ──► engrams ──► memory pool   │
│  Output: sub-questions answered, first-pass hypothesis        │
└──────────────────┬───────────────────────────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────────────────────────┐
│  Analyzer (chorus/analyzer.py)                                │
│  Qwen call → JSON → N domain agents                           │
│  e.g. neurology · optics · materials · regulatory · finance   │
└──────────────────┬───────────────────────────────────────────┘
                   │ asyncio.gather
       ┌───────────┼───────────┐
       ▼           ▼           ▼
   Agent A     Agent B     Agent C …   (chorus/agent.py)
   DDG+Qwen   DDG+Qwen   DDG+Qwen
       │           │           │
       └───────────┴───────────┘
                   │ writes positions to shared memory pool
                   ▼
┌──────────────────────────────────────────────────────────────┐
│  Coordinator (chorus/coordinator.py)                          │
│  Qwen call → agreements · conflicts · gaps · follow-ups       │
└──────────────────┬───────────────────────────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────────────────────────┐
│  Synthesis (chorus/synthesis.py)                              │
│  Qwen call → Markdown report + cost model → PDF download      │
└──────────────────────────────────────────────────────────────┘
                   │
                   ▼
         Orchestra chat ← user follow-ups answered from memory
```

---

## External services

| Service | Purpose | How |
|---|---|---|
| **Qwen Cloud** (DashScope) | All LLM calls (analyzer, agents, coordinator, synthesizer, researcher, chat) | `POST /compatible-mode/v1/chat/completions` with `X-Qwen-Key` per request |
| **Qwen text-embedding-v3** | Stage 1 memory embedding + semantic recall for chat | `POST /compatible-mode/v1/embeddings` |
| **DuckDuckGo** (`ddgs`) | Web search for Stage 1 Researcher and each domain agent | Python `ddgs` library; no API key |
| **Alibaba Cloud ECS** | Hosts the FastAPI backend + static SPA | `ap-southeast-1` (Singapore), Ubuntu 22.04, systemd + nginx |

---

## Deployment

```
Alibaba Cloud ECS (ap-southeast-1 · 43.106.15.59)
├── nginx (port 8080 external → 8002 internal)
├── systemd chorus.service
│     └── uvicorn chorus.app:get_app --factory --host 0.0.0.0 --port 8002
└── /opt/chorus/
      ├── chorus/        app layer
      ├── engine/        substrate + researcher
      ├── web/           SPA
      └── .env           QWEN_* config (gitignored)
```

Public endpoint: `http://43.106.15.59:8080` (Nginx) or `https://chorus.guardianity.space`
