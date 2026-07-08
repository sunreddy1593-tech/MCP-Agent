# Implementation Plan: Weekly Mobile-Store Review Pulse

A phase-wise plan to build the pipeline described in `architecture.md`, satisfying the deliverables and constraints in `context.md`. Each phase lists **goals, tasks, deliverables, and exit criteria** so progress is verifiable before moving on.

Constraints that apply throughout: public exports only, ≤5 themes (top 3 highlighted), ≤250-word note, no PII, MCP-first delivery, drafts never auto-sent.

---

## Phase 0 — Project Setup & Scaffolding

**Goal:** A runnable skeleton with configuration, folder structure, and dependency management.

**Tasks**
- Choose runtime (Python or Node.js) and initialize the project (dependency manifest, linter, formatter).
- Create the folder structure aligned with architecture components:
  - `sources/` (adapters), `pipeline/` (normalize, theming, summarize, render), `delivery/` (MCP clients), `store/` (local artifacts), `config/`.
- Add a run-config schema: product ID, date window (8–12 weeks), theme taxonomy, output targets (Doc ID / email alias).
- Add a `.env`/config loader (no secrets committed) — include `GROQ_API_KEY` for the LLM.
- Add the **Groq** SDK/client dependency and a thin `LlmClient` wrapper (configurable model, e.g. `llama-3.3-70b-versatile`).
- Stub the Orchestrator entry point (CLI command) that logs the planned run.

**Deliverables:** Project skeleton, config schema, empty stage modules, README stub.

**Exit criteria:** `run` command executes end-to-end as no-ops and prints the loaded run config.

---

## Phase 1 — Ingestion Layer (Source Adapters)

**Goal:** Read public App Store & Play Store review exports into a canonical model.

**Tasks**
- Define the canonical `Review` model (`store, rating, title, text, date`) — no reviewer identity fields.
- Implement the shared `ReviewSource` interface.
- Implement `AppStoreAdapter` and `PlayStoreAdapter` to parse public exports (CSV/JSON).
- Apply date-window filtering (last 8–12 weeks) at read time.
- Add sample/fixture export files for local testing.

**Deliverables:** Two working adapters, canonical `Review` model, sample data.

**Exit criteria:** Running ingestion on sample exports yields a merged list of `Review` objects within the window, with counts logged.

---

## Phase 2 — Normalization & PII Stripping

**Goal:** A clean, anonymized review set — the only data allowed downstream.

**Tasks**
- Merge both stores into one stream.
- Implement the **PII scrubber**: remove/mask usernames, emails, phone numbers, device IDs, and identifiable free-text.
- Apply **quality/language filters** (drop the review if any apply):
  - Fewer than **8 words** (too short / low-signal).
  - **Hindi** reviews (Devanagari-script content above a small ratio).
  - **Any emojis** (drop reviews that contain one or more emoji characters).
- Deduplicate reviews; drop empty text.
- Reduce each review to `{store, rating, title, text, date}`.
- Unit-test the scrubber and the filters against crafted cases.

**Deliverables:** Normalization module, PII scrubber, quality/language filters, with tests.

**Exit criteria:** Output contains zero identity fields; reviews are English, ≥8 words, and contain no emojis; PII/filter test suite passes; anonymized set persisted to local store.

---

## Phase 3 — Theming / Clustering

**Goal:** Group the ~1.6k normalized reviews into ≤5 themes, rank them, and select the top 3 with verbatim quotes.

### What the data says (from `normalized.json`)
A profiling pass over the current Groww dataset (1,649 reviews) drives the strategy below:
- **Polarized ratings** — 682×1★ and 600×5★ (plus 96/113/158 for 2–4★). Averaging alone hides pain; ranking must weight **severity** (share of 1–2★), not just volume.
- **The generic taxonomy under-fits.** Configured labels like `onboarding` (6 hits), `statements` (9), `KYC` (14) are rare. The corpus is actually dominated by:
  - **Charges & Fees** — `charge` 249, `brokerage` 106, `fee` 56
  - **Investing products / Trading** — `stock` 209, `fund`/`mutual fund` ~300, `SIP` 50, `IPO` 13, `order` 121
  - **App UX — UI & Updates** — `update` 152, `ui` 130, `interface` 90
  - **Customer Support** — `support` 120, `customer care` 31
  - **Account / Login / KYC** — `account` 116, `login` 17, `KYC` 14
  - **Withdrawals & Payments** — `withdraw` 65, `UPI` 25, `deposit` 14, `redeem`/`transfer` ~14
