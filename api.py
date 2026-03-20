"""api.py — FastAPI lead magnet server.

Deployed on Coolify. n8n POSTs URLs here, polls for results.

POST /scrape  →  { job_id, status: "processing" }
GET  /result/{job_id}  →  { status, found, count, csv (base64), pages_checked }

Pipeline per domain:
  1. URL discovery — Firecrawl map (up to 500 URLs, follows <a> links) + sitemap
     (always both, merged). Help subdomains get their own map (300 URLs). MadCap
     Flare fallback for JS-only help portals. Common path probing if nothing found.
  2. Categorise each URL: faq | help | home | article_index | article_post | other
  3. Select up to 12 pages in priority order:
       max 3 FAQ → 1 help → 1 home → 2 article_index → 5 article_post
       → other pages fill ALL remaining slots (shortest path first, no inner cap)
     Total cap: 15 pages per domain.
  4. Fetch each page — static HTTP (BS4) vs Firecrawl rawHtml (BS4); longest wins.
     Max 60,000 chars per page.
  5. Extract FAQs via LLM (Claude Haiku 4.5 via OpenRouter).
  6. Deduplicate by question text, build CSV, base64-encode.
"""

import base64
import csv
import io
import json
import os
import re
import time
import uuid
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from urllib.parse import urlparse

import requests as _requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

with open("config.json") as _f:
    _cfg = json.load(_f)
LLM_PROVIDER = _cfg["llm"]["provider"]
LLM_MODEL = _cfg["llm"]["model"]

_FIRECRAWL_KEY = os.environ.get("FIRECRAWL_API_KEY")

app = FastAPI(title="FAQ Scraper API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Async job store
# ---------------------------------------------------------------------------
_jobs: dict = {}           # job_id → {"status": "processing"|"done"|"error", "result": ...}
_executor = ThreadPoolExecutor(max_workers=4)

# Caps
MAX_URLS_PER_DOMAIN = 15
MAX_FAQ_PAGES = 3          # dedicated FAQ/help pages
MAX_ARTICLE_INDEXES = 2    # blog/content INDEX pages (not posts)
MAX_ARTICLE_POSTS = 5      # individual blog/article posts (LLM returns [] if no FAQ section)
# Other pages (service, pricing, feature pages) fill ALL remaining slots after priority pages.
# No separate inner cap — the total MAX_URLS_PER_DOMAIN=12 is the only ceiling.
MAX_PAGE_CHARS = 60_000    # truncate content before sending to LLM (Haiku handles this fine)

MIN_CONTENT_LENGTH = 100   # chars — skip pages shorter than this

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
}

# Path keywords for URL categorisation
_FAQ_KW  = {"faq", "faqs", "frequently-asked", "frequently_asked"}
_HELP_KW = {
    "help", "support", "help-center", "helpdesk", "helpcentre",
    "knowledge-base", "knowledgebase", "kb", "docs", "documentation",
}
_BLOG_KW = {
    "blog", "blogs",
    "article", "articles",
    "news",
    "resources",
    "insights",
    "post", "posts",
    "guide", "guides",
    "learn",
    "media",
    "updates",
    "content",
    "stories",
    "thought-leadership",
}


class ScrapeRequest(BaseModel):
    urls: str               # Comma-separated URLs
    no_discovery: bool = False  # If True, scrape given URLs directly (no Firecrawl map)
    email: str = ""         # Submitter email — used for one-per-person dedup


# ---------------------------------------------------------------------------
# Email dedup — persisted to disk so it survives server restarts
# ---------------------------------------------------------------------------
_EMAILS_FILE = "submitted_emails.json"


def _load_emails() -> set:
    try:
        with open(_EMAILS_FILE) as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def _record_email(email: str) -> None:
    emails = _load_emails()
    emails.add(email.lower().strip())
    with open(_EMAILS_FILE, "w") as f:
        json.dump(list(emails), f)


@app.get("/health")
def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# URL categorisation
# ---------------------------------------------------------------------------

