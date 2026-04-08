# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the Scraper

```bash
# Internal pipeline — reads from Google Sheets for dedup, appends new FAQs
python scraper.py

# Dry run — no Sheet reads/writes, prints what would be added
python scraper.py --dry-run

# Lead magnet API — deployed on Coolify, port 8000
uvicorn api:app --host 0.0.0.0 --port 8000
```

## Setup Requirements

```bash
pip install -r requirements.txt
```

`.env` file:
```
OPENROUTER_API_KEY=...   # current default LLM provider
FIRECRAWL_API_KEY=...    # URL discovery + page fetching for lead magnet
WEBSHARE_PROXY_URL=...   # optional proxy (http://user:pass@host:port)
```

Google Sheets auth (internal pipeline only): `credentials.json` from Google Cloud Console. First run opens browser to authorize; caches token as `authorized_user.json`.

## Architecture

Two separate pipelines:

**Internal pipeline** (LLM + Google Sheets):
`config.json → scraper.py → extractor.py → sheets.py`

**Lead magnet pipeline** (LLM, no Sheets, public):
`n8n → api.py → extractor.py → base64 CSV → n8n → Gmail`

---

### Lead Magnet — Full Flow

**User submits** the form at `faq.intelligentresources.app` with URLs + email.

**n8n workflow** (`tBiQRf6dFVhD2DvN` at `n8n.intelligentresources.app`):
1. **Webhook** — receives POST at `/webhook/faq-scraper` with `{ urls, email }`
2. **Prepare Variables** — extracts `urls` and `email` from webhook body
3. **Start Scrape Job** — POSTs `{ urls }` to `POST /scrape` → gets back `{ job_id }`
4. **Wait 10s** — waits before first poll
5. **Poll Result** — GETs `/result/{job_id}`
6. **Still Processing?** — IF `status == "processing"` → loops back to Wait 10s; otherwise continues
7. **FAQs Found?** — IF `found == true` → Decode CSV branch; else → Send No-Data Email
8. **Decode CSV** — Code node: base64-decodes the CSV into a binary attachment
9. **Send Report** — Gmail node: sends email with CSV attached + `analysis_html` as the email body (credential: `WBg8j6jRwooHB9oX`)
   OR **Send No-Data Email** — Gmail node: sends "no FAQ content found" message

**Coolify server** (`aos4gsswcog44sc04okwc000.intelligentresources.app`):
- `POST /scrape` — validates URLs, creates a job_id, starts background thread, returns `{ job_id, status: "processing" }` immediately
- `GET /result/{job_id}` — returns `{ status: "processing" }` while running, or `{ status: "done", found, count, csv, analysis_html, pages_checked }` when complete
- Background worker (`_run_scrape`):
  1. **URL discovery** (`discover_faq_urls`): Firecrawl map + sitemap (always both, merged) → MadCap Flare TOC fallback → common path probing
  2. **Categorise** each discovered URL: `faq` | `help` | `home` | `article_index` | `article_post` | `other`
  3. **Select** up to 15 pages in priority order: max 3 FAQ → 1 help → 1 home → 2 blog index → 5 blog posts → other pages fill ALL remaining slots (shortest path first, no separate inner cap)
  4. **Fetch** each page using two sources; uses the longest: static HTTP HTML (BS4-parsed) vs Firecrawl rawHtml (BS4-parsed). Max 60,000 chars.
  5. **Extract** FAQs via LLM (OpenRouter + Claude Haiku 4.5). FAQ pages → extract all Q&As. Blog posts & service pages → extract only explicit Q&A pairs; skip if none found.
  6. **Deduplicate** by question text, build CSV, base64-encode it
  7. **Analyse** (`analyze_faqs`): per-company LLM call (strategic insight + top 3 questions) + one combined LLM call (content opportunities + themes across all companies). Rendered to inline HTML (`findings_to_html`) for the email body.

---

### Key Design Decisions

**Async job pattern**: `POST /scrape` returns immediately to avoid Cloudflare's 100s timeout. n8n polls every 10s until done.

**URL discovery — Firecrawl + sitemap always combined**: Firecrawl map crawls the site via `<a>` link-following (up to 500 URLs, `include_subdomains=True`). The sitemap is **always** parsed alongside Firecrawl — not just as a fallback — because it catches pages that exist but aren't linked anywhere in the navigation (e.g. `/gtm-engineering/pricing`). Both URL sets are merged and deduplicated before categorisation.

**Subdomain detection**: Any discovered subdomain whose prefix is in `_HELP_SUBDOMAIN_KW` (help, support, docs, kb, faq, etc.) gets its own dedicated map call to discover its full URL list.