- **Length is quote-friendly** — median 18 words (min 8, max 104), so most kept reviews are viable verbatim quotes.

**Implication:** use a **data-informed taxonomy** (refresh the config labels to the themes above) rather than the generic default, and **rank by volume × severity**.

**Decisions (locked in):** the config taxonomy is **updated to the data-derived labels** (below), and classification uses **batched Groq** as the primary path (requires `GROQ_API_KEY`; ~35–55 calls/run). The keyword pre-pass doubles as the **offline fallback** when no key is present.

**Tasks**
- **Refresh the theme taxonomy** in config to the data-derived set (≤5): `charges_fees`, `trading_products`, `app_ux_updates`, `customer_support`, `withdrawals_payments`. Configurable per product; theme descriptions live with the theming module.
- **Classify at scale (batched Groq), token-aware:** assign every review to exactly one theme (or an **"Other"** bucket) using Groq **in batches** with JSON output. To respect the Groq limits (below), the **keyword pre-pass runs first as the primary classifier** and only reviews it can't confidently place are sent to the LLM — this cuts token usage dramatically versus sending all ~1.6k.
  - **Fallback:** if `GROQ_API_KEY` is absent, classify with the keyword pre-pass alone (deterministic; "Other" when no lexicon match) so the pipeline still runs offline and in tests.
- **Rank themes** by a combined score of **volume** and **severity** = `count` weighted by share of negative (1–2★) reviews; keep avg rating and counts for reporting. Apply **deterministic tie-breaks** (higher score → higher negative share → higher count → alphabetical) so runs are reproducible.
- **Select the top 3** themes (excluding "Other").
- **Extract candidate verbatim quotes** per top theme: exact snippets (already PII-scrubbed, ≥8 words), prefer clear, representative, on-theme reviews; bias toward negative reviews for pain-point themes; de-duplicate near-identical quotes; keep anonymized.
- Persist a `themes.json` artifact to the run store (theme label, description, count, avg_rating, negative_share, score, rank, review indices, candidate quotes) for inspection and downstream summarization.

**Groq rate-limit handling (`llama-3.3-70b-versatile`):**

| Limit | Value |
| --- | --- |
| Requests / minute (RPM) | 30 |
| Requests / day (RPD) | 1,000 |
| Tokens / minute (TPM) | 12,000 |
| Tokens / day (TPD) | 100,000 |

The binding constraint is **TPD (100K)** — a naive "send all 1.6k reviews" classification consumes ~99K tokens and starves Phase 4. Mitigations, in priority order:
- **Keyword pre-pass first, LLM only for the uncertain remainder.** Confidently keyword-matched reviews skip the LLM entirely; only ambiguous/"Other" candidates are sent to Groq. This keeps token spend a fraction of the full corpus.
- **Persistent classification cache** keyed by a review-content hash: never re-spend tokens re-classifying the same review across runs (also makes re-runs fast and deterministic).
- **Reserve a daily token budget for Phase 4** (e.g. cap Phase 3 LLM spend so summarization always has headroom); when the budget is hit, fall back to the keyword classifier for the rest.
- **Client-side throttling:** a rate limiter that keeps under **30 RPM** and **12K TPM** (estimate tokens/batch; pace batches accordingly), with **exponential backoff + retry on HTTP 429**; per-batch failures degrade to the keyword pre-pass so a run never hard-fails on limits.
- **Token-aware batch sizing:** size each batch so its estimated tokens stay within TPM and truncate over-long review text.

**Deliverables:** Theming module (keyword pre-pass + token-aware, cached, rate-limited batched Groq classification with offline fallback), `Theme` + `Quote` models, volume×severity ranking, `themes.json` artifact.

**Exit criteria:** At most 5 themes produced (plus optional "Other"); top 3 selected deterministically; each top theme has candidate verbatim quotes traceable to specific source reviews; results persisted and reproducible across runs; a full run stays within the Groq RPM/TPM/TPD limits and leaves token headroom for Phase 4.

**Scale & robustness notes (grounded in the data):**
- ~1.6k reviews exceed a single prompt — batch and (optionally) cache classification results by review hash.
- Reviews spanning multiple themes → assign the dominant theme; avoid double-counting in ranking.
- If a would-be top theme has no clean quote, report it without a fabricated one (feeds the Phase 4 "no invented quotes" rule).
- Keep an "Other" bucket for genuinely off-taxonomy reviews; exclude it from the top 3 unless volume+severity demand attention.

