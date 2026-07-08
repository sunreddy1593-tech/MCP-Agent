# Deployment Plan: Weekly Review Pulse on Railway

How to deploy the **Weekly Mobile-Store Review Pulse** pipeline on
[Railway](https://railway.app). Grounded in the actual project (`review_pulse/`,
`pyproject.toml`, `config/run_config.example.yaml`) and the phases in
`implementation-plan.md`.

---

## 1. What we are actually deploying

This project is **not a long-running web/HTTP server**. It is a **scheduled batch
pipeline** — a CLI that runs once per week to: download reviews → normalize →
theme → summarize (Groq) → render → deliver (Google Docs + Gmail **via MCP**).

Two implications for Railway:

- **Runtime model = Cron Job**, not a "web service". There is no port to bind, no
  healthcheck endpoint. The container starts on a schedule, runs to completion,
  and exits.
- **The app is an MCP _client/consumer_**, not an MCP _server_. It *calls* the
  Google Docs + Gmail MCP tools. Those MCP servers must be reachable from the
  Railway runtime for Phase 6 delivery (see §10). Until they are provisioned,
  the pipeline runs stages 1–5 and persists the rendered pulse locally
  (`note.md` / `email.txt`) — nothing upstream is lost.

```
        Railway Cron Service (this repo)
        ┌───────────────────────────────────────────┐
        │  python -m review_pulse.orchestrator ...   │
        │  download → run (Phases 1–6)               │
        └───────┬───────────────┬───────────────┬────┘
   outbound ▶   │               │               │
                ▼               ▼               ▼
        iTunes RSS +      Groq API        Docs + Gmail
        Google Play       (LLM)           MCP servers
        (public reviews)                  (delivery, §10)
                │
                ▼
        Railway Volume  (/data)  ← persistent run artifacts + manifests
```

---

## 2. Prerequisites

- A **Railway account** and a project (Hobby plan is sufficient; cron jobs run on
  usage-based billing).
- The **Railway CLI** (optional but recommended): `npm i -g @railway/cli`.
- A **Groq API key** (`GROQ_API_KEY`) — https://console.groq.com/keys.
- This repo pushed to **GitHub** (Railway deploys from a connected repo, or via
  `railway up` from the CLI).
- (For real delivery) Reachable **Google Docs + Gmail MCP servers** and an MCP
  client wired into `delivery/` — see §10. Not required for an artifact-only
  first deploy.

---

## 3. Repo changes required before deploying

Add the following files to the repo. They are deployment glue only — no changes
to pipeline logic.

### 3.1 `railway.toml` — build + schedule (config as code)

```toml
[build]
builder = "NIXPACKS"

[deploy]
# Download fresh reviews, then run the full weekly pipeline.
# RUN_CONFIG is injected as a Railway variable (see §4).
startCommand = "python -m review_pulse.orchestrator download --config $RUN_CONFIG && python -m review_pulse.orchestrator run --config $RUN_CONFIG"
# Weekly cadence. Railway cron is evaluated in UTC.
# 03:30 UTC Monday == 09:00 IST Monday.
cronSchedule = "30 3 * * 1"
# Cron jobs must exit; do not auto-restart on success.
restartPolicyType = "NEVER"
```

