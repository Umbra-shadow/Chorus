# Chorus

**A multi-agent research orchestra.** — Qwen hackathon, Track 3.

Ask one hard question ("how can we cure blindness?"). Chorus doesn't answer with
one voice — it **casts a team**. An analyzer reads the question, forms a
hypothesis, and decides which expert domains it needs (neurology, optics,
materials, software, regulatory… and **finance is always present** for cost).
Each domain becomes an agent. The agents **research in parallel**, sharing one
memory pool, then a **coordinator** reconciles their findings — surfacing
agreements, conflicts, and gaps, and sending follow-ups — before synthesizing one
**build-grade document** (with a cost model) you can download as PDF.

Same model, many agents, real cross-domain tension and resolution.

## Run

```bash
python3 -m venv .venv && .venv/bin/pip install -r engine/requirements.txt
make demo            # http://localhost:8002
```

Qwen is the main brain (`qwen-max` for the coordinator + specialists). Leave
`QWEN_API_KEY` blank in `.env` and enter your key in the web UI (sent per-request
as `X-Qwen-Key`, lives only in that tab, never persisted). Because users bring
their own key, Chorus fans out many parallel agent calls per query freely.

## Standalone

Chorus is self-contained. The memory substrate **and** the researcher under
`engine/` are **copied** from the Kioku project, not imported — Chorus has no
dependency on its sibling projects.

## Layout

```
engine/    copied Kioku memory substrate + researcher (no app code)
chorus/    app layer — analyzer (cast), agent (domain specialist), coordinator
           (reconcile), synthesis (final doc), orchestra (run it all), app
web/       orchestration UI — agents lighting up in parallel
```