---

## Phase 4 — Summarization (LLM)

**Goal:** Produce the structured weekly note payload with guardrails, using **Groq** as the LLM.

**Inputs from Phase 3:** the **top 3 `Theme` objects** (rank 1–3) read from `themes.json`, each carrying its `label`, `description`, stats (`count`, `avg_rating`, `negative_share`, `score`), `review_indices`, and a set of pre-selected, PII-scrubbed candidate `Quote`s (each with `text`, `rating`, `date`, `store`). Phase 4 does **not** re-derive or re-rank themes; it summarizes what Phase 3 produced. The data-derived `label`s (`charges_fees`, `trading_products`, `app_ux_updates`, `customer_support`, `withdrawals_payments`) are mapped to human-readable display names (e.g. `charges_fees` → "Charges & Fees") via a display-name map in the summarization module.

**Decisions (locked in):** Groq **JSON mode** is the primary path with a **bounded retry/repair loop (≤3 attempts)**; the deterministic template is the **offline fallback**. Emitted quotes are validated against the **Phase 3 candidate quote pool** (never re-derived or invented), and the note records which path produced it via `generated_by` (`"groq"` | `"fallback"`).

**Tasks**
- Build the prompt from the top 3 themes: display names, one-line descriptions, a **stat string** derived from Phase 3 metrics (e.g. `"249 reviews, 62% negative, avg 1.8★"`), and their candidate quotes (the pool the LLM may choose from).
- Call **Groq** via the `LlmClient.complete_json` wrapper (JSON mode, configurable model, e.g. `llama-3.3-70b-versatile`). This is a **single small call** that fits within the daily token **headroom Phase 3 reserves** for summarization.
- Produce `WeeklyNote`: `week_of`, `product`, exactly **3 `NoteTheme`s** (`name` + `summary` + `stat`), exactly **3 verbatim quotes** (chosen from the candidate `Quote`s), **3 concrete action ideas** grounded in the themes, plus `word_count` and `generated_by`.
- Implement validators (enforced in code, not just via the prompt):
  - **Quote validator** — every emitted quote must match a Phase 3 candidate quote exactly, or be a **verbatim substring** of one (no paraphrase, no invention).
  - **Word-budget validator** — ≤250 words over the note's prose.
  - **Structure check** — exactly 3 themes, 3 quotes, 3 actions.
  - **PII re-scan** — run `pii.contains_pii` over generated text as a safety net.
- Add a bounded **retry/repair loop** (≤3 attempts) that feeds the specific validation errors back to the model.
- **Offline fallback:** if `GROQ_API_KEY` is absent (or the Groq path fails validation after retries), compose a deterministic template note from the top themes and their first candidate quote — mirroring the Phase 3 keyword fallback so the pipeline stays runnable/testable — flagged `generated_by = "fallback"`.
- Persist a `note.json` artifact to the run store.

**Deliverables:** Summarization module (Groq + deterministic offline fallback), `WeeklyNote`/`NoteTheme` models, validator suite, `note.json` artifact.

**Exit criteria:** Generated note passes all validators; the 3 quotes are verbatim against the Phase 3 candidate pool and traceable to specific source reviews; exactly 3 themes/quotes/actions; ≤250 words; note persisted and flagged with its generation path.

---

## Phase 5 — Rendering

**Goal:** Turn the validated `WeeklyNote` payload into Doc and email representations.

**Inputs from Phase 4:** the `WeeklyNote` — 3 `NoteTheme`s (display `name` + `summary` + `stat`), 3 verbatim `quotes`, 3 `actions`, plus `word_count` and `generated_by`.

