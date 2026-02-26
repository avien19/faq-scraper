# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the Scraper

```bash
# Normal run (reads from Google Sheets for dedup, appends new FAQs)
python scraper.py

# Dry run (no Sheet reads/writes, prints what would be added)
python scraper.py --dry-run
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
ANTHROPIC_API_KEY=...
GEMINI_API_KEY=...
# OpenAI uses OPENAI_API_KEY from environment automatically

# Optional: Webshare proxy for sites that block scrapers (see config.json use_proxy flag)
# Format: http://username:password@proxy-host:port
WEBSHARE_PROXY_URL=...
```

Google Sheets authentication requires `credentials.json` (OAuth desktop app credentials from Google Cloud Console). On first run it opens a browser for authorization and caches the token in `authorized_user.json`.

## Architecture

The pipeline is: **config.json → scraper.py → extractor.py → sheets.py**

**`scraper.py`** — orchestration entry point. Reads `config.json`, iterates competitors, fetches pages, calls `extract_faqs()`, deduplicates against existing sheet rows, and appends new FAQs.

**`extractor.py`** — LLM extraction. Sends cleaned page text to the configured LLM provider (anthropic/openai/gemini) with a prompt that returns JSON `[{question, answer}]` pairs. Providers are imported lazily so only the one in use needs to be installed.

**`sheets.py`** — Google Sheets I/O via `gspread`. `get_existing_faqs()` reads all rows for deduplication; `append_faqs()` appends new rows. Sheet columns are: `Competitor | Source URL | Question | Answer | Date First Seen`.

## Key Design Decisions

**Fetching strategy** (`smart_fetch` in scraper.py): HTTP first, browser fallback. If HTTP content is under 100 chars (JS-rendered), or HTTP fails, it retries with headless Playwright. The browser mode dismisses cookie banners and clicks accordion/dropdown FAQ elements before extracting HTML. Per-competitor `force_browser: true` in config skips HTTP entirely.

**Deduplication**: Normalized key of `competitor::question` (lowercased, punctuation stripped). Runs in-memory during a session so the same run won't add duplicates even across multiple URLs for the same competitor.

**LLM as universal parser**: Rather than per-site CSS selectors, raw page text is sent to an LLM. The prompt instructs it to extract Q&A pairs from dedicated FAQ pages (all pairs) vs. blog posts (only explicit FAQ sections).

## Configuration (`config.json`)

- `llm.provider` — `anthropic`, `openai`, or `gemini`
- `llm.model` — model ID for the chosen provider
- `google_sheet.spreadsheet_name` / `worksheet_name` — must match the actual Google Sheet
- `request_delay_seconds` — sleep between URL fetches
- Per-competitor: `faq_urls`, `blog_urls`, `force_browser`, `use_proxy`
