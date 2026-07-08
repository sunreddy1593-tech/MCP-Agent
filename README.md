# Weekly Mobile-Store Review Pulse

Turn public App Store / Play Store reviews into a **weekly one-page pulse** —
published to **Google Docs** and drafted in **Gmail**, with all Google I/O
routed through **MCP servers**. Themes and the written note are generated with
**Groq** as the LLM.

See [`context.md`](context.md), [`architecture.md`](architecture.md),
[`implementation-plan.md`](implementation-plan.md), and
[`edge-case.md`](edge-case.md) for the full design.

## Status

**Phase 0 — Project Setup & Scaffolding (done).** The pipeline runs end-to-end
as no-ops and prints the loaded run config. Stages 1–8 are implemented in later
phases (see the implementation plan).

## Project layout

```
review_pulse/
  orchestrator.py     # CLI entry point; sequences the pipeline
  config.py           # run-config schema + loader (Groq key from env)
  sources/            # App Store / Play Store adapters (ReviewSource interface)
  pipeline/           # normalize (+PII), theming, summarize (Groq), render
  delivery/           # Google Docs + Gmail clients (via MCP)
  llm/                # Groq LlmClient wrapper
  store/              # local artifacts + run manifest
config/               # example run config
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

## Run

```bash
python -m review_pulse.orchestrator run --config config/run_config.example.yaml
# or, if installed:  review-pulse run --config config/run_config.example.yaml
```

In Phase 0 this prints the loaded config and walks every stage as a no-op,
writing a run manifest to `store/runs/<run-id>/manifest.json`.

## Constraints (enforced across the pipeline)

- Public review exports only — no login-gated scraping.
- At most 5 themes; the note highlights the top 3.
- Note is ≤250 words.
- No PII in any artifact (stripped at ingestion, re-scanned at render).
- Gmail message is a **draft** only — never auto-sent.
- Google Docs & Gmail are reached via **MCP**, not direct Google APIs.

> **Note:** The Google Docs and Gmail MCP servers must be provisioned in the
> runtime for Phase 6 delivery. Only `user-alphavantage` is configured today.