**Tasks**
- Render a **Doc body** from `WeeklyNote` — title `"{product} — Weekly Review Pulse ({week_of})"`, then sections: **Top themes** (display name + one-line summary + the Phase 4 stat string, e.g. count / % negative / avg rating), **Real user quotes** (the 3 verbatim quotes, optionally attributed with the quote's `store`/`rating` carried through from Phase 3), **3 action ideas**. When `generated_by == "fallback"`, mark the note as non-LLM so readers know it was template-generated.
- Render an **email body** — subject line + inline note, with a placeholder for the Doc link that is resolved after Docs delivery (Phase 6).
- **Sanitize/escape** quote text so it can't break Doc/markdown formatting.
- Run a final **PII re-scan** (`pii.contains_pii`) over both rendered outputs; **block delivery** on any leak.
- Re-check constraints post-render: ≤250 words, exactly 3 themes/quotes/actions.
- Persist rendered artifacts (`note.md`, `email.txt`) to the run store and return a `RenderedNote` (`doc_body` + `email_body`).

**Deliverables:** Renderer producing `RenderedNote` (doc_body + email_body); persisted rendered artifacts.

**Exit criteria:** Both outputs generated from the data-derived top-3 themes; final PII scan clean; constraints re-validated; artifacts persisted.

---

## Phase 6 — Delivery Layer (MCP Integration)

**Goal:** Append the pulse to a Google Doc and create a Gmail draft — via MCP only, using the **MCP Google Workspace server** ([`sunreddy1593-tech/MCP-1`](https://github.com/sunreddy1593-tech/MCP-1)).

**Inputs from Phase 5:** the `RenderedNote` (`doc_body` + `email_body`) and the run's `outputs` config (`doc_id`, `doc_title`, `email_to`).

**Target MCP server & tool contracts (verified against the repo):** The server exposes **exactly three tools** over stdio or streamable HTTP (registered under a client id like `google-workspace`), using OAuth2 user-delegated auth with an auto-refreshed refresh token. Only these contracts may be relied on:

| Tool | Input | Output |
| --- | --- | --- |
| `append_to_google_doc` | `document_id` (req; raw ID **or** full Docs URL), `content` (req), `add_newline_before` (default `true`) | `{ document_id, status: "appended" }` |
| `draft_gmail` | `to: string[]` (req), `subject` (req), `body` (req), `body_type: "text"\|"html"` (default `text`), `cc?`, `bcc?`, `reply_to?` | `{ draft_id, message_id, status: "drafted" }` |
| `send_gmail` | same as `draft_gmail` | `{ message_id, thread_id, status: "sent" }` — **must never be called** (draft-only constraint) |

**Capability constraints this imposes (important):**
- **Docs is append-only into a pre-existing Doc.** There is **no create-Doc tool** and the tool **never overwrites**. So `outputs.doc_id` must point to a Doc that already exists (created/shared manually once); the pipeline appends a new dated section each week rather than creating or replacing a Doc.
- **No Doc URL is returned.** `append_to_google_doc` returns only `{document_id, status}`; derive the link as `https://docs.google.com/document/d/{document_id}/edit`.
- **Native draft-only path.** `draft_gmail` creates a draft without sending, satisfying the "drafts never auto-sent" constraint; `send_gmail` is intentionally left uncalled.

**Tasks**
- **Provision the Google Workspace MCP server** in the runtime (only `user-alphavantage` is configured today — see note below). Requires Google Cloud OAuth setup (Gmail API + Google Docs API enabled, scopes `gmail.send`, `gmail.compose`, `documents`) and a one-time `npm run auth` to mint the refresh token.
- Call MCP tools via the `CallMcpTool` pattern; **read each tool's schema before calling** and adapt arguments to it.
- Implement `DocsClient.publish`: **append** `doc_body` to the existing Doc identified by `outputs.doc_id` via `append_to_google_doc` (`add_newline_before=true` to separate weekly sections); construct and return `{doc_id, doc_url}` from the returned `document_id`. Treat a missing/invalid Doc (`DOCUMENT_NOT_FOUND`) as a configuration error surfaced to the operator.
- Implement `GmailClient.create_draft`: call `draft_gmail` with `to=[outputs.email_to]`, the rendered subject, and `email_body` (with the resolved Doc link substituted); choose `body_type` to match the render (`text` or `html`); **never call `send_gmail`**; return `{draft_id}`.
- Wire **idempotency:** store `doc_id`, `doc_url`, `draft_id`, and a per-week delivery marker in the `RunManifest`. Because Docs is append-only, guard re-runs of the **same week** so they don't append a duplicate section (skip or replace-in-manifest); a **new** week appends a fresh section as intended.
- **Map structured tool errors:** handle the server's machine-readable `error.code`s — `CREDENTIALS_MISSING`/`INSUFFICIENT_SCOPE` (auth/provisioning), `DOCUMENT_NOT_FOUND` (bad `doc_id`), `RATE_LIMITED` (backoff + retry), `NETWORK_ERROR`/`GOOGLE_API_ERROR` (transient retry then degrade), `INVALID_INPUT` (fix payload).
- **Graceful degradation (fallback):** if the Workspace MCP server is unavailable, keep the Phase 5 rendered `note.md`/`email.txt` in the run store and log an actionable message with delivery status `pending` (so upstream work isn't lost). If the Doc append succeeds but the draft fails, keep the Doc result and retry only the draft.

**Deliverables:** Docs MCP client (append-based) and Gmail MCP client (draft-only) targeting the Google Workspace server, idempotent delivery keyed on the `RunManifest`, structured error mapping, and local-artifact fallback when MCP is absent.

**Exit criteria:** With the Workspace MCP present, a run **appends** the pulse to the configured Doc and creates a real Gmail **draft** (never a sent email); re-running the same week does not duplicate the Doc section or draft; without MCP, the rendered note is persisted locally and delivery status is reported clearly.

**Environment note:** This runtime currently exposes only the `user-alphavantage` MCP server. The **Google Workspace MCP server ([`sunreddy1593-tech/MCP-1`](https://github.com/sunreddy1593-tech/MCP-1)) must be provisioned** (built, OAuth-authorized, and registered as an MCP server) before Phase 6 can deliver; until then the fallback path persists the pulse locally. Note also that a target Google Doc must be created and its ID placed in `outputs.doc_id`, since the server appends to an existing Doc and cannot create one. Stages 1–5 are unaffected.

---

## Phase 7 — Orchestration & Persistence

**Goal:** A single command runs the full pipeline reliably with audit records.

**Tasks**
- Sequence all stages in the Orchestrator with clear per-stage logging.
- Persist intermediate artifacts (raw, anonymized, themed, note) to the local store.
- Write the `RunManifest` (window, counts, Doc ID, draft ID, timings).
- Add error handling: fail loudly with context; keep partial artifacts for replay.

**Deliverables:** Full orchestrated pipeline, run manifest, local store layout.

**Exit criteria:** One command runs ingest → deliver; manifest written; failures are diagnosable and replayable.

---

## Phase 8 — Validation, Hardening & Docs

**Goal:** Confidence, safety nets, and operability.

**Tasks**
- End-to-end test on realistic sample exports.
- Verify all constraints: ≤5 themes, top 3, ≤250 words, zero PII, verbatim quotes, draft-only.
- Add scheduling for weekly cadence (cron/scheduled job).
- Write README/runbook: setup, config, running, provisioning MCP servers, troubleshooting.

**Deliverables:** Test suite, scheduler config, documentation.

**Exit criteria:** Green E2E run producing a valid weekly pulse Doc + Gmail draft; documented and schedulable.

---

## Phase Dependency Overview

```
Phase 0 ─▶ Phase 1 ─▶ Phase 2 ─▶ Phase 3 ─▶ Phase 4 ─▶ Phase 5 ─▶ Phase 6 ─▶ Phase 7 ─▶ Phase 8
 setup     ingest    normalize   theming    summarize   render    deliver    orchestr.  harden
                     (+PII)                 (+guards)             (MCP)
```

Phases 1–5 are source-agnostic and testable with local fixtures. Phase 6 is the only phase that hard-depends on external MCP servers, so it can proceed in parallel once fixtures exist upstream.

---

## Milestones

| Milestone | Phases | Outcome |
| --- | --- | --- |
| **M1 — Data ready** | 0–2 | Anonymized, windowed review set from both stores |
| **M2 — Insight ready** | 3–5 | Validated weekly note rendered for Doc + email |
| **M3 — Delivered** | 6–7 | Doc published + Gmail draft created via MCP, orchestrated |
| **M4 — Production-ready** | 8 | Tested, scheduled, documented |

---

## Risks & Mitigations

| Risk | Mitigation |
| --- | --- |
| Docs/Gmail MCP servers not provisioned | Build 1–5 against fixtures; isolate delivery so only Phase 6 is blocked |
| LLM invents quotes | Verbatim quote validator rejects non-source snippets |
| PII leakage | Strip at ingestion + re-scan at render; unit-tested scrubber |
| Note exceeds length | Enforce ≤250-word budget in code with repair loop |
| Duplicate artifacts on re-run | Idempotent delivery keyed on stored Doc/draft IDs |
| Store export format drift | Adapter interface isolates parsing per source |
