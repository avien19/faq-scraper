import json
import os
import re
import sys
import time
from datetime import date
from urllib.parse import urlparse as _urlparse

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from extractor import extract_faqs
from sheets import get_existing_faqs, append_faqs, get_competitor_urls

load_dotenv()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# Minimum chars of cleaned text to consider a page as having real content
MIN_CONTENT_LENGTH = 100


def _build_proxies(use_proxy):
    """Return a requests-compatible proxies dict, or None if not using proxy."""
    if not use_proxy:
        return None
    proxy_url = os.environ.get("WEBSHARE_PROXY_URL")
    if not proxy_url:
        print("  [PROXY] WARNING: use_proxy=True but WEBSHARE_PROXY_URL is not set in .env")
        return None
    return {"http": proxy_url, "https": proxy_url}


def fetch_page(url, proxies=None):
    """Fetch a page's HTML via HTTP. Returns (html, used_browser) tuple."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30, proxies=proxies)
        resp.raise_for_status()
        return resp.text, False
    except requests.RequestException as e:
        print(f"  [HTTP] Failed: {e}")
        return None, False


def fetch_page_browser(url, proxy_url=None):
    """Fetch a page using headless Chromium via Playwright.
    Dismisses cookie banners, expands FAQ elements, returns full HTML.
    """
    from playwright.sync_api import sync_playwright

    print(f"  [BROWSER] Rendering with headless browser...")
    try:
        with sync_playwright() as p:
            launch_kwargs = {"headless": True}
            if proxy_url:
                launch_kwargs["proxy"] = {"server": proxy_url}
                print(f"  [BROWSER] Using proxy.")
            browser = p.chromium.launch(**launch_kwargs)
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)

            _dismiss_cookie_banner(page)
            _expand_faq_elements(page)
            page.wait_for_timeout(1000)

            # Force all hidden elements visible so get_text() picks them up
            page.evaluate("""() => {
                document.querySelectorAll(
                    '[style*="display: none"], [style*="display:none"], ' +
                    '[style*="height: 0"], [style*="height:0"], ' +
                    '.w-dropdown-list, [class*="dropdown-list"], ' +
                    '[class*="accordion-content"], [class*="faq-content"], ' +
                    '[aria-hidden="true"]'
                ).forEach(el => {
                    el.style.setProperty('display', 'block', 'important');
                    el.style.setProperty('height', 'auto', 'important');
                    el.style.setProperty('overflow', 'visible', 'important');
                    el.style.setProperty('opacity', '1', 'important');
                    el.style.setProperty('visibility', 'visible', 'important');
                });
            }""")

            html = page.content()
            browser.close()
            return html
    except Exception as e:
        print(f"  [BROWSER] Failed: {e}")
        return None


def _dismiss_cookie_banner(page):
    """Try to dismiss common cookie consent banners."""
    selectors = [
        'button:has-text("Accept")',
        'button:has-text("Accept All")',
        'button:has-text("Allow")',
        'button:has-text("Got it")',
        'button:has-text("OK")',
        '[class*="cookie"] button',
        '#onetrust-accept-btn-handler',
    ]
    for selector in selectors:
        try:
            btn = page.query_selector(selector)
            if btn and btn.is_visible():
                btn.click()
                page.wait_for_timeout(500)
                print(f"  [BROWSER] Dismissed cookie banner.")
                return
        except Exception:
            continue


def _expand_faq_elements(page):
    """Click on common FAQ accordion/toggle elements to reveal answers."""
    selectors = [
        # Webflow dropdown pattern (AroFlo uses this)
        '.w-dropdown-toggle',
        # Common FAQ accordion patterns
        '[class*="faq"] [class*="toggle"]',
        '[class*="faq"] [class*="question"]',
        '[class*="accordion"] button',
        '[class*="accordion"] [class*="header"]',
        '[class*="accordion"] [class*="trigger"]',
        'details:not([open]) summary',
        '[data-toggle="collapse"]',
        # Generic expandable elements
        '[class*="expandable"]',
        '[class*="collapsible"] [class*="header"]',
    ]
    total_clicked = 0
    for selector in selectors:
        try:
            elements = page.query_selector_all(selector)
            for el in elements:
                try:
                    if el.is_visible():
                        el.click(timeout=2000)
                        total_clicked += 1
                        page.wait_for_timeout(300)
                except Exception:
                    continue
        except Exception:
            continue

    if total_clicked > 0:
        print(f"  [BROWSER] Expanded {total_clicked} FAQ element(s).")


def smart_fetch(url, force_browser=False, use_proxy=False):
    """Try HTTP first, fall back to browser if it fails or content is too thin."""
    proxies = _build_proxies(use_proxy)
    proxy_url = proxies["https"] if proxies else None

    if force_browser:
        print(f"  [BROWSER] Force browser mode enabled.")
        html = fetch_page_browser(url, proxy_url=proxy_url)
        if html:
            return html, "browser"
        return None, None

    # Try simple HTTP first
    html, _ = fetch_page(url, proxies=proxies)
    if html:
        text = clean_html(html, browser_rendered=False)
        if len(text) >= MIN_CONTENT_LENGTH:
            return html, "http"

        # Content too short - likely JS-rendered, try browser
        print(f"  [HTTP] Content too short ({len(text)} chars), trying browser...")
    else:
        # HTTP failed (403, timeout, etc.) - try browser
        print(f"  [HTTP] Retrying with browser...")

    # Fall back to headless browser
    html = fetch_page_browser(url, proxy_url=proxy_url)
    if html:
        return html, "browser"

    return None, None


def clean_html(raw_html, browser_rendered=False):
    """Strip non-content tags and extract readable text from HTML."""
    soup = BeautifulSoup(raw_html, "html.parser")
    # Strip non-content tags. Note: <nav> is intentionally NOT stripped even for
    # HTTP-fetched pages — some sites (e.g. Aroflo/Webflow) put FAQ answers inside
    # <nav class="w-dropdown-list"> which is hidden by CSS but present in the DOM.
    # The LLM handles any navigation noise cleanly.
    strip_tags = ["script", "style", "noscript", "svg"]
    if not browser_rendered:
        strip_tags += ["footer", "header"]
    for tag in soup(strip_tags):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    lines = [line for line in text.splitlines() if line.strip()]
    return "\n".join(lines)


def make_dedup_key(competitor, question):
    """Create a normalized key for deduplication."""
    q = re.sub(r"[^\w\s]", "", question.lower())
    q = re.sub(r"\s+", " ", q).strip()
    return f"{competitor.lower()}::{q}"


def main():
    dry_run = "--dry-run" in sys.argv

    with open("config.json", "r") as f:
        config = json.load(f)

    provider = config["llm"]["provider"]
    model = config["llm"]["model"]
    sheet_name = config["google_sheet"]["spreadsheet_name"]
    worksheet_name = config["google_sheet"]["worksheet_name"]
    delay = config.get("request_delay_seconds", 2)
    domain_settings = config.get("domain_settings", {})

    # Load existing FAQs for dedup
    if not dry_run:
        existing_rows = get_existing_faqs(sheet_name, worksheet_name)
    else:
        existing_rows = []
        print("[DRY RUN] Skipping Google Sheets read.\n")

    existing_keys = set()
    for row in existing_rows:
        # Strip whitespace from keys to handle inconsistent sheet headers
        cleaned = {k.strip(): v for k, v in row.items()}
        key = make_dedup_key(cleaned.get("Competitor", ""), cleaned.get("Question", ""))
        existing_keys.add(key)

    print(f"Loaded {len(existing_keys)} existing FAQs from sheet.\n")

    # Load competitors from the live source sheet
    source_cfg = config.get("source_sheet", {})
    if source_cfg.get("spreadsheet_name"):
        print(f"Loading competitors from '{source_cfg['spreadsheet_name']}'...")
        competitors = get_competitor_urls(
            source_cfg["spreadsheet_name"],
            source_cfg.get("worksheet_name", "Sheet1"),
        )
        print(f"Found {len(competitors)} competitor(s) in source sheet.\n")
    else:
        competitors = config.get("competitors", [])

    all_new_rows = []

    for competitor in competitors:
        name = competitor["name"]
        faq_urls  = competitor.get("faq_urls", [])
        blog_urls = competitor.get("blog_urls", [])
        homepage  = competitor.get("homepage", "")

        # If no FAQ/blog URLs, fall back to the homepage itself
        urls = faq_urls + blog_urls
        if not urls and homepage:
            urls = [homepage]

        if not urls:
            print(f"[{name}] No URLs configured, skipping.")
            continue

        print(f"[{name}] Processing {len(urls)} URL(s)...")

        # Apply per-competitor flags first, then domain_settings overrides
        force_browser = competitor.get("force_browser", False)
        use_proxy     = competitor.get("use_proxy", False)

        # Check domain_settings for any URL belonging to this competitor
        for url in urls:
            try:
                domain = _urlparse(url).netloc.lstrip("www.")
            except Exception:
                domain = ""
            ds = domain_settings.get(domain, {})
            if ds.get("force_browser"):
                force_browser = True
            if ds.get("use_proxy"):
                use_proxy = True

        for url in urls:
            print(f"  Fetching: {url}")
            html, method = smart_fetch(url, force_browser=force_browser, use_proxy=use_proxy)
            if not html:
                print(f"  [SKIP] Could not fetch content.")
                continue

            text = clean_html(html, browser_rendered=(method == "browser"))
            if len(text) < MIN_CONTENT_LENGTH:
                print(f"  [WARN] Page content too short ({len(text)} chars), skipping.")
                continue

            print(f"  Cleaned text: {len(text)} chars (via {method}). Extracting FAQs...")
            faqs = extract_faqs(text, url, provider, model)
            print(f"  Found {len(faqs)} FAQ(s).")

            for faq in faqs:
                key = make_dedup_key(name, faq["question"])
                if key in existing_keys:
                    continue
                existing_keys.add(key)
                all_new_rows.append([
                    name,
                    url,
                    faq["question"],
                    faq["answer"],
                    date.today().isoformat(),
                ])

            time.sleep(delay)

    print(f"\n{'=' * 50}")
    print(f"New FAQs to add: {len(all_new_rows)}")

    if all_new_rows and not dry_run:
        added = append_faqs(sheet_name, worksheet_name, all_new_rows)
        print(f"Appended {added} rows to Google Sheet.")
    elif dry_run and all_new_rows:
        print("\n[DRY RUN] Would add these FAQs:\n")
        for row in all_new_rows:
            print(f"  [{row[0]}] Q: {row[2]}")
            print(f"           A: {row[3][:100]}...")
            print()
    else:
        print("No new FAQs found.")


if __name__ == "__main__":
    main()