def _is_slug(segment: str) -> bool:
    """
    Heuristic: is this path segment a human-written post slug?
    Post slugs are long OR have 2+ hyphens. Category names are typically
    short with at most one hyphen (e.g. "case-studies", "en-au").
    Examples:
      "blog"                             → not a slug (keyword)
      "case-studies"                     → not a slug (1 hyphen, category)
      "2024"                             → not a slug (year)
      "service-reminder-email"           → slug (2 hyphens)
      "how-does-field-service-work"      → slug (4 hyphens)
      "ultimate-guide-to-job-scheduling" → slug (long)
    """
    return len(segment) > 20 or segment.count("-") >= 2


def _categorize_url(url: str) -> str:
    """
    Returns one of: 'faq', 'help', 'home', 'article_index', 'article_post', 'other'.

    article_index = the listing page  (e.g. /blog, /articles, /resources/guides)
    article_post  = an individual post (e.g. /blog/my-post-title-here) — we skip these
    """
    path = urlparse(url).path.lower().rstrip("/")
    segments = [s for s in path.split("/") if s]

    if not segments:
        return "home"

    # Check FAQ and help first — these take priority over everything
    if any(kw in segments or kw in path for kw in _FAQ_KW):
        return "faq"
    if any(kw in segments for kw in _HELP_KW):
        return "help"

    # Look for a blog/content keyword anywhere in the path segments
    for i, seg in enumerate(segments):
        if seg in _BLOG_KW:
            rest = segments[i + 1:]
            if not rest:
                return "article_index"      # /blog  /articles  /resources
            if len(rest) >= 2:
                return "article_post"       # /resources/webinars/page  — always specific
            # Exactly 1 segment after the keyword: slug check
            if _is_slug(rest[0]):
                return "article_post"       # /blog/service-reminder-email
            return "article_index"          # /blog/category  /resources/webinars

    return "other"


# ---------------------------------------------------------------------------
# URL discovery
# ---------------------------------------------------------------------------

