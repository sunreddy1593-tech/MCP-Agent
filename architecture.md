# Architecture: Weekly Mobile-Store Review Pulse

This document describes the system architecture for turning public App Store and Play Store reviews into a weekly one-page pulse, published to Google Docs and drafted as a Gmail message — all Google I/O routed through **MCP servers**.

---

## 1. Design Goals & Principles

| Principle | Implication |
| --- | --- |
| **MCP-first integrations** | No bespoke Google OAuth/REST clients. Docs & Gmail reached only via MCP tools. |
| **Deterministic pipeline** | Each stage has a clear input/output contract so runs are reproducible and testable. |
| **Privacy by construction** | PII is stripped at ingestion, before any data reaches the LLM or artifacts. |
| **Bounded output** | ≤5 themes, top 3 highlighted, ≤250-word note — enforced in code, not just prompts. |
| **Idempotent delivery** | Re-running a given week updates the same Doc / draft rather than duplicating. |
| **Provider-agnostic sources** | App Store & Play Store behind a common adapter interface. |

---

## 2. High-Level Architecture

```
                        ┌─────────────────────────────────────────────┐
                        │                Orchestrator                  │
                        │        (weekly job / CLI / scheduler)        │
                        └───────────────────┬─────────────────────────┘
                                            │
      ┌─────────────┬───────────────┬───────┴────────┬───────────────┬──────────────┐
      ▼             ▼               ▼                ▼               ▼              ▼
┌───────────┐ ┌───────────┐  ┌────────────┐  ┌────────────┐  ┌────────────┐ ┌────────────┐
│ Ingestion │ │Normalize +│  │  Theming/  │  │  Summarize │  │  Render    │ │  Deliver   │
│ (sources) │→│ PII Strip │→ │ Clustering │→ │  (LLM)     │→ │  (note)    │→│ (MCP: Docs │
│           │ │           │  │            │  │            │  │            │ │  + Gmail)  │
└───────────┘ └───────────┘  └────────────┘  └────────────┘  └────────────┘ └────────────┘
      │             │               │                │               │              │
      └─────────────┴───────────────┴────────────────┴───────────────┴──────────────┘
                                            │
                                    ┌───────▼────────┐
                                    │  Local Store   │
                                    │ (raw + staged  │
                                    │   artifacts)   │
                                    └────────────────┘
```

The pipeline is a linear sequence of pure-ish stages coordinated by an **Orchestrator**. Every stage reads from and writes to a local run store, enabling replay and inspection.

---

## 3. Components

### 3.1 Orchestrator
- Entry point (CLI command or scheduled job) that runs the full weekly pipeline.
- Owns the **run configuration**: product identifier, date window (last 8–12 weeks), theme taxonomy, output targets (Doc ID / email alias).
- Handles sequencing, retries, and error surfacing. Persists a `run manifest` (timestamps, counts, artifact IDs) per execution.

### 3.2 Ingestion Layer (Source Adapters)
- Reads **public review exports only** (CSV/JSON export files or public export APIs). No login-gated scraping.
- Two adapters behind a shared `ReviewSource` interface:
  - `AppStoreAdapter`
  - `PlayStoreAdapter`
- Each adapter maps provider fields to the canonical `Review` model (see §4).
- Filters to the configured date window at read time.

### 3.3 Normalization & PII Stripping
- Merges reviews from both stores into one canonical stream.
- **PII scrubber** runs here — before any downstream processing:
  - Drops/masks usernames, emails, phone numbers, device IDs, and free-text identifiers.
  - Reviews are reduced to anonymous `{rating, title, text, date, store}`.
- Deduplicates and drops empty/very-short text.
- Output: a clean, anonymized review set (the only data allowed to flow onward).

### 3.4 Theming / Clustering
- Groups reviews into **at most 5 themes** (e.g. onboarding, KYC, payments, statements, withdrawals — configurable taxonomy).
- Strategy (either or hybrid):
  - **Guided classification** against the predefined taxonomy (preferred for consistency) — run via the **Groq** `LlmClient`, or
  - **Embedding + clustering** with a cap of 5 clusters, then labeled. Note: Groq does not serve embeddings, so this path needs a separate embeddings provider; the Groq-based guided classification is the default to keep a single LLM dependency.
- Computes per-theme **volume + weighting** (count, avg rating, trend) to rank themes and select the **top 3**.
- Selects candidate **verbatim quotes** per top theme (exact snippets, no rewording).

### 3.5 Summarization (LLM — Groq)
- **LLM provider: Groq.** Uses the Groq API (OpenAI-compatible chat completions) for fast, low-latency inference — e.g. a Llama-family model such as `llama-3.3-70b-versatile` for quality or `llama-3.1-8b-instant` for speed. Model is configurable.
- Consumes ranked themes, stats, and candidate quotes.
- Produces the structured note payload:
  - Top 3 themes with short descriptions
  - 3 verbatim user quotes (passed through, never invented)
  - 3 concrete action ideas grounded in the themes
- Requests **structured/JSON output** (Groq JSON mode) so the payload parses deterministically.
- Guardrails: word budget (≤250 words), "no invented quotes" instruction, and a post-generation validator that confirms each emitted quote exists in the source set.
- Auth via `GROQ_API_KEY` (env/config, never committed). A thin `LlmClient` wrapper isolates the Groq SDK so the model/provider can be swapped without touching pipeline logic.

