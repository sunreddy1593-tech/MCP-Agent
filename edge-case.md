# Edge Cases & Corner Scenarios

This document catalogs edge cases and failure modes for the Weekly Mobile-Store Review Pulse pipeline, organized by stage (per `architecture.md`). Each case lists the **scenario**, **expected handling**, and **severity**.

Severity legend: 🔴 must handle before ship · 🟡 handle for robustness · 🟢 nice-to-have / log-only.

---

## 1. Ingestion (Source Adapters)

| # | Scenario | Expected Handling | Severity |
| --- | --- | --- | --- |
| 1.1 | Export file missing / unreadable / wrong path | Fail fast with a clear message naming the source; abort run before downstream stages | 🔴 |
| 1.2 | Empty export (0 reviews) | Skip source; if **all** sources empty, stop and report "no reviews in window" (no Doc/draft) | 🔴 |
| 1.3 | Malformed rows (bad CSV/JSON, ragged columns) | Skip the bad row, log line number + reason; continue; report skipped count | 🟡 |
| 1.4 | Missing expected fields (no `text`, no `date`) | Row with no `text` is dropped; missing `date` → excluded from window filter (logged) | 🔴 |
| 1.5 | Only one store has data | Proceed with available store; note single-source coverage in manifest | 🟡 |
| 1.6 | Date formats differ / timezone-naive across stores | Normalize all dates to ISO-8601 UTC before window filtering | 🔴 |
| 1.7 | Reviews outside 8–12 week window | Filtered out at read time; counts logged (ingested vs. in-window) | 🔴 |
| 1.8 | Non-UTF-8 / mixed encodings, emojis, RTL text | Decode defensively (fallback encoding); preserve emojis/RTL; never crash on encoding | 🟡 |
| 1.9 | Duplicate export passed twice / overlapping windows | Dedupe in Phase 2; ingestion tolerates overlap | 🟡 |
| 1.10 | Extremely large export (100k+ rows) | Stream/batch parse; avoid loading all in memory at once | 🟢 |
| 1.11 | Rating out of range (0, 6, null, non-numeric) | Clamp/validate; invalid rating kept as `null` rating, not dropped (text still valuable) | 🟡 |

---

## 2. Normalization & PII Stripping

| # | Scenario | Expected Handling | Severity |
| --- | --- | --- | --- |
| 2.1 | Email/phone/user handle embedded in review **text** | Scrubber masks it inside free text, not just structured fields | 🔴 |
| 2.2 | Device IDs / order numbers / long digit strings in text | Pattern-mask likely identifiers while preserving readability | 🔴 |
| 2.3 | Reviewer name only in a metadata column | Column dropped entirely at normalization | 🔴 |
| 2.4 | PII inside a would-be **quote** | Quote candidates are drawn only from already-scrubbed text; re-scan at render | 🔴 |
| 2.5 | Over-aggressive scrubbing destroys meaning (e.g. "$500 withdrawal" → masked) | Tune patterns to target identifiers, not all numbers; keep amounts/dates readable | 🟡 |
| 2.6 | Near-duplicate reviews (same text, different casing/whitespace) | Normalize + dedupe on canonicalized text | 🟡 |
| 2.7 | Empty or whitespace-only text after scrubbing | Drop from downstream set | 🟡 |
| 2.8 | Very short text ("ok", "👍", "bad") | Kept for rating signal but deprioritized as quote candidates | 🟢 |
| 2.9 | Non-English / multilingual reviews | Preserve; theming/summary should handle or explicitly note language scope | 🟡 |

---

## 3. Theming / Clustering

| # | Scenario | Expected Handling | Severity |
| --- | --- | --- | --- |
| 3.1 | Fewer than 3 distinct themes present | Report only the themes that exist; note may have <3 top themes rather than invented ones | 🔴 |
| 3.2 | More than 5 candidate themes | Hard-cap at 5; merge/absorb smallest into nearest or an "Other" bucket | 🔴 |
| 3.3 | Reviews that fit no theme | Assign to "Other/Uncategorized"; excluded from top-3 unless volume demands | 🟡 |
| 3.4 | Review spans multiple themes | Allow multi-label or assign to dominant theme; avoid double-counting in ranking | 🟡 |
| 3.5 | Tie in theme ranking (equal volume) | Deterministic tiebreak (e.g. lower avg rating first, then alphabetical) | 🟡 |
| 3.6 | One theme dominates (90%+ of reviews) | Still surface top 3; if only 1 real theme, report honestly | 🟡 |
| 3.7 | Sparse data (e.g. 3 total reviews) | Proceed but flag low-confidence; don't fabricate themes | 🔴 |
| 3.8 | No verbatim quote available for a top theme | Report theme without a quote rather than inventing one | 🔴 |
| 3.9 | Embedding/cluster call fails (if using embeddings) | Fall back to guided taxonomy classification | 🟡 |

---

## 4. Summarization (LLM)

