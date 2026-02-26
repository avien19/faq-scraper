"""scraper_public.py — Lead magnet entry point.

Usage:
    python scraper_public.py --urls "https://example.com/faq,https://other.com/faq" --output /tmp/faqs.csv

Called by n8n Execute Command node. Outputs a CSV file, no Google Sheets, no LLM.
"""

import argparse
import csv
import re
import sys
import time
from datetime import date

from dotenv import load_dotenv

from scraper import smart_fetch, clean_html, MIN_CONTENT_LENGTH
from extractor import extract_faqs

load_dotenv()

REQUEST_DELAY = 2


def parse_urls(raw):
    """Accept comma or newline separated URLs, return clean list."""
    urls = re.split(r"[,\n]+", raw)
    return [u.strip() for u in urls if u.strip().startswith("http")]


def guess_competitor_name(url):
    """Extract a readable name from a URL (e.g. 'aroflo.com' → 'AroFlo')."""
    match = re.search(r"(?:https?://)?(?:www\.)?([^/]+)", url)
    if match:
        domain = match.group(1)
        # Strip TLD and capitalise
        name = domain.split(".")[0]
        return name.capitalize()
    return url


def main():
    parser = argparse.ArgumentParser(description="Lead magnet FAQ scraper")
    parser.add_argument(
        "--urls",
        required=True,
        help="Comma-separated list of URLs to scrape",
    )
    parser.add_argument(
        "--output",
        default="/tmp/faqs.csv",
        help="Path to write the output CSV (default: /tmp/faqs.csv)",
    )
    args = parser.parse_args()

    urls = parse_urls(args.urls)
    if not urls:
        print("[ERROR] No valid URLs provided.")
        sys.exit(1)

    print(f"Lead magnet scraper — {len(urls)} URL(s) to process\n")

    rows = []

    for url in urls:
        name = guess_competitor_name(url)
        print(f"[{name}] Fetching: {url}")

        html, method = smart_fetch(url, force_browser=False, use_proxy=False)
        if not html:
            print(f"  [SKIP] Could not fetch content.")
            time.sleep(REQUEST_DELAY)
            continue

        text = clean_html(html, browser_rendered=(method == "browser"))
        if len(text) < MIN_CONTENT_LENGTH:
            print(f"  [WARN] Content too short ({len(text)} chars), skipping.")
            time.sleep(REQUEST_DELAY)
            continue

        print(f"  Cleaned text: {len(text)} chars (via {method}). Extracting FAQs...")
        faqs = extract_faqs(
            page_text=text,
            source_url=url,
            provider=None,
            model=None,
            raw_html=html,
            mode="free",
        )
        print(f"  Found {len(faqs)} FAQ(s).")

        for faq in faqs:
            rows.append([
                name,
                url,
                faq["question"],
                faq["answer"],
                date.today().isoformat(),
            ])

        time.sleep(REQUEST_DELAY)

    if not rows:
        print("\n[WARN] No FAQs found across all URLs.")
        # Still write an empty CSV so n8n doesn't error on missing file
        rows = [["No FAQs found. Try URLs that have a dedicated FAQ page."]]
        write_csv(args.output, rows, headers=False)
        sys.exit(0)

    write_csv(args.output, rows)
    print(f"\nDone. {len(rows)} FAQ(s) written to {args.output}")


def write_csv(path, rows, headers=True):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if headers:
            writer.writerow(["Competitor", "Source URL", "Question", "Answer", "Date"])
        writer.writerows(rows)


if __name__ == "__main__":
    main()
