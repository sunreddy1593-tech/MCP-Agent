# Weekly Mobile-Store Review Pulse

Turn public App Store / Play Store reviews into a **weekly one-page pulse** —
appended to a **Google Doc** and drafted in **Gmail**, with all Google I/O
routed through **MCP servers**. Themes and the written note are generated with
**Groq** as the LLM, with deterministic offline fallbacks so the pipeline always
runs (and is fully testable) without any external service.

See [`context.md`](context.md), [`architecture.md`](architecture.md),
[`implementation-plan.md`](implementation-plan.md), and
[`edge-case.md`](edge-case.md) for the full design.

## Status

All phases implemented (0–8): ingestion, normalization + PII scrubbing, theming,
Groq summarization with guardrails, rendering, MCP delivery (Docs append + Gmail
draft), orchestration + persistence, and validation/hardening/docs.

## How it works

```
ingest → normalize (+PII strip) → theme/rank → summarize (Groq) → render → deliver (MCP)
sources    quality/lang filters    top 3 of ≤5    3 themes/quotes/    Doc body +   Docs append +
(App/Play)   dedupe, anonymize       + quotes       actions, ≤250w      email body   Gmail draft
```

Every stage persists an artifact to `store/runs/<run-id>/` and a `RunManifest`
records counts, timings, Doc/draft IDs, and status — so runs are auditable and
replayable.

## Project layout

```
review_pulse/
  orchestrator.py     # CLI entry point; sequences + times the pipeline
  config.py           # run-config schema + loader (secrets from env)
  sources/            # App Store / Play Store adapters + downloader
  pipeline/           # normalize (+PII), theming, summarize (Groq), render, validators
  delivery/           # Google Docs + Gmail clients via MCP (+ deliver orchestration)
  llm/                # Groq LlmClient wrapper
  store/              # local artifacts, run manifest, delivery ledger
config/               # example + prod run configs
data/samples/         # sample exports for local experimentation
tests/                # unit + end-to-end tests
.github/workflows/    # weekly GitHub Actions schedule
railway.toml          # Railway cron deployment (weekly)
```

## Setup

Requires Python 3.10+.

```bash
# 1. Create and activate a virtual environment
py -m venv .venv
.\.venv\Scripts\Activate.ps1        # Windows PowerShell
# source .venv/bin/activate          # macOS/Linux

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure secrets
copy .env.example .env               # then add your GROQ_API_KEY
```

## Configuration

Run configs are YAML (see [`config/run_config.example.yaml`](config/run_config.example.yaml)).
Secrets are **never** in YAML — they come from the environment / `.env`.

