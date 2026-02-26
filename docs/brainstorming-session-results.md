# Brainstorming Session Results

**Session Date:** 2026-02-16
**Facilitator:** Business Analyst Mary
**Participant:** User

---

## Executive Summary

**Topic:** Building an automated FAQ scraper for Kynection competitors

**Session Goals:** Design a lean, practical system to scrape competitor FAQs daily and store them for reference when creating answer-optimized blog content. Cover both technical architecture and content strategy.

**Techniques Used:** First Principles Thinking, Morphological Analysis, Resource Constraints

**Total Ideas Generated:** 22+

### Key Themes Identified:
- FAQ sources are inconsistent - dedicated pages vs. buried in blogs
- URL paths and site labels don't match (e.g., "Signal Files" = `/blogs`)
- Discovery is the hardest part; extraction can be offloaded to AI
- "Lean and working" beats "comprehensive and complex"
- Manual steps are acceptable where they save significant build complexity
- Change detection = simple diff against previous snapshot

---

## Technique Sessions

### First Principles Thinking

**Description:** Stripped the problem down to fundamentals by walking through the manual process of gathering competitor FAQs.

**Ideas Generated:**
1. Two distinct FAQ source types exist: dedicated FAQ pages and FAQ sections buried in blog/article pages
2. Discovery is the core challenge - nav menus, dropdowns, URL paths, and page labels are all inconsistent across competitors
3. URL paths don't match labels (e.g., "Signal Files" lives at `/blogs`) - can't rely on URL pattern matching alone
4. FAQ identification is keyword-based - scan page content for "FAQ", "Frequently Asked Questions", "FAQs", "FAQ's"
5. "New" detection = diff against yesterday's snapshot, not date-based
6. Of 27 competitors, only 1 had a known FAQ URL at session start - massive discovery work needed

**Insights Discovered:**
- The manual process reveals the real complexity is in *finding* FAQs, not *extracting* them
- Human pattern recognition (scanning nav menus, spotting FAQ keywords) is what needs to be automated
- Blog-based FAQs are a secondary source that requires different extraction logic than dedicated FAQ pages

**Notable Connections:**
- The inconsistency of site structures is actually an argument FOR LLM-based extraction (AI handles messy variation better than rigid selectors)
- The keyword scanning approach is simple enough to automate reliably

---

### Morphological Analysis

**Description:** Mapped the system into 5 core parameters and explored options for each, selecting the best combination.

**Parameter Decisions:**

| Parameter | Options Explored | Decision | Rationale |
|-----------|-----------------|----------|-----------|
| **FAQ Discovery** | Manual mapping, Semi-auto crawl, Full auto-discovery, Hybrid | One-time auto-discovery script + verified config file | Balanced automation with reliability - auto-find URLs once, human verifies, daily scraper uses known URLs |
| **FAQ Extraction** | Simple HTML parsing, CSS selectors, Headless browser, AI-assisted | HTTP fetch + headless browser fallback + LLM extraction | AI handles structural variation across sites; headless browser fallback for JS-rendered content |
| **Storage** | Flat files, Markdown, SQLite, Google Sheets | Single Google Sheet (all competitors) | Easy to browse, share, filter when creating blog content |
| **Change Detection** | Overwrite + highlight, Append-only + date, Two tabs | Append-only with "Date First Seen" column | Never lose historical data; filter by date to spot new FAQs |
| **Content Use** | Manual browse, AI-assisted briefs, Trend alerts, Just data | Manual browse | Simple - open the sheet, scan competitor FAQs, get inspired for blog topics |

**Insights Discovered:**
- The 5 parameters are largely independent - each can be changed without affecting the others
- Google Sheets as storage doubles as the "UI" - no need to build a dashboard
- Append-only storage naturally creates a historical record of competitor FAQ evolution

**Notable Connections:**
- LLM extraction + Google Sheets = the entire "backend" is essentially an API call + a spreadsheet
- The config file from discovery becomes the single source of truth for what to scrape