### 3.6 Rendering
- Converts the note payload into two representations:
  - **Doc body** (structured headings/sections for Google Docs).
  - **Email body** (inline note + link to the Doc).
- Enforces final constraints: word count, theme count, PII re-scan.

### 3.7 Delivery Layer (MCP Clients)
- **All Google I/O via MCP** — the app calls MCP tools, never Google REST directly.
- **Google Docs MCP**: create or update the weekly pulse document; returns a shareable Doc link.
- **Gmail MCP**: create a **draft** message to self/alias containing the note (or a pointer/link to the Doc). Draft only — never auto-send.
- Idempotency: the run manifest records the Doc ID and draft ID so re-runs update rather than duplicate.

### 3.8 Local Store
- Filesystem-backed store for: raw exports, anonymized reviews, theming output, generated note, and run manifest.
- Enables debugging, replay, and auditing without re-pulling sources.

---

## 4. Data Model (Canonical)

```
Review
  store        : "app_store" | "play_store"
  rating       : int (1–5)
  title        : string | null
  text         : string        # PII-stripped
  date         : ISO-8601
  # NO reviewer identity fields retained

Theme
  id           : string
  label        : string
  review_ids   : string[]
  count        : int
  avg_rating   : float
  rank         : int

Quote
  text         : string        # verbatim, anonymized
  theme_id     : string
  source_store : string

WeeklyNote
  week_of      : date
  product      : string
  top_themes   : Theme[3]
  quotes       : Quote[3]
  actions      : string[3]
  word_count   : int

RunManifest
  run_id       : string
  window       : {start, end}
  counts       : {ingested, kept, themes}
  doc_id       : string
  draft_id     : string
  created_at   : timestamp
```

---

## 5. Data Flow (Step by Step)

1. **Configure** — Orchestrator loads run config (product, window, taxonomy, targets).
2. **Ingest** — Source adapters read public exports → raw reviews within date window.
3. **Normalize + scrub** — merge, strip PII, dedupe → anonymized review set.
4. **Theme** — classify/cluster into ≤5 themes; rank; pick top 3; select verbatim quotes.
5. **Summarize** — LLM builds note payload (3 themes, 3 quotes, 3 actions); validate quotes & word budget.
6. **Render** — produce Doc body + email body.
7. **Deliver (Docs)** — MCP: create/update pulse Doc → capture Doc link.
8. **Deliver (Gmail)** — MCP: create draft to self/alias with note + Doc link.
9. **Record** — write run manifest with Doc ID + draft ID.

---

## 6. MCP Integration Detail

```
      Agent / App
          │  (tool calls only)
          ▼
   ┌───────────────┐        ┌───────────────┐
   │ Google Docs   │        │  Gmail        │
   │ MCP Server    │        │  MCP Server   │
   └──────┬────────┘        └──────┬────────┘
          │                        │
          ▼                        ▼
   Docs create/update       Draft create
   (auth handled by MCP)    (auth handled by MCP)
```

- The application depends on **MCP tool contracts**, not Google SDKs.
- Auth, tokens, and HTTP are the MCP server's responsibility — the app holds no Google credentials.
- Expected tool categories:
  - Docs: create document, update/insert content, return link.
  - Gmail: create draft (recipient = self/alias), set subject + body.

> **Environment note:** The Docs and Gmail MCP servers must be provisioned in the runtime. If unavailable, the Delivery Layer is the only stage that must be swapped — stages 1–6 are unaffected.

---

## 7. Cross-Cutting Concerns

### Privacy
- PII stripped at §3.3 before any LLM or artifact stage.
- Final PII re-scan at render time as a safety net.
- Only anonymized text is ever persisted or transmitted.

### Constraint Enforcement (in code, not just prompts)
- Themes hard-capped at 5; note highlights exactly top 3.
- Note validated to ≤250 words.
- Quote validator rejects any snippet not found verbatim in the source set.

### Error Handling & Idempotency
- Each stage fails loudly with context; partial artifacts persist for replay.
- Delivery is idempotent via stored Doc/draft IDs per week.
- Drafts are never auto-sent (human stays in the loop).

### Observability
- Run manifest = audit record (counts, IDs, timings).
- Local store retains intermediate artifacts for inspection.

---

## 8. Technology Choices (Suggested, Non-Binding)

| Concern | Suggested |
| --- | --- |
| Language / runtime | Python or Node.js |
| Review parsing | CSV/JSON readers per store export |
| LLM provider | **Groq** (OpenAI-compatible API; Llama-family model, e.g. `llama-3.3-70b-versatile`) |
| Theming | Groq LLM classification against taxonomy (default), or embeddings + capped clustering (separate embeddings provider) |
| Summarization | Groq LLM with JSON/structured output + validators |
| Google delivery | Google Docs MCP + Gmail MCP servers |
| Storage | Local filesystem (JSON) for run artifacts |
| Scheduling | Cron / scheduled job for weekly cadence |

---

## 9. Extensibility

- **New sources**: implement the `ReviewSource` interface (e.g. a third store).
- **New taxonomies**: swap the theme taxonomy config per product.
- **New delivery surfaces**: add MCP clients (e.g. Slack) alongside Docs/Gmail without touching upstream stages.
