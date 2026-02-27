"""scraper_public.py — Lead magnet CLI entry point.

Usage:
    python scraper_public.py --urls "https://example.com" --output /tmp/faqs.csv

Called by n8n Execute Command node. Outputs a CSV file.
Pipeline: Firecrawl map → select up to 5 pages → LLM extraction → CSV.
"""

import argparse
import csv
import json
import re
import sys
import time
from datetime import date

from dotenv import load_dotenv

load_dotenv()

with open("config.json") as _f:
    _cfg = json.load(_f)
LLM_PROVIDER = _cfg["llm"]["provider"]
LLM_MODEL = _cfg["llm"]["model"]

REQUEST_DELAY = 1


def parse_urls(raw: str) -> list[str]:
    return [u.strip() for u in re.split(r"[,\n]+", raw) if u.strip().startswith("http")]


def main():
    parser = argparse.ArgumentParser(description="Lead magnet FAQ scraper")
    parser.add_argument("--urls", required=True)
    parser.add_argument("--output", default="/tmp/faqs.csv")
    args = parser.parse_args()

    input_urls = parse_urls(args.urls)
    if not input_urls:
        print("[ERROR] No valid URLs provided.")
        sys.exit(1)

    from api import discover_faq_urls, _guess_name, _fetch_page_markdown, MIN_CONTENT_LENGTH
    from extractor import extract_faqs

    print(f"Lead magnet scraper — {len(input_urls)} input URL(s)\n")
    rows: list[list] = []

    for input_url in input_urls:
        name = _guess_name(input_url)
        print(f"[{name}] Discovering FAQ pages from {input_url}...")

        urls_to_scrape = discover_faq_urls(input_url)
        print(f"[{name}] Selected {len(urls_to_scrape)} page(s): {urls_to_scrape}\n")

        for url in urls_to_scrape:
            print(f"  Fetching: {url}")
            content, method = _fetch_page_markdown(url)
            if not content or len(content) < MIN_CONTENT_LENGTH:
                print(f"  [SKIP] No usable content.")
                time.sleep(REQUEST_DELAY)
                continue

            print(f"  Content: {len(content)} chars (via {method}). Extracting with LLM...")
            faqs = extract_faqs(content, url, LLM_PROVIDER, LLM_MODEL)
            print(f"  Found {len(faqs)} FAQ(s).")

            for faq in faqs:
                rows.append([name, url, faq["question"], faq["answer"], date.today().isoformat()])

            time.sleep(REQUEST_DELAY)

    if not rows:
        print("\n[WARN] No FAQs found across all pages checked.")
        rows = [["No FAQs found. Try submitting the direct URL of the FAQ or Help page."]]
        _write_csv(args.output, rows, headers=False)
        sys.exit(0)

    _write_csv(args.output, rows)
    print(f"\nDone. {len(rows)} FAQ(s) written to {args.output}")


def _write_csv(path: str, rows: list, headers: bool = True) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if headers:
            writer.writerow(["Competitor", "Source URL", "Question", "Answer", "Date"])
        writer.writerows(rows)


if __name__ == "__main__":
    main()
