# Chorus — Alibaba Cloud + Qwen Cloud proof

This file points to the exact code that calls **Qwen Cloud** (Alibaba Cloud Model
Studio / DashScope) and explains the **Alibaba Cloud ECS** deployment for the
hackathon submission.

## 1. Qwen Cloud is the only brain — exact call sites

Every LLM call in Chorus goes through one client against the DashScope
OpenAI-compatible endpoint. No Gemini, no OpenAI, no other provider.

| What | File · symbol | Qwen API surface |
|---|---|---|
| HTTP client (base URL, retries, key) | [`engine/qwen.py`](../../engine/qwen.py) · `QwenClient` | `POST /chat/completions`, `POST /embeddings` |
| Endpoint + model config | [`engine/config.py`](../../engine/config.py) · `_llm_from_env` | `QWEN_BASE_URL`, `QWEN_MODEL`, `QWEN_EMBED_MODEL` |
| Stage 1 sub-question research | [`engine/research/researcher.py`](../../engine/research/researcher.py) · `Researcher.run` → `chat` | chat |
| Domain agent casting (JSON mode) | [`chorus/analyzer.py`](../../chorus/analyzer.py) · `Analyzer.cast` → `chat_json` | chat |
| Domain specialist research | [`chorus/agent.py`](../../chorus/agent.py) · `Agent.research` → `chat` | chat |
| Coordinator reconciliation | [`chorus/coordinator.py`](../../chorus/coordinator.py) · `Coordinator.review` → `chat` | chat |
| Final synthesis document | [`chorus/synthesis.py`](../../chorus/synthesis.py) · `Synthesizer.run` → `chat` | chat |
| Stage 1 memory embeddings | [`engine/research/researcher.py`](../../engine/research/researcher.py) → `embed` | embeddings |
| Post-convene Q&A recall | [`chorus/app.py`](../../chorus/app.py) · `/api/chat` → `chat` | chat |

Default endpoint (international):
`https://dashscope-intl.aliyuncs.com/compatible-mode/v1`

The key is never stored server-side — it is sent per-request from the browser as
`X-Qwen-Key` / `Authorization: Bearer <key>` and is never logged or committed.
See `QwenClient.__init__` in `engine/qwen.py`.

## 2. Alibaba Cloud ECS — live deployment

Instance: **`i-t4nifs3uru6sgp7fbhl9`** (`guardianity-demo`)
Region: **`ap-southeast-1`** (Singapore)
Public IP: **`43.106.15.59`**
Instance type: `ecs.e-c1m2.large` — 2 vCPU · 4 GiB

### Health probe

```bash
curl http://43.106.15.59:8080/api/health
# → {"ok":true,"service":"chorus","version":"0.1.0","backend":"pystore","brain":"qwen"}
```

### systemd service

`/etc/systemd/system/chorus.service` on the ECS instance:

```
[Service]
WorkingDirectory=/opt/chorus
Environment=PYTHONPATH=/opt/chorus
ExecStart=/opt/chorus/.venv/bin/uvicorn chorus.app:get_app \
          --factory --host 0.0.0.0 --port 8002
EnvironmentFile=/opt/chorus/.env
```

Nginx reverse-proxies `:8080` (public) → `:8002` (uvicorn).

### Provisioning — aliyun CLI calls used

```bash
# Instance was created with:
aliyun ecs CreateInstance --RegionId ap-southeast-1 \
  --InstanceName guardianity-demo \
  --InstanceType ecs.e-c1m2.large \
  --ImageId ubuntu_22_04_x64_20G_alibase_20240926.vhd \
  --InternetMaxBandwidthOut 10 \
  --InternetChargeType PayByTraffic

# Security group rule for Chorus port:
aliyun ecs AuthorizeSecurityGroup \
  --RegionId ap-southeast-1 \
  --SecurityGroupId sg-t4nicfn340fmdbjo2rp1 \
  --IpProtocol tcp --PortRange 8080/8080 \
  --SourceCidrIp 0.0.0.0/0 \
  --Description "Chorus Track3 AgentSociety"
```

## 3. What to record (proof video / screenshots)

1. **ECS console** — show the running `guardianity-demo` instance in
   `ap-southeast-1`, its public IP `43.106.15.59`.
2. **Terminal** — run the health probe above; response must show `"brain":"qwen"`.
3. **Open the app** at `http://43.106.15.59:8080` (or `https://chorus.guardianity.space`):
   enter a Qwen key, type a hard question, press **Convene** — watch agents
   light up in parallel.
4. **Model Studio usage dashboard** — optional, shows the DashScope API traffic
   tying back to Qwen Cloud.
5. **Orchestra chat** — after convening, ask a follow-up question to show the
   orchestra memory working.
