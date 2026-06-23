# Chorus — Multi-Agent Research Orchestra

**Track 3 · Agent Society · Qwen Cloud Hackathon**

> Ask one hard question. Chorus assembles a domain-expert orchestra, has them
> research in parallel with shared memory, and synthesizes one build-grade document.

[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Track](https://img.shields.io/badge/Qwen%20Hackathon-Track%203%20%C2%B7%20Agent%20Society-amber)](https://deploy/alibaba/PROOF.md)

---

## What it does

You type one hard question — *"how can we cure blindness?"* or *"how do we build
an affordable home water purifier?"* — and press **Convene**.

**Stage 1 — Analyze.** A researcher reads the question, generates N sub-questions,
searches the web, and builds a first-pass hypothesis. Findings are committed to a
shared memory pool.

**Stage 2 — Orchestra.** An analyzer reads the Stage 1 output and casts a team of
domain-expert agents (neurology, optics, materials, regulatory, finance — always
at least one cost agent). Every agent is a specialist with a distinct research
focus and personality, powered by Qwen.

**Stage 3 — Research in parallel.** All agents run concurrently via
`asyncio.gather`. Each searches the web, reasons with Qwen, and writes their
*position* to the shared memory pool.

**Stage 4 — Coordinate.** The coordinator reads every agent's position, surfaces
agreements, conflicts, and gaps, and may dispatch follow-up sub-questions.

**Stage 5 — Synthesize.** The synthesizer merges all evidence into one structured
Markdown document — problem framing, domain findings, a cross-domain reconciliation,
a cost model, and next steps — downloadable as PDF.

**After.** You can keep talking to the orchestra via the left-panel chat. Every
answer is grounded in the orchestra's private memory from the run.

---

## Live demo

| | URL | Status |
|---|---|---|
| **Chorus** | https://chorus.guardianity.space | live |
| **Kioku** (sibling, Track 1) | https://kioku.guardianity.space | live |

Both run on **Alibaba Cloud ECS** `ap-southeast-1` (Singapore).
See [`deploy/alibaba/PROOF.md`](deploy/alibaba/PROOF.md) for the deployment proof.

---

## Architecture

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full diagram and
component walk-through.

```
User question
    │
    ▼
Stage 1 · Researcher  (DDG + Qwen → shared memory)
    │
    ▼
Analyzer  (Qwen JSON → N domain agents)
    │
    ├── Agent A · domain 1  (DDG + Qwen → memory)
    ├── Agent B · domain 2  (DDG + Qwen → memory)  ← asyncio.gather
    └── Agent N · finance   (DDG + Qwen → memory)
    │
    ▼
Coordinator  (Qwen → reconcile agreements/conflicts/gaps)
    │
    ▼
Synthesizer  (Qwen → Markdown + PDF)
    │
    ▼
Orchestra chat  (follow-up Q&A from shared memory)
```

---

## Qwen Cloud integration

Chorus uses **only Qwen / DashScope** — no other LLM provider.

- **Endpoint**: `https://dashscope-intl.aliyuncs.com/compatible-mode/v1`
- **Models**: `qwen-max` (all agent calls), `text-embedding-v3` (memory embeddings)
- **Key flow**: user enters key once in the UI → stored in `localStorage` → sent
  as `X-Qwen-Key` on every request → never stored server-side, never logged

See [`engine/qwen.py`](engine/qwen.py) for the client and
[`engine/config.py`](engine/config.py) for model/endpoint config.

---

## Run locally

```bash
git clone https://github.com/Umbra-shadow/Chorus.git
cd Chorus
cp .env.example .env
# edit .env — set QWEN_API_KEY (or leave blank and enter key in UI)

python3 -m venv .venv && source .venv/bin/activate
pip install -r engine/requirements.txt
make demo          # http://localhost:8002
```

---

## Layout

```
engine/         Qwen client, config, memory substrate, Stage 1 researcher
chorus/         App layer:
  analyzer.py     cast domain agents from question + Stage 1
  agent.py        one domain specialist (DDG + Qwen)
  coordinator.py  reconcile agent positions
  synthesis.py    final document + PDF
  orchestra.py    async orchestrator (SSE stream)
  app.py          FastAPI routes
web/            SPA — Stage 1 questions + agent cards + report + chat
docs/           Architecture diagram (Mermaid + PNG)
deploy/alibaba/ ECS proof + deploy notes
```

---

## License

Apache License 2.0 — see [LICENSE](LICENSE).

Built by **Guardianity** · [guardianity.space](https://guardianity.space)
