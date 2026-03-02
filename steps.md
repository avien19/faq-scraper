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

## Coolify Server (`api.py`) — Step by Step

### Step 1 — Receive the request

`POST /scrape` is called with `{ "urls": "https://example.com" }`.
The server creates a **job ID** (UUID), stores it as `status: processing`, fires the work off in a background thread, and immediately returns `{ job_id }` to n8n. This avoids Cloudflare's 100-second timeout.

---

### Step 2 — Parse the submitted URLs

The raw `urls` string is split on commas and newlines. Each item is trimmed and validated — only strings starting with `http` are kept. Multiple URLs can be submitted in one job (one per competitor).

---

### Step 3 — URL Discovery (per domain)

For each submitted URL, the scraper derives the **base domain** (e.g. `https://www.simprogroup.com`) and runs discovery to find the best pages to scrape.

#### 3a. Firecrawl Map

Firecrawl crawls the entire site by following all `<a>` links recursively, up to **500 URLs** for the main domain. Returns a flat list of every URL it found.

- `include_subdomains=True` is set so it also finds URLs on subdomains like `helpguide.simprogroup.com`, `docs.example.com`, `support.example.com`.

#### 3b. Help subdomain detection

From the full URL list, the scraper looks for any subdomain whose prefix matches a known help keyword: `help`, `helpguide`, `support`, `docs`, `documentation`, `kb`, `faq`, `faqs`, `knowledge`.

If found (e.g. `helpguide.simprogroup.com`), a **separate Firecrawl map** is run on that subdomain alone (up to **300 URLs**) to get its full page list.

#### 3c. MadCap Flare fallback