---

### Resource Constraints

**Description:** Pressure-tested the design by asking "What's the absolute minimum version that works by Monday?"

**Ideas Generated:**
1. Start with 10 competitors instead of 27 - cuts problem in half
2. Skip the auto-discovery script entirely - manually find 10 URLs in 30 minutes
3. Skip headless browser for v1 - simple HTTP fetch only, add browser later for specific sites that need it
4. Python script running locally - no deployment, no infrastructure
5. Manual/cron scheduling - no n8n needed for v1
6. User already started manual URL mapping (added Blogs column to spreadsheet with URLs for several competitors)

**Insights Discovered:**
- 30 minutes of manual URL finding eliminates days of building a discovery crawler
- The cheapest, fastest path is: `requests` + LLM API + `gspread` - three libraries
- Most complexity is in edge cases that don't need to be solved for v1

**Notable Connections:**
- The user's manual URL mapping effort during the session validated the "manual discovery" approach in real-time
- Starting with 10 creates a working system that can be stress-tested before scaling to 27

---

## Idea Categorization

### Immediate Opportunities
*Ideas ready to implement now*

1. **Python FAQ Scraper Script (V1)**
   - Description: Python script using `requests` + LLM API + `gspread` to fetch 10 competitor pages, extract FAQs, and append to Google Sheet
   - Why immediate: All components are proven, cheap, and simple to wire together
   - Resources needed: Python, OpenAI/Claude API key, Google Sheets API credentials, 10 manually-verified URLs

2. **Manual URL Config File**
   - Description: JSON/YAML config file mapping each competitor to their FAQ page URL and/or blog URL
   - Why immediate: User already started mapping URLs in the spreadsheet
   - Resources needed: 30 minutes of manual browsing

3. **Google Sheet Structure**
   - Description: Single sheet with columns: Competitor, Source URL, Question, Answer, Date First Seen
   - Why immediate: Takes 2 minutes to create
   - Resources needed: Google account

### Future Innovations
*Ideas requiring development/research*

1. **Auto-Discovery Script**
   - Description: One-time script that crawls competitor sites to find FAQ/blog URLs automatically
   - Development needed: Sitemap parsing, nav link extraction, common URL pattern matching
   - Timeline estimate: After v1 is working, before scaling to 27 competitors

2. **Headless Browser Fallback**
   - Description: Puppeteer/Playwright integration for JS-heavy competitor sites where HTTP fetch returns empty content
   - Development needed: Identify which sites need it, integrate with extraction pipeline
   - Timeline estimate: After v1 testing reveals which sites fail with simple HTTP

3. **n8n or Hosted Deployment**
   - Description: Move from local Python script to n8n workflow or hosted cron job for true daily automation
   - Development needed: Workflow design, hosting setup, monitoring
   - Timeline estimate: After v1 is proven and stable

### Moonshots
*Ambitious, transformative concepts*

1. **AI Content Brief Generator**
   - Description: Second automation that reads the FAQ sheet and generates blog content briefs - "Competitors are answering these 5 questions about [topic], here's an outline that answers them better"
   - Transformative potential: Turns passive data collection into active content strategy
   - Challenges to overcome: Needs enough data accumulated first, prompt engineering for quality briefs

2. **Trend Detection & Alerts**
   - Description: Weekly summary of new FAQ topics across competitors, grouped by theme, delivered via email/Slack
   - Transformative potential: Early signal detection for emerging customer concerns
   - Challenges to overcome: Needs semantic grouping of questions, notification infrastructure

### Insights & Learnings
*Key realizations from the session*

- **Discovery vs. Extraction split:** The hardest part (finding FAQs) is best solved manually for v1; the repetitive part (extracting them) is best solved with AI
- **LLM as universal parser:** Using an LLM to extract FAQs means we don't need to build per-site scrapers or maintain CSS selectors
- **Config-driven architecture:** A simple config file of URLs makes the system easy to maintain, extend, and debug
- **"Good enough" beats "perfect":** A working scraper for 10 sites delivers 90% of the value with 20% of the effort