def _base_url(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def _root_domain(netloc: str) -> str:
    """Strip subdomains: helpguide.simprogroup.com → simprogroup.com"""
    parts = netloc.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else netloc


# Subdomain prefixes that suggest a dedicated help/FAQ site
_HELP_SUBDOMAIN_KW = {"help", "helpguide", "support", "docs", "documentation", "kb", "faq", "faqs", "knowledge"}


def _firecrawl_map(url: str, limit: int = 500, include_subdomains: bool = False) -> list[str]:
    """Raw Firecrawl map call. Returns a flat list of URL strings."""
    from firecrawl import FirecrawlApp
    client = FirecrawlApp(api_key=_FIRECRAWL_KEY)
    result = client.map(url, limit=limit, include_subdomains=include_subdomains)
    raw = result.links or []
    return [item.url for item in raw if hasattr(item, "url")] if raw and not isinstance(raw[0], str) else list(raw)


def _map_urls_firecrawl(base_url: str) -> list[str]:
    """
    Map a domain and its subdomains.
    Step 1: Map the main domain (with subdomains=True) to discover linked subdomains.
    Step 2: Any subdomain whose prefix looks like a help/docs/support site gets
            its own dedicated map call so we get its full URL list.
    """
    base_netloc = urlparse(base_url).netloc
    root = _root_domain(base_netloc)

    print(f"  [MAP] Firecrawl mapping {base_url} (including subdomains)...")
    all_urls = _firecrawl_map(base_url, limit=500, include_subdomains=True)
    print(f"  [MAP] {len(all_urls)} URLs from main domain.")

    # Find subdomains that look like dedicated help/docs sites
    seen_netlocs = {base_netloc}
    help_bases: set[str] = set()
    for u in all_urls:
        netloc = urlparse(u).netloc
        if netloc in seen_netlocs or _root_domain(netloc) != root:
            continue
        seen_netlocs.add(netloc)
        prefix = netloc.replace(f".{root}", "").replace(f".{root.split('.')[0]}", "")
        if any(kw in prefix for kw in _HELP_SUBDOMAIN_KW):
            scheme = urlparse(u).scheme
            help_bases.add(f"{scheme}://{netloc}")

    # Re-map each help subdomain to get its full URL set
    for help_base in help_bases:
        print(f"  [MAP] Detected help subdomain — mapping {help_base}...")
        sub_urls: list[str] = []
        try:
            sub_urls = _firecrawl_map(help_base, limit=300, include_subdomains=False)
            print(f"  [MAP] {len(sub_urls)} additional URLs from {help_base}.")
        except Exception as e:
            print(f"  [MAP] Subdomain map failed ({e}).")

        # MadCap Flare sites have JS-only navigation — Firecrawl map misses most pages.
        # Fall back to reading the TOC chunk files directly.
        if len(sub_urls) < 20:
            madcap_urls = _map_urls_madcap_flare(help_base)
            if madcap_urls:
                sub_urls.extend(madcap_urls)

        all_urls.extend(sub_urls)

    print(f"  [MAP] {len(all_urls)} total URLs discovered.")
    return all_urls


def _map_urls_sitemap(base_url: str) -> list[str]:
    """Fallback: parse sitemap.xml to discover URLs."""
    sitemap_candidates: list[str] = []
    try:
        r = _requests.get(f"{base_url}/robots.txt", headers=_HEADERS, timeout=8)
        if r.ok:
            for line in r.text.splitlines():
                if line.lower().startswith("sitemap:"):
                    sitemap_candidates.append(line.split(":", 1)[1].strip())
    except Exception:
        pass
    sitemap_candidates += [f"{base_url}/sitemap.xml", f"{base_url}/sitemap_index.xml"]

    all_pages: list[str] = []
    visited: set[str] = set()

    for sm_url in sitemap_candidates:
        if sm_url in visited:
            continue
        visited.add(sm_url)
        try:
            r = _requests.get(sm_url, headers=_HEADERS, timeout=10)
            if not r.ok:
                continue
            pages, sub_sitemaps = _parse_sitemap_xml(r.content)
            all_pages.extend(pages)
            for sub in sub_sitemaps:
                if any(x in sub.lower() for x in ("image", "video", "news", "media")):
                    continue
                if sub in visited or len(all_pages) >= 2000:
                    continue
                visited.add(sub)
                try:
                    r2 = _requests.get(sub, headers=_HEADERS, timeout=10)
                    if r2.ok:
                        sub_pages, _ = _parse_sitemap_xml(r2.content)
                        all_pages.extend(sub_pages)
                except Exception:
                    continue
            if all_pages:
                break
        except Exception:
            continue

    seen: set[str] = set()
    return [u for u in all_pages if not (u in seen or seen.add(u))]  # type: ignore[func-returns-value]


def _parse_sitemap_xml(content: bytes) -> tuple[list[str], list[str]]:
    pages, indexes = [], []
    try:
        root = ET.fromstring(content)
        ns = (root.tag.split("}")[0] + "}") if root.tag.startswith("{") else ""
        for loc in root.findall(f"{ns}sitemap/{ns}loc"):
            if loc.text:
                indexes.append(loc.text.strip())
        for loc in root.findall(f"{ns}url/{ns}loc"):
            if loc.text:
                pages.append(loc.text.strip())
    except ET.ParseError:
        pass
    return pages, indexes


def _map_urls_madcap_flare(base_url: str) -> list[str]:
    """
    Discover page URLs from a MadCap Flare WebHelp site via its TOC JS chunk files.
    MadCap Flare sites have no sitemap and a JS-only nav, so Firecrawl map misses most pages.
    Discovery path: /Data/HelpSystem.js → /Data/Tocs/*.js → chunk files → all page URLs.
    """
    try:
        r = _requests.get(f"{base_url}/Data/HelpSystem.js", headers=_HEADERS, timeout=8)
        if not r.ok:
            return []
        match = re.search(r'Toc\s*=\s*"([^"]+)"', r.text)
        if not match:
            return []
        toc_path = match.group(1)
    except Exception:
        return []

    try:
        r = _requests.get(f"{base_url}/{toc_path}", headers=_HEADERS, timeout=8)
        if not r.ok:
            return []
        toc_dir = "/".join(toc_path.split("/")[:-1])
        chunks = re.findall(r'"([^"]*Chunk\d+\.js)"', r.text)
    except Exception:
        return []

    all_urls: list[str] = []
    for chunk in chunks:
        try:
            r = _requests.get(f"{base_url}/{toc_dir}/{chunk}", headers=_HEADERS, timeout=10)
            if not r.ok:
                continue
            paths = re.findall(r'"(Content/[^"]+\.htm)"', r.text)
            for path in paths:
                all_urls.append(f"{base_url}/{path}")
        except Exception:
            continue

    if all_urls:
        print(f"  [MADCAP] Found {len(all_urls)} URLs via MadCap Flare TOC.")
    return all_urls


def _probe_faq_paths(base_url: str) -> list[str]:
    """HEAD-probe common FAQ/help paths when neither map nor sitemap helped."""
    paths = [
        "/faq", "/faqs", "/frequently-asked-questions",
        "/help", "/support", "/help-center", "/helpdesk", "/knowledge-base",
    ]
    found: list[str] = []
    for path in paths:
        try:
            r = _requests.head(base_url + path, headers=_HEADERS, timeout=6, allow_redirects=True)
            if r.status_code < 400:
                found.append(base_url + path)
        except Exception:
            continue
    return found


def discover_faq_urls(input_url: str, max_total: int = MAX_URLS_PER_DOMAIN) -> list[str]:
    """
    Given any URL, discover the best pages on that domain to scrape for FAQs.

    Selection priority (up to max_total total):
      1. Dedicated FAQ pages       (up to MAX_FAQ_PAGES)
      2. Dedicated help pages      (up to 1)
      3. Homepage                  (1)
      4. Blog/content INDEX pages  (up to MAX_ARTICLE_INDEXES)
      5. Individual blog/article posts (up to MAX_ARTICLE_POSTS, fill remaining slots)
         Posts are included because they may contain FAQ sections. The LLM returns []
         if a post has no FAQ content, so there is no cost beyond the fetch.

    Discovery: Firecrawl map + sitemap (always both, merged) → common path probing fallback.
    """
    base = _base_url(input_url)
    domain = urlparse(base).netloc

    # Discover all URLs — always check both Firecrawl AND the sitemap.
    # Firecrawl finds pages reachable via <a> links (navigation, internal links).
    # The sitemap is the definitive list of every page the site owner wants indexed
    # and catches pages not linked anywhere in the navigation (e.g. /pricing subpages).
    # Results are merged and deduplicated so we never miss pages from either source.
    all_urls: list[str] = []
    if _FIRECRAWL_KEY:
        try:
            all_urls = _map_urls_firecrawl(base)
        except Exception as e:
            print(f"  [MAP] Firecrawl failed ({e}).")

    sitemap_urls = _map_urls_sitemap(base)
    if sitemap_urls:
        before = len(all_urls)
        seen = set(all_urls)
        for u in sitemap_urls:
            if u not in seen:
                seen.add(u)
                all_urls.append(u)
        print(f"  [SITEMAP] Added {len(all_urls) - before} new URL(s) not found by Firecrawl.")

    if not all_urls:
        print(f"  [MAP] No URLs discovered from Firecrawl or sitemap.")

    # Keep URLs on the same root domain (including subdomains like helpguide.*, docs.*, etc.)
    root = _root_domain(domain)
    all_urls = [u for u in all_urls if _root_domain(urlparse(u).netloc) == root]

    faq_urls: list[str] = []
    help_urls: list[str] = []
    home_urls: list[str] = []
    article_index_urls: list[str] = []
    article_post_urls: list[str] = []
    other_urls: list[str] = []

    for url in all_urls:
        cat = _categorize_url(url)
        if cat == "faq":
            faq_urls.append(url)
        elif cat == "help":
            help_urls.append(url)
        elif cat == "home":
            home_urls.append(url)
        elif cat == "article_index":
            article_index_urls.append(url)
        elif cat == "article_post":
            article_post_urls.append(url)
        else:
            other_urls.append(url)

    # If the explicit input URL is a FAQ/help page, make sure it's included
    input_cat = _categorize_url(input_url)
    if input_cat == "faq" and input_url not in faq_urls:
        faq_urls.insert(0, input_url)
    elif input_cat == "help" and input_url not in help_urls:
        help_urls.insert(0, input_url)

    # Fallback: probe common paths if no FAQ/help pages found at all
    if not faq_urls and not help_urls:
        print(f"  [DISCOVER] No FAQ/help pages found — probing common paths...")
        for u in _probe_faq_paths(base):
            cat = _categorize_url(u)
            (faq_urls if cat == "faq" else help_urls).append(u)

    # Build prioritised selection
    selected: list[str] = []
    selected.extend(faq_urls[:MAX_FAQ_PAGES])

    if len(selected) < max_total:
        selected.extend(help_urls[:1])

    if len(selected) < max_total:
        selected.extend(home_urls[:1] if home_urls else [base + "/"])

    remaining = max_total - len(selected)
    if remaining > 0:
        selected.extend(article_index_urls[:min(remaining, MAX_ARTICLE_INDEXES)])

    # Blog/article posts — LLM returns [] if a post has no FAQ section
    remaining = max_total - len(selected)
    if remaining > 0:
        selected.extend(article_post_urls[:min(remaining, MAX_ARTICLE_POSTS)])

    # Fill ALL remaining slots with "other" pages (service, pricing, feature pages).
    # No separate inner cap — the total max_total ceiling is the only limit.
    # Shortest path first so /pricing comes before /gtm-engineering/pricing.
    remaining = max_total - len(selected)
    if remaining > 0 and other_urls:
        other_sorted = sorted(
            other_urls,
            key=lambda u: len([s for s in urlparse(u).path.split("/") if s])
        )
        selected.extend(other_sorted[:remaining])

    # Deduplicate and cap
    seen: set[str] = set()
    result: list[str] = []
    for u in selected:
        if u not in seen:
            seen.add(u)
            result.append(u)
    return result[:max_total]


# ---------------------------------------------------------------------------
# Page fetching
# ---------------------------------------------------------------------------

def _fetch_page_markdown(url: str) -> tuple[str, str]:
    """
    Fetch a page and return (content, method).

    Two strategies, whichever gives more text wins:
      1. Static HTTP  — plain requests.get(), BeautifulSoup. Gets the raw HTML
                        exactly as the server sent it before any JS runs.
                        Catches CSS-hidden content (e.g. Webflow accordion answers
                        with display:none that Firecrawl's browser strips out).
      2. Firecrawl    — headless browser render, rawHtml parsed by BeautifulSoup.
                        Catches JS-rendered content (React/Vue SPAs, lazy-loaded
                        sections that aren't in the static HTML at all).

    Firecrawl Markdown is intentionally not used — it's a stripped-down version
    of the same render and would never produce more text than rawHtml.
    """
    from bs4 import BeautifulSoup

    def _parse_html(html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg", "header", "footer"]):
            tag.decompose()
        return soup.get_text(separator="\n", strip=True)

    # Strategy 1: static HTTP
    static_text = ""
    try:
        r = _requests.get(url, headers=_HEADERS, timeout=20)
        if r.ok:
            static_text = _parse_html(r.text)
    except Exception:
        pass

    # Strategy 2: Firecrawl (browser-rendered)
    firecrawl_text = ""
    if _FIRECRAWL_KEY:
        try:
            from firecrawl import FirecrawlApp
            client = FirecrawlApp(api_key=_FIRECRAWL_KEY)
            result = client.scrape(url, formats=["rawHtml"])
            raw_html = (result.rawHtml or "") if hasattr(result, "rawHtml") else ""
            if raw_html:
                firecrawl_text = _parse_html(raw_html)
        except Exception as e:
            print(f"  [FIRECRAWL] Scrape failed: {e}.")

    # Use whichever source gave more content
    if not static_text and not firecrawl_text:
        print(f"  [FETCH] Both sources returned empty.")
        return "", ""

    if len(static_text) >= len(firecrawl_text):
        content, method = static_text, "static"
    else:
        content, method = firecrawl_text, "firecrawl"

    if len(content) >= MIN_CONTENT_LENGTH:
        print(f"  [FETCH] Using {method} ({len(content)} chars)")
        return content[:MAX_PAGE_CHARS], method

    print(f"  [FETCH] Content too short ({len(content)} chars).")
    return "", ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _guess_name(url: str) -> str:
    m = re.search(r"(?:https?://)?(?:www\.)?([^/]+)", url)
    if m:
        return m.group(1).split(".")[0].capitalize()
    return url


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok"}


def _run_scrape(job_id: str, urls_raw: str, no_discovery: bool = False) -> None:
    """Background worker — runs in a thread pool, stores result in _jobs.

    no_discovery=True: scrape the submitted URLs directly without Firecrawl map.
    Used by the internal weekly pipeline where URLs are pre-configured per competitor.
    """
    try:
        from extractor import extract_faqs

        input_urls = [
            u.strip() for u in re.split(r"[,\n]+", urls_raw)
            if u.strip().startswith("http")
        ]

        rows: list[list] = []
        pages_checked: list[str] = []
        seen_questions: set[str] = set()

        for input_url in input_urls:
            name = _guess_name(input_url)

            if no_discovery:
                urls_to_scrape = [input_url]
                print(f"\n[job:{job_id[:8]}] [{name}] Direct scrape (no discovery): {input_url}")
            else:
                print(f"\n[job:{job_id[:8]}] [{name}] Discovering FAQ pages from {input_url}...")
                urls_to_scrape = discover_faq_urls(input_url)
                print(f"[job:{job_id[:8]}] [{name}] Selected: {urls_to_scrape}")

            for url in urls_to_scrape:
                pages_checked.append(url)
                print(f"  Fetching: {url}")

                content, method = _fetch_page_markdown(url)
                if not content or len(content) < MIN_CONTENT_LENGTH:
                    print(f"  [SKIP] No usable content ({len(content)} chars).")
                    continue

                print(f"  Content: {len(content)} chars (via {method}). Extracting with LLM...")
                faqs = extract_faqs(content, url, LLM_PROVIDER, LLM_MODEL)
                print(f"  Found {len(faqs)} FAQ(s).")

                for faq in faqs:
                    if faq["question"] not in seen_questions:
                        seen_questions.add(faq["question"])
                        rows.append([name, url, faq["question"], faq["answer"], date.today().isoformat()])

                time.sleep(1)

        if not rows:
            _jobs[job_id] = {
                "status": "done",
                "result": {
                    "found": False,
                    "pages_checked": pages_checked,
                    "message": (
                        "We couldn't find any FAQ content on the pages we checked. "
                        "This usually means the site doesn't have a dedicated FAQ section, "
                        "or the content is loaded dynamically. "
                        "Try submitting the direct URL of the FAQ or Help page (e.g. example.com/faq)."
                    ),
                },
            }
            return

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["Competitor", "Source URL", "Question", "Answer", "Date"])
        writer.writerows(rows)
        csv_b64 = base64.b64encode(buf.getvalue().encode("utf-8")).decode("ascii")

        # Key findings analysis
        print(f"[job:{job_id[:8]}] Running key findings analysis...")
        from extractor import analyze_faqs, findings_to_html
        findings = analyze_faqs(rows, LLM_PROVIDER, LLM_MODEL)
        analysis_html = findings_to_html(findings)

        _jobs[job_id] = {
            "status": "done",
            "result": {
                "found": True,
                "count": len(rows),
                "csv": csv_b64,
                "analysis_html": analysis_html,
                "faqs": [
                    {"competitor": r[0], "source_url": r[1], "question": r[2], "answer": r[3], "date": r[4]}
                    for r in rows
                ],
                "pages_checked": pages_checked,
            },
        }
        print(f"[job:{job_id[:8]}] Done — {len(rows)} FAQ(s).")

    except Exception as e:
        print(f"[job:{job_id[:8]}] ERROR: {e}")
        _jobs[job_id] = {"status": "error", "result": {"error": str(e)}}


@app.post("/scrape")
def scrape(req: ScrapeRequest):
    """
    Start a scrape job. Returns immediately with a job_id.
    Poll GET /result/{job_id} until status is 'done' or 'error'.
    If the email has already submitted before, returns status 'already_submitted'.
    """
    if not req.urls.strip():
        raise HTTPException(status_code=400, detail="No URLs provided")

    # One-per-person gate — only enforced when an email is provided
    if req.email.strip():
        email = req.email.strip().lower()
        if email in _load_emails():
            print(f"[DEDUP] Already submitted: {email}")
            return {"status": "already_submitted", "job_id": None}
        _record_email(email)

    input_urls = [
        u.strip() for u in re.split(r"[,\n]+", req.urls)
        if u.strip().startswith("http")
    ]
    if not input_urls:
        raise HTTPException(status_code=400, detail="No valid URLs found")

    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"status": "processing", "result": None}
    _executor.submit(_run_scrape, job_id, req.urls, req.no_discovery)

    return {"job_id": job_id, "status": "processing"}


@app.get("/result/{job_id}")
def get_result(job_id: str):
    """
    Poll for scrape job result.
    Returns {"status": "processing"} while running.
    Returns {"status": "done", "result": {...}} when complete.
    """
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] == "processing":
        return {"status": "processing"}
    return {"status": job["status"], **job["result"]}