Some help sites (like Simpro's `helpguide.simprogroup.com`) are built with **MadCap Flare** — a documentation platform that has an empty `<body>` and loads all navigation via JavaScript. Firecrawl map can only find ~1 page on these sites because there are no `<a>` links to follow.

When a help subdomain returns fewer than 20 URLs from Firecrawl, the scraper tries the MadCap Flare route:
1. Fetch `/Data/HelpSystem.js` — this file references the table of contents
2. Parse out the TOC file path (e.g. `Data/Tocs/Phase_5.js`)
3. Fetch the TOC file — it references chunk files (`Phase_5_Chunk0.js`, `Phase_5_Chunk1.js`, etc.)
4. Fetch each chunk — these are JavaScript objects containing every page URL and title on the site
5. Extract all `.htm` paths from the chunks and prepend the base URL

This is how all 10 FAQ pages on `helpguide.simprogroup.com` are found (out of 767 total pages).

#### 3d. Sitemap fallback

If Firecrawl fails entirely (API error, key missing), the scraper falls back to parsing the site's XML sitemap:
1. Fetch `robots.txt` — look for `Sitemap:` directives
2. Try `/sitemap.xml` and `/sitemap_index.xml`
3. Follow any sub-sitemap references (skipping image/video/news sitemaps)

#### 3e. Common path probing

If no FAQ or help pages were found from any of the above, the scraper does a series of `HEAD` requests to common paths:
`/faq`, `/faqs`, `/frequently-asked-questions`, `/help`, `/support`, `/help-center`, `/helpdesk`, `/knowledge-base`

Any path that returns a non-404 response is added to the list.

---

### Step 4 — Categorise every discovered URL

Each URL is categorised into one of five types:

| Category | How it's detected | Example |
|----------|-------------------|---------|
| `faq` | Path contains `faq`, `faqs`, or `frequently-asked` | `/resources/faq`, `/support/faqs` |
| `help` | Path contains `help`, `support`, `docs`, `kb`, `knowledge-base`, etc. | `/help-center`, `/docs` |
| `home` | Root path only (no segments) | `example.com/` |
| `article_index` | Path has a blog keyword with **no slug** after it — this is a listing/category page | `/blog`, `/resources/guides`, `/blog/case-studies` |
| `article_post` | Path has a blog keyword followed by a **slug** — this is an individual post | `/blog/how-to-manage-field-service` |

**How slugs are detected:** a path segment is a post slug if it has **2 or more hyphens** OR is **longer than 20 characters**. Short single-hyphen segments are category names, not posts.

Examples:
- `/blog/case-studies` → `case-studies` has 1 hyphen → **article_index** (category page)
- `/blog/service-reminder-email` → 2 hyphens → **article_post** (individual post)
- `/blog/how-to-manage-field-service` → 4 hyphens → **article_post** (individual post)
- `/resources/webinars/mastering-growth` → 2 segments after `resources` → **article_post**

**Important distinction:**
- `article_index` (listing pages like `/blog`, `/resources`) → max **2** selected
- `article_post` (individual posts with slugs) → max **5** selected

These are different things. The scraper picks **2 blog listing pages** + up to **5 individual posts**. For posts, the LLM only returns FAQs if the post contains an explicit FAQ section (heading that says "FAQs" or "Frequently Asked Questions") — otherwise it returns `[]` and nothing is added.

---

### Step 5 — Select pages to scrape

Pages are selected in strict priority order, up to a maximum of **12 pages per domain**:

| Priority | Type | Max |
|----------|------|-----|
| 1st | Dedicated FAQ pages | 3 |
| 2nd | Help/support pages | 1 |
| 3rd | Homepage | 1 |
| 4th | Blog/content index pages | 2 |
| 5th | Individual blog/article posts | 5 |

If the user submitted a specific FAQ or help URL (e.g. `https://aroflo.com/resources/faq`), that URL is always included first regardless of discovery.

---

### Step 6 — Fetch each page

Each selected URL is fetched using **three sources simultaneously** and the longest result is used:

| Source | How | What it captures |
|--------|-----|-----------------|
| **Firecrawl Markdown** | Firecrawl renders the page with a headless browser, returns clean structured Markdown | Normal page content, well-formatted |
| **Firecrawl rawHtml** | Same headless browser render; full HTML parsed by BeautifulSoup (strips script/style/svg/header/footer, keeps nav) | JS-rendered content; but may miss hidden elements stripped by the browser |
| **Static HTTP HTML** | Plain `requests.get()` of the URL, parsed by BeautifulSoup | The page source exactly as the server sent it — **always includes all DOM elements** regardless of CSS visibility |

**Why three sources?** Firecrawl's headless browser executes JavaScript which can strip `display:none` elements from the live DOM before returning `rawHtml`. Sites like Aroflo (Webflow) store accordion answers inside `<nav class="w-dropdown-list">` elements hidden by `display:none`. The static HTTP source fetches the raw server HTML where all these elements are always present, regardless of how the browser would render them. The static source consistently wins for accordion-heavy FAQ pages.

The server logs which source won: `[FETCH] Using static (N chars)` / `[FETCH] Using markdown (N chars)`.

Content is capped at **60,000 characters** before being sent to the LLM.

If Firecrawl fails entirely for a page, it falls back to plain HTTP + BeautifulSoup only.

Pages shorter than **100 characters** after fetching are skipped.

---

### Step 7 — Extract FAQs with the LLM

The page text is sent to **Claude Haiku 4.5** via OpenRouter with a prompt that instructs it to:

- For **dedicated FAQ pages**: extract every Q&A pair found
- For **blog posts**: only extract content from explicit FAQ sections (e.g. a heading that says "FAQs" or "Frequently Asked Questions"). If no FAQ section exists, return `[]`
- Return only valid JSON: `[{"question": "...", "answer": "..."}, ...]`
- Keep answers under 500 characters
- No markdown fencing, no explanation — just the JSON

If the LLM returns non-JSON or an empty array, zero FAQs are added for that page.

---

### Step 8 — Deduplicate

Within a single job, exact duplicate questions (same text) are dropped. This prevents the same Q&A appearing twice if it appears on multiple pages of the same site.

---

### Step 9 — Build the CSV and return

All collected rows are written into a CSV with columns:
`Competitor | Source URL | Question | Answer | Date`

The CSV is **base64-encoded** and returned in the job result:
```json
{
  "status": "done",
  "found": true,
  "count": 42,
  "csv": "<base64 string>",
  "pages_checked": ["https://...", "https://..."]
}
```

If zero FAQs were found across all pages:
```json
{
  "status": "done",
  "found": false,
  "message": "We couldn't find any FAQ content..."
}
```

---

### Step 10 — n8n decodes and emails

Back in n8n:
- **Decode CSV** node: base64-decodes the CSV string into a binary file attachment
- **Send Report** node: sends a Gmail with the CSV attached to the user's email
- **Send No-Data Email** node: sends a Gmail explaining no FAQ content was found and suggests trying a direct FAQ/help URL

---

## Limits & Constraints

### Per submission
| What | Limit |
|------|-------|
| Pages scraped per domain | Max **12 pages** |
| FAQ/help pages per domain | Max **3** |
| Blog index pages per domain | Max **2** |
| Individual blog/article posts | Max **5** (LLM skips if no FAQ section) |
| Page content sent to LLM | Max **60,000 chars** per page |
| Deduplication | Exact duplicate questions within the same job are dropped |

### URL discovery
| Scenario | Behaviour |
|----------|-----------|
| Normal site | Firecrawl crawls up to 500 URLs via `<a>` links |
| Help/docs subdomain detected | Separate Firecrawl map on that subdomain (up to 300 URLs) |
| MadCap Flare help site | TOC chunk files read directly — bypasses JS-only navigation |
| Sitemap available | Used as fallback if Firecrawl fails |
| No FAQ/help pages found | Falls back to probing common paths (`/faq`, `/help`, `/support`, etc.) |
| Firecrawl fails entirely | Falls back to plain HTTP + BeautifulSoup |

### Content extraction
| Scenario | Behaviour |
|----------|-----------|
| Normal page | Longest of: Firecrawl Markdown, Firecrawl rawHtml text, static HTTP text |
| Accordion / collapsed answers (CSS `display:none`) | Static HTTP HTML wins — contains hidden elements that Firecrawl's browser strips |
| JavaScript-rendered page (content not in static HTML) | Firecrawl markdown or rawHtml wins — Firecrawl renders JS before extracting |
| Page under 100 chars after fetch | Skipped |

### What gets skipped
- Blog/article posts beyond the 5-post limit
- Pages shorter than 100 characters after fetching
- Duplicate questions within the same job run
- URLs that aren't on the same root domain as the submitted URL

### Known edge cases
- **Dynamically loaded FAQ content** (answers fetched via API on click, not in the DOM) — answers will be missing or empty
- **Login-gated pages** — will return empty or an error page
- **Very large sites** — Firecrawl map is capped at 500 URLs for the main domain and 300 for subdomains; pages beyond that won't be discovered
- **Rate limiting / bot protection** — some sites block scrapers; these will return empty content
- **MadCap Flare** — fully supported via TOC parsing. Other JS-heavy help platforms (Zendesk Guide, Intercom Articles) may have limited discovery

### Timing
- Each page takes roughly **15–30 seconds** to fetch + extract
- A full domain (12 pages max) takes roughly **3–6 minutes**
- The n8n polling loop checks every **10 seconds**
- No hard timeout — the job runs until complete or errors