---

## Action Planning

### Top 3 Priority Ideas

#### #1 Priority: Manual URL Config File
- Rationale: Foundation for everything else - can't scrape without knowing where to scrape
- Next steps: Finish mapping remaining competitors in the first 10; create a JSON/YAML config file with competitor name, FAQ URL, blog URL
- Resources needed: Browser, 30 minutes
- Timeline: Before building the script

#### #2 Priority: Python FAQ Scraper Script (V1)
- Rationale: Core deliverable - the thing that actually does the work
- Next steps: Set up Python project, install dependencies (`requests`, `openai`/`anthropic`, `gspread`), build fetch → extract → store pipeline, test against 2-3 sites first
- Resources needed: Python 3, API keys, Google Sheets API service account
- Timeline: Weekend build

#### #3 Priority: Google Sheet Structure
- Rationale: Output destination - needs to exist before first scrape
- Next steps: Create sheet with columns (Competitor, Source URL, Question, Answer, Date First Seen), set up Google Sheets API access, share with team
- Resources needed: Google account, 10 minutes
- Timeline: Before first script run

---

## Reflection & Follow-up

### What Worked Well
- First Principles thinking surfaced the manual process, which directly informed architecture decisions
- Morphological Analysis provided a clear framework for making independent decisions on each system component
- Resource Constraints forced a practical, buildable scope that can be expanded later

### Areas for Further Exploration
- Blog FAQ extraction: How well does LLM extraction work on blog posts with FAQ sections vs. dedicated FAQ pages?
- Rate limiting: How to be respectful of competitor sites when scraping daily
- Error handling: What happens when a site is down or restructured?

### Recommended Follow-up Techniques
- Prototyping: Build the v1 script and test against 2-3 sites before scaling
- Retrospective: After 1 week of running, review what's working and what's not

### Questions That Emerged
- Which LLM API to use? (OpenAI vs. Claude vs. Gemini - cost and extraction quality comparison)
- How to handle competitors with multiple FAQ pages (like simprogroup with 2 FAQ URLs)?
- What's the best prompt for LLM FAQ extraction from raw HTML?
- Should the script handle pagination on blog pages?

### Next Session Planning
- **Suggested topics:** Technical implementation of the Python scraper, LLM prompt engineering for FAQ extraction
- **Recommended timeframe:** Immediately after URL config is complete
- **Preparation needed:** Finish manual URL mapping for first 10 competitors, set up Google Cloud project for Sheets API

---

## V1 Architecture Summary

```
[Config File: competitors.json]
        |
        v
[Python Script (cron/manual)]
        |
        v
[HTTP GET each URL] --fail?--> [Log & skip]
        |
        v
[Send HTML to LLM API]
  "Extract all FAQ Q&A pairs from this page"
        |
        v
[Compare against existing sheet entries]
        |
        v
[Append new FAQs to Google Sheet]
  Columns: Competitor | Source URL | Question | Answer | Date First Seen
```

## Competitor URL Mapping (In Progress)

| # | Competitor | FAQ Page | Blog Page | Status |
|---|-----------|----------|-----------|--------|
| 1 | cloudcon.com | - | /articles | Blog only |
| 2 | simprogroup.com | 2 helpguide FAQ URLs | /blog | Both |
| 3 | assignar.com | /frequently-asked-questions/ | - | FAQ only |
| 4 | aroflo.com | /resources/faq | /blog | Both |
| 5 | procore.com | - | /en-au/blog | Blog only |
| 6 | tradifyhq.com | Zendesk help center | /blog | Both |
| 7 | servicem8.com | - | blog.servicem8.com | Blog only |
| 8 | safetyculture.com | - | blog.safetyculture.com | Blog only |
| 9 | sitemate.com | - | - | Needs mapping |
| 10 | pronto.net | - | - | Needs mapping |

---

*Session facilitated using the BMAD-METHOD brainstorming framework*