> If `download` finds no reviews it exits non-zero and `&&` stops the run — that
> is intentional (don't publish an empty pulse). Check the logs and config ids.

### 3.2 `.python-version` — pin the interpreter

`pyproject.toml` requires `>=3.10`. Pin a concrete version so builds are
reproducible:

```
3.11
```

### 3.3 `config/run_config.prod.yaml` — production run config

Copy `config/run_config.example.yaml` and point the artifact directories at the
mounted **Volume** (§5) so runs persist across restarts/deploys. Everything here
is non-secret (the Groq key stays in the environment).

```yaml
product_id: "com.nextbillion.groww"
product_name: "Groww"

window:
  weeks: 12

themes:
  labels:
    - charges_fees
    - trading_products
    - app_ux_updates
    - customer_support
    - withdrawals_payments

sources:
  app_store:
    app_id: null
    country: "in"
  play_store:
    app_id: "com.nextbillion.groww"
    lang: "en"
    country: "in"
  max_reviews: 20000

outputs:
  doc_title: "Groww — Weekly Review Pulse"
  doc_id: null            # set after the first Doc exists → idempotent updates
  email_to: "you@example.com"

groq:
  model: "llama-3.3-70b-versatile"
  temperature: 0.2

# Persisted on the Railway Volume mounted at /data (see §5).
exports_dir: "/data/exports"
store_dir: "/data/store/runs"
```

### 3.4 `.dockerignore` (only if you choose the Dockerfile path in §7)

```
.venv
store
data
__pycache__
*.pyc
.pytest_cache
.env
```

> **Never commit `.env`.** Confirm it is git-ignored; secrets go in Railway
> variables (§4).

---

## 4. Environment variables & secrets

Set these on the Railway **service** (Variables tab, or `railway variables set`):

| Variable | Required | Value / notes |
| --- | --- | --- |
| `GROQ_API_KEY` | Yes | Your Groq key. Consumed by `config.load_config` via `python-dotenv`/env. |
| `GROQ_MODEL` | No | Override the model (default `llama-3.3-70b-versatile`). |
| `RUN_CONFIG` | Yes | `config/run_config.prod.yaml` — used by `startCommand`. |
| `TZ` | No | Informational only; Railway cron always fires in **UTC**. |
| `PYTHONUNBUFFERED` | Recommended | `1` — flush logs immediately so Railway shows progress live. |

Delivery/MCP-related variables (endpoints, tokens) are added in §10 once the MCP
client is wired.

---

## 5. Persistent storage (Railway Volume)

The container filesystem is **ephemeral** — artifacts written to it vanish on the
next deploy/restart. The pipeline writes exports, `normalized.json`,
`themes.json`, `note.json`, `note.md`, `email.txt`, and `manifest.json` to the
run store, and Phase 6 idempotency + the artifact-only fallback depend on them
surviving between runs.

1. In the Railway service → **Volumes** → **New Volume**.
2. Mount path: **`/data`**.
3. Ensure `config/run_config.prod.yaml` points `exports_dir` and `store_dir`
   under `/data` (§3.3).

> The classification cache and re-run idempotency (`doc_id`, `draft_id` in the
> `RunManifest`) only pay off if the volume persists — otherwise every run is a
> cold start.

---

## 6. Deploy — step by step

### Option A: Dashboard (GitHub)

1. Push the repo (including the §3 files) to GitHub.
2. Railway → **New Project** → **Deploy from GitHub repo** → select this repo.
3. Railway detects Python (Nixpacks) from `pyproject.toml` / `requirements.txt`.
4. Add the **Volume** (§5) and **Variables** (§4).
5. Confirm **Settings → Deploy → Cron Schedule** matches `railway.toml`
   (`30 3 * * 1`) and **Restart Policy = Never**.
6. Trigger a manual deploy to validate the build, then let the schedule drive it.

### Option B: Railway CLI

```bash
railway login
railway init                       # or: railway link  (existing project)
railway variables set GROQ_API_KEY=... RUN_CONFIG=config/run_config.prod.yaml PYTHONUNBUFFERED=1
railway volume add --mount-path /data
railway up                         # build + deploy from the local repo
```

---

## 7. Build strategy (Nixpacks vs Dockerfile)

**Primary: Nixpacks (zero-config).** Railway auto-installs from
`requirements.txt` / `pyproject.toml`. The only OS need is outbound HTTPS
(iTunes RSS, Google Play, Groq) — no system packages required.

**Alternative: Dockerfile** (use if you want full control / faster cold builds).
Set `builder = "DOCKERFILE"` in `railway.toml` and add:

```dockerfile
FROM python:3.11-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Cron start command comes from railway.toml → deploy.startCommand.
CMD ["python", "-m", "review_pulse.orchestrator", "run", "--config", "config/run_config.prod.yaml"]
```

---

## 8. Scheduling & cadence

- Cron expression lives in `railway.toml` (`cronSchedule`) or the dashboard.
- **All Railway cron schedules run in UTC.** Convert your target local time:
  e.g. weekly Monday 09:00 IST → `30 3 * * 1`.
- Railway will **not start a new run while the previous one is still active**, so
  overlapping weekly runs can't stack up.
- A typical run is minutes (download + Groq classification is the long pole; the
  Phase 3 batched-Groq pass can take several minutes under the rate limits).

---

## 9. First-run validation checklist

- [ ] Build succeeds; logs show the loaded (redacted) run config.
- [ ] `download` reports non-zero review counts for the configured store(s).
- [ ] `themes.json` shows ≤5 themes with a deterministic top‑3.
- [ ] `note.json` is ≤250 words, exactly 3 themes/quotes/actions, quotes verbatim.
- [ ] `note.md` / `email.txt` exist on the volume and pass the render PII scan.
- [ ] `manifest.json` written with counts (and `doc_id`/`draft_id` once §10 is live).

---

## 10. MCP delivery: the critical caveat

Phase 6 (`delivery/docs_client.py`, `delivery/gmail_client.py`) is still a
**stub**, and the Cursor `CallMcpTool` mechanism used during development is an
IDE-agent harness — it does **not** exist in a headless Railway container. So a
plain deploy today runs stages **1–5** and lands in the **artifact-only
fallback**: the rendered pulse is persisted to `/data`, delivery status
`pending`. That is a valid, safe first deployment.

To enable real delivery from Railway, you need **all three**:

1. **Reachable Docs + Gmail MCP servers.** Either run them as *separate Railway
   services* (private-networked) or use hosted MCP endpoints. (Today only
   `user-alphavantage` is configured — neither Docs nor Gmail.)
2. **An MCP client in `delivery/`** — e.g. the Python `mcp` SDK connecting over
   stdio/HTTP — replacing the stubbed `publish` / `create_draft`. This reads each
   tool's schema, creates/updates the Doc, and creates a Gmail **draft** (never
   sends), returning `{doc_id, doc_url}` / `{draft_id}`.
