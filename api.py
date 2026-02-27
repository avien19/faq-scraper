"""api.py — FastAPI wrapper for the lead magnet scraper.

Deployed on Coolify. n8n POSTs URLs here, gets back JSON.

POST /scrape
  Body: { "urls": "https://a.com/faq,https://b.com/faq" }
  Returns:
    { "found": true,  "count": N, "csv": "<base64>" }   — FAQs found
    { "found": false, "message": "..." }                 — nothing found
"""

import base64
import io
import re
import time
import csv
from datetime import date
from urllib.parse import urlparse, urljoin

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="FAQ Scraper API")

# Common FAQ page paths to try when a bare domain/homepage is given
FAQ_PATHS = ["/faq", "/faqs", "/frequently-asked-questions", "/help", "/support", "/help-center"]


class ScrapeRequest(BaseModel):
    urls: str  # Comma-separated URLs


@app.get("/health")
def health():
    return {"status": "ok"}


def _is_homepage(url: str) -> bool:
    """Return True if the URL is a bare domain with no meaningful path."""
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    return path == "" or path == "/index.html"


def _find_faq_url(base_url: str, smart_fetch_fn, clean_html_fn, min_length: int):
    """
    Try the given URL first. If it's a homepage with no FAQ content,
    check common FAQ sub-paths and return the first one that yields content.
    Returns (html, method, url_used) or (None, None, base_url).
    """
    from extractor import extract_faqs

    html, method = smart_fetch_fn(base_url, force_browser=False, use_proxy=False)
    if html:
        text = clean_html_fn(html, browser_rendered=(method == "browser"))
        if len(text) >= min_length:
            faqs = extract_faqs(page_text=text, source_url=base_url,
                                provider=None, model=None, raw_html=html, mode="free")
            if faqs:
                return html, method, base_url

    # If the original URL looks like a homepage, probe common FAQ sub-paths
    if _is_homepage(base_url):
        parsed = urlparse(base_url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        for path in FAQ_PATHS:
            candidate = base + path
            html, method = smart_fetch_fn(candidate, force_browser=False, use_proxy=False)
            if not html:
                continue
            text = clean_html_fn(html, browser_rendered=(method == "browser"))
            if len(text) < min_length:
                continue
            faqs = extract_faqs(page_text=text, source_url=candidate,
                                provider=None, model=None, raw_html=html, mode="free")
            if faqs:
                return html, method, candidate
            time.sleep(1)

    return None, None, base_url


@app.post("/scrape")
def scrape(req: ScrapeRequest):
    if not req.urls.strip():
        raise HTTPException(status_code=400, detail="No URLs provided")

    try:
        from scraper import smart_fetch, clean_html, MIN_CONTENT_LENGTH
        from extractor import extract_faqs

        url_list = [u.strip() for u in re.split(r"[,\n]+", req.urls) if u.strip().startswith("http")]
        if not url_list:
            raise HTTPException(status_code=400, detail="No valid URLs found")

        rows = []
        for url in url_list:
            match = re.search(r"(?:https?://)?(?:www\.)?([^/]+)", url)
            name = match.group(1).split(".")[0].capitalize() if match else url

            html, method, used_url = _find_faq_url(url, smart_fetch, clean_html, MIN_CONTENT_LENGTH)
            if not html:
                continue

            text = clean_html(html, browser_rendered=(method == "browser"))
            faqs = extract_faqs(
                page_text=text,
                source_url=used_url,
                provider=None,
                model=None,
                raw_html=html,
                mode="free",
            )

            for faq in faqs:
                rows.append([name, used_url, faq["question"], faq["answer"], date.today().isoformat()])

            time.sleep(2)

        if not rows:
            return {
                "found": False,
                "message": (
                    "We couldn't find any FAQ content on the pages you submitted. "
                    "This usually means the page doesn't have a dedicated FAQ section, "
                    "or the content is loaded dynamically. "
                    "Try submitting the direct URL of the FAQ or Help page (e.g. example.com/faq)."
                ),
            }

        # Build CSV in memory and return as base64
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["Competitor", "Source URL", "Question", "Answer", "Date"])
        writer.writerows(rows)
        csv_bytes = buf.getvalue().encode("utf-8")
        csv_b64 = base64.b64encode(csv_bytes).decode("ascii")

        return {"found": True, "count": len(rows), "csv": csv_b64}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
