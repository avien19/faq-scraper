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

1. **Firecrawl map** — crawls the whole site to find all URLs, including help subdomains (e.g. `helpguide.simprogroup.com`)
2. **Categorise URLs** — faq > help > home > blog index (individual posts skipped)
3. **Select up to 5 pages** in that priority order
4. **Fetch each page** via Firecrawl (clean Markdown, max 60k chars)
5. **Extract FAQs** with Claude Haiku 4.5 via OpenRouter
6. **Return** base64-encoded CSV with columns: Competitor, Source URL, Question, Answer, Date