3. **MCP auth/config as Railway variables** (server URLs, OAuth tokens the MCP
   servers require). The app itself holds no Google credentials — auth is the MCP
   server's responsibility.

**Recommended rollout:**
- **Phase A (now):** deploy artifact-only; verify stages 1–5 on schedule.
- **Phase B (after MCP is provisioned):** implement the MCP client, add its
  variables, re-deploy; set `outputs.doc_id` after the first Doc so weekly runs
  update the same document instead of creating duplicates.

---

## 11. Observability, cost & security

**Observability**
- Railway **Deploy Logs** stream stdout/stderr per run (set `PYTHONUNBUFFERED=1`).
- Each run's `manifest.json` on the volume is the audit record (window, counts,
  Doc/draft ids, timing) — inspect via the volume or a follow-up read.

**Cost / scaling**
- Cron runs only a few minutes/week, so compute cost is minimal; the volume is
  the main standing cost (small — JSON/text artifacts).
- No horizontal scaling needed — it is a single sequential batch job.

**Security**
- Secrets only in Railway variables; `.env` never committed.
- Privacy is enforced in-pipeline: PII stripped at ingestion and re-scanned at
  render (delivery is blocked on any leak). No reviewer identity fields are
  persisted.
- Gmail output is a **draft only** — a human sends it; nothing is auto-sent.

---

## 12. Rollback & troubleshooting

**Rollback:** Railway keeps prior deploys — use **Deployments → Redeploy** on the
last good build. Config/cron changes are just a new deploy; the volume data is
untouched.

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Build fails on deps | Interpreter mismatch | Confirm `.python-version` = 3.11; deps resolve on 3.11. |
| Run aborts after `download` | No reviews found (non-zero exit) | Check `sources.*.app_id` / country in prod config; view logs. |
| Artifacts disappear between runs | No/incorrect volume | Mount volume at `/data`; ensure config dirs point under `/data`. |
| Groq errors / rate limits | Missing key or TPD exhausted | Set `GROQ_API_KEY`; Phase 3 fallback covers limits, but verify budget. |
| Job seems to "restart forever" | Restart policy | Set `restartPolicyType = "NEVER"` for the cron service. |
| No Doc / draft created | MCP not wired (expected today) | Artifact-only fallback is normal; complete §10 for real delivery. |

---

## 13. Out of scope / open items

- Implementing the MCP client + provisioning Docs/Gmail MCP servers (§10).
- Alerting on failed runs (could add a Railway webhook / notification later).
- Optional: allow `exports_dir` / `store_dir` overrides via env vars so the prod
  config file isn't required (small change to `config.load_config`).
