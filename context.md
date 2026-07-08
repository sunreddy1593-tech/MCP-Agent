# Project Context: Weekly Mobile-Store Review Pulse

## Overview
Turn raw mobile-store feedback (App Store + Play Store reviews) into a **weekly pulse** the team can scan in minutes. The pulse answers three questions: what users care about, what they actually said, and what to do next.

Reviews are already public. The job is to **aggregate → theme → summarize → deliver** the insight through familiar surfaces:
- **Google Docs** for the written pulse
- **Gmail** for a draft email you can send to yourself

Auth and REST plumbing are handled through **MCP servers**, not bespoke Google API integration.

## End-to-End Flow ("Done" Definition)
1. **Pull** recent App Store and Play Store reviews for the product (last ~8–12 weeks).
2. **Cluster** them into a small set of themes and distill a one-page weekly note.
3. **Publish** that note where stakeholders can read it (Google Docs).
4. **Draft** an email to yourself (or an alias) that contains or links to that pulse (Gmail).

## Deliverables

### Weekly One-Page Pulse
Must include:
- **Top themes** — what people are talking about most (highlight top 3)
- **Real user quotes** — verbatim snippets from reviews, no invented wording
- **Three action ideas** — concrete next steps grounded in the themes

### Draft Email
Send yourself a draft email containing the weekly note (or a clear pointer/link to it).

## What Must Be Built
- **Import reviews** from roughly the last 8–12 weeks. Use whatever fields the export provides (e.g. rating, title, text, date).
- **Group reviews** into **at most 5 themes** (examples: onboarding, KYC, payments, statements, withdrawals — pick what fits the product).
- **Generate a weekly one-page note** with:
  - Top 3 themes (a subset of your themes as appropriate)
  - 3 user quotes
  - 3 action ideas
- **Draft an email** with the note to yourself or an alias.

## Integrations: Google Docs & Gmail via MCP
- Use **MCP (Model Context Protocol) servers** for Google Docs and Gmail — e.g. creating/updating the pulse document and creating the draft message.
- **MCP-first**, not "call Google APIs manually." Avoid a bespoke OAuth client + REST client as the primary integration path.
- Choose MCP servers or connectors the environment provides for Docs and Gmail so tooling stays consistent and auth/HTTP plumbing isn't duplicated.

## Who This Helps
| Audience | Why |
| --- | --- |
| Product / Growth | Prioritize fixes and improvements from real signals |
| Support | Align messaging with what users are actually saying |
| Leadership | One-page health check without drowning in raw reviews |

## Key Constraints
- **Reviews:** Use **public review exports only** — no scraping behind store logins or ToS-violating automation.
- **Themes:** Maximum **5 themes** for clustering; the written pulse highlights the **top 3**.
- **Length:** Keep the note scannable and **≤250 words** where applicable.
- **Privacy:** **No PII** — no usernames, emails, device IDs, or other identifiable reviewer data in any artifact. Quotes must be anonymous / stripped as needed.