| Field | Meaning |
| --- | --- |
| `product_id` / `product_name` | App identifier + display name. |
| `window.weeks` | Review window, 8–12 weeks. |
| `themes.labels` | Theme taxonomy (≤5), data-derived per product. |
| `sources.app_store` / `sources.play_store` | `app_id` (+ country/lang) for the downloader. |
| `outputs.doc_id` | **Existing** Google Doc ID/URL to append to (the MCP server can't create one). |
| `outputs.email_to` | Recipient of the Gmail draft (draft-only, never sent). |
| `groq.model` / `groq.temperature` | LLM settings. |
| `exports_dir` / `store_dir` | Where raw exports and run artifacts live. |

Environment variables (from `.env` / the runtime):

| Variable | Purpose |
| --- | --- |
| `GROQ_API_KEY` | Enables LLM theming + summarization (omit to use offline fallbacks). |
| `GROQ_MODEL` | Optional model override. |
| `MCP_WORKSPACE_TRANSPORT` | `http` (default) or `stdio`. |
| `MCP_WORKSPACE_URL` | Streamable-HTTP endpoint of the Google Workspace MCP server. |
| `MCP_WORKSPACE_AUTH_TOKEN` | Bearer token for the HTTP transport. |
| `MCP_WORKSPACE_COMMAND` / `MCP_WORKSPACE_ARGS` | stdio launch (alternative to HTTP). |

## Run

```bash
# Download fresh public reviews into exports_dir
python -m review_pulse.orchestrator download --config config/run_config.example.yaml

# Run the full weekly pipeline
python -m review_pulse.orchestrator run --config config/run_config.example.yaml
# or, if installed:  review-pulse run --config config/run_config.example.yaml
```

Outputs land in `store/runs/<run-id>/`: `raw.json`, `normalized.json`,
`themes.json`, `note.json`, `note.md`, `email.txt`, and `manifest.json`. The CLI
exits non-zero if a run fails; a partial manifest is still written for diagnosis.

## Provisioning the Google Workspace MCP server (for real delivery)

Delivery targets the Google Workspace MCP server
([`sunreddy1593-tech/MCP-1`](https://github.com/sunreddy1593-tech/MCP-1)), which
exposes `append_to_google_doc`, `draft_gmail`, and `send_gmail` (the last is
intentionally never called). The app is an MCP **client** and holds no Google
credentials — auth is the server's responsibility.

1. **Enable APIs + OAuth** in Google Cloud: Google Docs API and Gmail API, with
   scopes `documents`, `gmail.compose`, `gmail.send`. Run the server's one-time
   `npm run auth` to mint a refresh token.
2. **Run the server** (HTTP or stdio) reachable from this runtime.
3. **Point the pipeline at it** via `MCP_WORKSPACE_URL` (+ `MCP_WORKSPACE_AUTH_TOKEN`)
   or a stdio command.
4. **Create the target Doc once** and set its ID in `outputs.doc_id` — the server
   *appends* a dated section each week and cannot create a Doc.

Without these, stages 1–5 still run and the rendered pulse is kept locally with
delivery status `pending` (nothing is lost).

## Scheduling (weekly cadence)

Two options are provided (choose one):

- **Railway cron** — [`railway.toml`](railway.toml) runs `download` then `run`
  every Monday 03:30 UTC (09:00 IST). Set `RUN_CONFIG` and the secrets as Railway
  variables; artifacts persist on a mounted volume (see `config/run_config.prod.yaml`).
- **GitHub Actions** — [`.github/workflows/weekly-pulse.yml`](.github/workflows/weekly-pulse.yml)
  runs the same cadence (plus manual `workflow_dispatch`) and uploads the run
  artifacts. Add `GROQ_API_KEY`, `MCP_WORKSPACE_URL`, `MCP_WORKSPACE_AUTH_TOKEN`
  as repository secrets.

## Testing

```bash
pip install -r requirements.txt pytest
pytest -q
```

The suite runs fully offline (no Groq key, no MCP server needed) and includes an
end-to-end test that verifies every hard constraint on a realistic fixture.

## Constraints (enforced in code across the pipeline)

- Public review exports only — no login-gated scraping.
- At most 5 themes; the note highlights the **top 3** (ranked by volume × severity).
- Note is **≤250 words** (validated + repair loop).
- **Zero PII** in any artifact (scrubbed at ingestion, re-scanned at render).
- Quotes are **verbatim** from source reviews — never paraphrased or invented.
- Gmail message is a **draft** only — `send_gmail` is never called.
- Google Docs & Gmail are reached via **MCP**, not direct Google APIs.

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Delivery status `pending`, no Doc/draft | Workspace MCP not configured/reachable | Set `MCP_WORKSPACE_URL` (+ token); artifacts are kept locally meanwhile. |
| `DOCUMENT_NOT_FOUND` | `outputs.doc_id` wrong or not shared | Point it at an existing Doc the authorized account can edit. |
| `CREDENTIALS_MISSING` / `INSUFFICIENT_SCOPE` | MCP server not authorized / missing scopes | Re-run the server's OAuth with Docs + Gmail scopes. |
| Groq `429 Too Many Requests` / slow theming | Rate limits (RPM/TPM/TPD) | Expected; the client backs off and degrades per-batch to the keyword classifier. Re-runs reuse cached classifications. |
| `note.json` missing / delivery blocked | Fewer than 3 rankable themes in the window | Widen `window.weeks` or download more reviews. |
| `no export found ... skipping source` | No downloaded exports | Run the `download` command first, or set `app_id`s / pass `--query`. |
| Run failed mid-pipeline | A stage raised | Read `store/runs/<run-id>/manifest.json` (`failed_stage`, `error`); completed-stage artifacts remain for replay. |
