# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the Scraper

```bash
# Normal run (reads from Google Sheets for dedup, appends new FAQs)
python scraper.py

# Dry run (no Sheet reads/writes, prints what would be added)
python scraper.py --dry-run

# Lead magnet CLI (no LLM, no Sheets — outputs CSV)
python scraper_public.py --urls "https://example.com/faq" --output /tmp/faqs.csv

# Run the FastAPI lead magnet endpoint (deployed on Coolify)
uvicorn api:app --host 0.0.0.0 --port 8000
```

## Setup Requirements

Install dependencies:
```bash
pip install -r requirements.txt
# Also install provider-specific packages as needed:
pip install anthropic openai playwright
playwright install chromium
```

Create a `.env` file with API keys for whichever LLM provider is set in `config.json`:
```
OPENROUTER_API_KEY=...   # for openrouter provider (current default)
ANTHROPIC_API_KEY=...    # for anthropic provider
GEMINI_API_KEY=...       # for gemini provider
# OpenAI uses OPENAI_API_KEY from environment automatically

FIRECRAWL_API_KEY=...    # for lead magnet URL discovery + page fetching (api.py / scraper_public.py)

# Optional: Webshare proxy for sites that block scrapers (see config.json use_proxy flag)
# Format: http://username:password@proxy-host:port
WEBSHARE_PROXY_URL=...
```

Google Sheets authentication requires `credentials.json` (OAuth desktop app credentials from Google Cloud Console). On first run it opens a browser for authorization and caches the token in `authorized_user.json`.

## Architecture

There are two separate pipelines:

**Internal pipeline** (LLM-powered, writes to Google Sheets):
`config.json → scraper.py → extractor.py → sheets.py`

**Lead magnet pipeline** (no LLM, no Sheets, free-tier public):
- CLI: `scraper_public.py → extractor.py` (free mode) → CSV file
- API: `api.py → extractor.py` (free mode) → JSON with base64 CSV

**`scraper.py`** — orchestration entry point for the internal pipeline. Reads `config.json`, loads competitors either from config or from a live source Google Sheet, fetches pages, calls `extract_faqs()`, deduplicates against existing sheet rows, and appends new FAQs.

**`extractor.py`** — extraction logic with two modes:
- `mode="llm"` (default): sends cleaned page text to the configured LLM provider and returns JSON `[{question, answer}]` pairs. Supports `anthropic`, `openai`, `gemini`, and `openrouter` providers (imported lazily).
- `mode="free"`: no LLM, tries schema.org FAQPage JSON-LD → HTML class patterns → `<details>`/`<summary>` → text heuristics.

**`sheets.py`** — Google Sheets I/O via `gspread`. `get_existing_faqs()` reads all rows for deduplication; `append_faqs()` appends new rows; `get_competitor_urls()` reads the source competitor URL list. Sheet columns: `Competitor | Source URL | Question | Answer | Date First Seen`.

**`api.py`** — FastAPI wrapper for the lead magnet. n8n POSTs `{ "urls": "..." }` to `POST /scrape`. For each domain: (1) fetches `robots.txt` + `sitemap.xml` to discover all page URLs, (2) scores and selects up to 5 pages (FAQ/help pages first, then homepage, then blog articles), (3) extracts FAQs with LLM. Returns `{ "found": true, "count": N, "csv": "<base64>", "pages_checked": [...] }`.

**`scraper_public.py`** — CLI wrapper for the lead magnet. Called by n8n Execute Command node. Shares the same `discover_faq_urls()` pipeline from `api.py` and uses LLM extraction. Writes a CSV file.

## Key Design Decisions

**Fetching strategy** (`smart_fetch` in scraper.py): HTTP first, browser fallback. If HTTP content is under 100 chars (JS-rendered), or HTTP fails, it retries with headless Playwright. The browser mode dismisses cookie banners and clicks accordion/dropdown FAQ elements before extracting HTML. Per-competitor `force_browser: true` in config skips HTTP entirely.

**Deduplication**: Normalized key of `competitor::question` (lowercased, punctuation stripped). Runs in-memory during a session so the same run won't add duplicates even across multiple URLs for the same competitor.

**LLM as universal parser**: Rather than per-site CSS selectors, raw page text is sent to an LLM. The prompt instructs it to extract Q&A pairs from dedicated FAQ pages (all pairs) vs. blog posts (only explicit FAQ sections).

**Lead magnet URL discovery** (`discover_faq_urls` in `api.py`): Given any input URL, derives the base domain and fetches sitemaps (`robots.txt` → `sitemap.xml` → `sitemap_index.xml`). Categorises URLs by path keywords: `faq`/`faqs` → FAQ, `help`/`support` → help, `blog`/`articles` → article. Selects up to 5 pages in priority order: FAQ pages → help page → homepage → blog articles. Falls back to probing common paths (`/faq`, `/help`, etc. via HEAD) when the sitemap has no FAQ/help pages.

## Configuration (`config.json`)

- `llm.provider` — `openrouter` (current), `anthropic`, `openai`, or `gemini`
- `llm.model` — model ID for the chosen provider (e.g. `anthropic/claude-haiku-4-5` for openrouter)
- `google_sheet.spreadsheet_name` / `worksheet_name` — output sheet for scraped FAQs
- `source_sheet.spreadsheet_name` / `worksheet_name` — input sheet with competitor URLs (columns: homepage, FAQ URL, blog URL). If set, overrides the `competitors` array.
- `request_delay_seconds` — sleep between URL fetches
- `domain_settings` — domain-keyed overrides (e.g. `{ "aroflo.com": { "force_browser": true, "use_proxy": true } }`)
- Per-competitor in `competitors[]`: `faq_urls`, `blog_urls`, `homepage`, `force_browser`, `use_proxy`