**MadCap Flare discovery** (`_map_urls_madcap_flare`): MadCap Flare help sites have an empty `<body>` and JS-only navigation — Firecrawl map only finds ~1 page. When a help subdomain returns <20 URLs from Firecrawl, the scraper fetches `/Data/HelpSystem.js` → follows the `Toc` reference → reads TOC chunk JS files → extracts all page URLs. This is how `helpguide.simprogroup.com` (767 pages, 10 FAQ pages) is fully discovered.

**Two-source content extraction** (`_fetch_page_markdown`): Every page is fetched two ways and whichever produces more text is used:
1. **Static HTTP** — plain `requests.get()` parsed by BeautifulSoup. Gets the raw HTML exactly as the server sent it before any JS runs. Catches CSS-hidden content like Webflow accordion answers (`display:none`) that Firecrawl's browser strips from the live DOM.
2. **Firecrawl rawHtml** — headless browser render parsed by BeautifulSoup. Catches JS-rendered/lazy-loaded content that isn't present in the static HTML at all (React/Vue SPAs, etc.).

Firecrawl Markdown is intentionally not used — it's a stripped-down version of the same browser render and would never produce more text than rawHtml.

**URL categorisation** (`_categorize_url`):
- `faq` — path contains faq/faqs/frequently-asked
- `help` — path contains help/support/docs/kb/knowledge-base etc.
- `home` — root path
- `article_index` — blog keyword with no slug after it (e.g. `/blog`, `/resources/webinars`)
- `article_post` — blog keyword + slug (2+ hyphens OR >20 chars) — included last (up to 5); LLM returns `[]` if post has no FAQ section
- `other` — everything else (service, pricing, feature pages). Fills ALL remaining slots after priority pages, sorted shortest-path-first. No separate inner cap — the 15-page total ceiling is the only limit.

**Slug detection** (`_is_slug`): A path segment is a post slug if it has `>=2 hyphens` OR `>20 chars`. Category names like `case-studies` have only 1 hyphen so they're treated as index pages.

**MAX_PAGE_CHARS = 60,000**: Raised from 20,000 to avoid cutting off FAQ sections near the bottom of long pages.

**LLM extraction** (`extractor.py`): Sends clean text to LLM, which returns `[{question, answer}]` JSON. Deduplication by exact question text within a job run.

**Key findings analysis** (`analyze_faqs` in `extractor.py`): After all FAQs are collected, the scraper runs N+1 LLM calls - one per company for a compact insight (strategic observation + top 3 buyer questions) and one combined call across all companies for content opportunities and cross-competitor themes. This keeps the email short regardless of how many companies are submitted. `findings_to_html` renders the output as inline-styled HTML for Gmail (no external CSS). Em-dashes and en-dashes are stripped from all LLM output.

---

### Files

- **`api.py`** — FastAPI lead magnet server. Async job pattern, Firecrawl + sitemap discovery, MadCap Flare TOC parsing, two-source page fetching, LLM extraction.
- **`extractor.py`** — LLM extraction logic + key findings analysis (per-company insight/questions + combined opportunities/themes). Supports anthropic, openai, gemini, openrouter providers.
- **`scraper.py`** — Internal pipeline orchestration. Reads config, fetches pages, deduplicates, writes to Sheets.
- **`sheets.py`** — Google Sheets I/O via gspread. OAuth auth. `get_competitor_urls()` reads the source sheet.
- **`config.json`** — LLM provider/model, sheet names, domain-level overrides.
- **`steps.md`** — Full step-by-step breakdown of the lead magnet pipeline including limits and constraints.

---

## Configuration (`config.json`)

- `llm.provider` — `openrouter` (current), `anthropic`, `openai`, or `gemini`
- `llm.model` — model ID (e.g. `anthropic/claude-haiku-4-5` for openrouter)
- `google_sheet.spreadsheet_name` / `worksheet_name` — output sheet (internal pipeline)
- `source_sheet.spreadsheet_name` / `worksheet_name` — input competitor URL list (overrides `competitors[]`)
- `request_delay_seconds` — sleep between URL fetches
- `domain_settings` — per-domain overrides: `{ "domain.com": { "force_browser": true, "use_proxy": true } }`

---

## Deployment

Server: Coolify at `aos4gsswcog44sc04okwc000.intelligentresources.app`
n8n: `n8n.intelligentresources.app`
- Lead magnet workflow: `tBiQRf6dFVhD2DvN`
- Internal weekly workflow: `D38mHD7qqMo1A9yF` (every Monday 9am, reads Kynection Competitor URLs sheet, notifies `accounts@intelligentresourcing.co`)

Lead magnet frontend: `faq.intelligentresources.app`

To deploy: push to `master` → redeploy in Coolify (auto git pull + restart).