| # | Scenario | Expected Handling | Severity |
| --- | --- | --- | --- |
| 4.1 | LLM invents a quote not in source | Quote validator rejects; retry/repair; if still failing, drop that quote | 🔴 |
| 4.2 | LLM lightly paraphrases a quote | Exact-match validation fails → treated as invented; rejected | 🔴 |
| 4.3 | Note exceeds 250 words | Word-budget validator triggers repair loop to compress | 🔴 |
| 4.4 | LLM returns fewer/more than 3 actions or quotes | Structure validator enforces exactly 3 each (or documented minimum) | 🔴 |
| 4.5 | LLM returns malformed/unparseable output | Retry with stricter format; cap retries; fail run with context if exhausted | 🔴 |
| 4.6 | Action ideas not grounded in themes | Validate actions reference the selected themes; regenerate if generic | 🟡 |
| 4.7 | LLM API timeout / rate limit / transient error | Exponential backoff retry; bounded attempts | 🟡 |
| 4.8 | LLM reintroduces PII from context | Post-generation PII re-scan before render | 🔴 |
| 4.9 | Non-deterministic output across runs | Low temperature + validators; acceptable variance documented | 🟢 |

---

## 5. Rendering

| # | Scenario | Expected Handling | Severity |
| --- | --- | --- | --- |
| 5.1 | Quotes contain characters that break Doc formatting | Escape/sanitize before insertion | 🟡 |
| 5.2 | Doc link not yet available when building email | Render email after Docs delivery, or use a placeholder resolved post-publish | 🔴 |
| 5.3 | Final PII re-scan catches leaked identifier | Block delivery; fail with the offending field flagged | 🔴 |
| 5.4 | Word count crept over budget after formatting | Re-validate post-render; repair if needed | 🟡 |

---

## 6. Delivery (MCP: Docs & Gmail)

| # | Scenario | Expected Handling | Severity |
| --- | --- | --- | --- |
| 6.1 | Docs/Gmail MCP server not provisioned/available | Fail delivery with actionable message; upstream artifacts persisted for retry | 🔴 |
| 6.2 | MCP auth expired / unauthorized | Trigger re-auth flow; retry once; surface clearly if still failing | 🔴 |
| 6.3 | MCP tool schema differs from expectation | Read schema before calling; adapt args; fail loudly on mismatch | 🔴 |
| 6.4 | Docs create succeeds but Gmail draft fails | Doc persists; record Doc ID; retry draft only (don't recreate Doc) | 🔴 |
| 6.5 | Re-run of same week | Idempotent: update existing Doc + draft via stored IDs, don't duplicate | 🔴 |
| 6.6 | Stored Doc/draft ID no longer exists (deleted) | Detect missing target; create fresh and update manifest | 🟡 |
| 6.7 | MCP call times out / partial write | Bounded retry; verify final state; avoid duplicate artifacts | 🟡 |
| 6.8 | Draft accidentally configured to send | Enforce draft-only; never call a send tool | 🔴 |
| 6.9 | Email alias / recipient misconfigured | Validate recipient config at run start | 🟡 |
| 6.10 | Rate limiting from MCP server | Backoff + retry within bounds | 🟢 |

---

## 7. Orchestration & Cross-Cutting

| # | Scenario | Expected Handling | Severity |
| --- | --- | --- | --- |
| 7.1 | Partial failure mid-pipeline | Persist completed-stage artifacts; allow replay from last good stage | 🔴 |
| 7.2 | Two runs triggered concurrently | Lock per (product, week) to prevent races/duplicates | 🟡 |
| 7.3 | Missing/invalid run config (bad window, no product) | Validate config at startup; refuse to run | 🔴 |
| 7.4 | Window with legitimately zero reviews | Skip delivery; write manifest noting "no pulse this week" | 🟡 |
| 7.5 | Clock/timezone drift affecting "this week" | Anchor window to explicit config dates, not just `now()` | 🟡 |
| 7.6 | Secrets/credentials in logs or artifacts | Never log config secrets; MCP holds Google auth, app holds none | 🔴 |
| 7.7 | Disk full / cannot write local store | Fail with clear I/O error; don't proceed to delivery | 🟢 |
| 7.8 | Non-reproducible run for debugging | Run manifest + persisted artifacts enable full replay | 🟡 |

---

## 8. Constraint-Guard Summary

These invariants must hold on **every** run, enforced in code (not just prompts):

- ✅ **Sources:** public exports only — no login-gated/ToS-violating data.
- ✅ **Themes:** at most 5; exactly the top 3 highlighted (or fewer if fewer exist — never invented).
- ✅ **Quotes:** verbatim from source; validator rejects any non-matching snippet.
- ✅ **Length:** note ≤250 words.
- ✅ **Privacy:** zero PII in any artifact; stripped at ingestion + re-scanned at render.
- ✅ **Delivery:** Gmail message is a **draft** only — never auto-sent.
- ✅ **Idempotency:** re-runs update existing Doc/draft, never duplicate.
