# How Everything Works

## The User Journey

1. User goes to `faq.intelligentresources.app` → enters competitor URLs + their email → submits
2. The form POSTs to the n8n webhook
3. ~2–5 mins later they get an email with a CSV of competitor FAQs attached

---

## n8n Workflow (`tBiQRf6dFVhD2DvN`)

```
Webhook → Prepare Variables → Start Scrape Job (POST /scrape)
  → Wait 10s → Poll Result (GET /result/{job_id})
  → Still Processing? ──yes──→ Wait 10s (loops)
                      ──no──→ FAQs Found?
                                ──yes──→ Decode CSV → Send Report (Gmail)
                                ──no──→ Send No-Data Email (Gmail)
```

---

## Coolify Server (`api.py`)

- `POST /scrape` — accepts `{ urls }`, starts a background thread, returns `{ job_id }` instantly (no timeout)
- `GET /result/{job_id}` — returns `{ status: "processing" }` until done, then the full result

**Background thread does:**

1. **Firecrawl map** — crawls the whole site to find all URLs, including help subdomains (e.g. `helpguide.simprogroup.com`). For MadCap Flare help sites (JS-only navigation, no sitemap), reads TOC chunk files directly from `/Data/Tocs/` to discover all pages.
2. **Categorise URLs** — faq > help > home > blog index (individual posts skipped)
3. **Select up to 7 pages** in priority order: FAQ pages (up to 3) → help page → homepage → blog index pages (up to 2) → individual blog/article posts (up to 2)
4. **Fetch each page** via Firecrawl — requests both Markdown and raw HTML. Raw HTML is parsed with BeautifulSoup to include CSS-hidden content (e.g. collapsed accordions). Whichever is longer wins. Max 60k chars per page.
5. **Extract FAQs** with Claude Haiku 4.5 via OpenRouter
6. **Return** base64-encoded CSV with columns: Competitor, Source URL, Question, Answer, Date

---

## Limits & Constraints

### Per submission
| What | Limit |
|------|-------|
| URLs per submission | No hard limit, but each domain takes ~1–3 min |
| Pages scraped per domain | Max **7 pages** |
| FAQ/help pages per domain | Max **3** |
| Blog index pages per domain | Max **2** |
| Individual blog/article posts | Max **2** (LLM skips them if no FAQ section found) |
| Page content sent to LLM | Max **60,000 chars** per page |
| Deduplication | By question text — exact duplicates within the same job are dropped |

### URL discovery
| Scenario | Behaviour |
|----------|-----------|
| Site has a sitemap | Firecrawl map is used first (faster, more complete) |
| Site has no sitemap | Firecrawl crawls via `<a>` links |
| Help/docs subdomain detected | Separate map call on that subdomain (e.g. `helpguide.simprogroup.com`) |
| MadCap Flare help site | TOC chunk files read directly — bypasses JS-only navigation |
| No FAQ/help pages found anywhere | Falls back to probing common paths: `/faq`, `/help`, `/support`, `/kb` etc. |
| Firecrawl fails entirely | Falls back to plain HTTP + BeautifulSoup |

### Content extraction
| Scenario | Behaviour |
|----------|-----------|
| Normal page | Firecrawl Markdown (clean, structured) |
| Accordion / collapsed answers (e.g. Webflow, CSS `display:none`) | Raw HTML parsed by BeautifulSoup — includes hidden elements |
| JavaScript-rendered page | Firecrawl renders JS before extracting |
| Page under 100 chars after fetch | Skipped |

### What gets skipped
- Individual blog/article posts beyond the 2-post limit
- Pages shorter than 100 characters after fetching
- Duplicate questions within the same job run
- URLs that aren't on the same root domain as the submitted URL

### Known edge cases
- **Dynamically loaded FAQ content** (answers fetched via API on click) — neither Firecrawl nor BeautifulSoup can retrieve these. The user will get questions only or nothing.
- **Login-gated pages** — will return empty or an error page.
- **Very large sites** — Firecrawl map is capped at 500 URLs for the main domain and 300 for subdomains. Pages beyond that won't be discovered.
- **Rate limiting / bot protection** — some sites block scrapers even with a real User-Agent. These will return empty content.
- **MadCap Flare** — supported via TOC parsing. Other JS-heavy help platforms (Zendesk Guide, Intercom Articles, Salesforce Help) may have limited discovery.

### Timing
- Each domain takes roughly **1–3 minutes** depending on how many pages are scraped and how fast the site responds
- The n8n polling loop checks every **10 seconds**
- No hard timeout on the scrape job — it runs until complete or errors
