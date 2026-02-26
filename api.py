"""api.py — FastAPI wrapper for the lead magnet scraper.

Deployed on Coolify. n8n POSTs URLs here, gets back a CSV file.

POST /scrape
  Body: { "urls": "https://a.com/faq,https://b.com/faq" }
  Returns: CSV file (text/csv)
"""

import os
import tempfile

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="FAQ Scraper API")


class ScrapeRequest(BaseModel):
    urls: str  # Comma-separated URLs


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/scrape")
def scrape(req: ScrapeRequest):
    if not req.urls.strip():
        raise HTTPException(status_code=400, detail="No URLs provided")

    # Write CSV to a temp file
    tmp = tempfile.NamedTemporaryFile(
        suffix=".csv", delete=False, dir="/tmp", prefix="faqs_"
    )
    tmp.close()
    output_path = tmp.name

    try:
        # Import here to keep startup fast
        import re
        import time
        import csv
        from datetime import date
        from scraper import smart_fetch, clean_html, MIN_CONTENT_LENGTH
        from extractor import extract_faqs

        url_list = [u.strip() for u in re.split(r"[,\n]+", req.urls) if u.strip().startswith("http")]
        if not url_list:
            raise HTTPException(status_code=400, detail="No valid URLs found")

        rows = []
        for url in url_list:
            # Guess competitor name from domain
            match = re.search(r"(?:https?://)?(?:www\.)?([^/]+)", url)
            name = match.group(1).split(".")[0].capitalize() if match else url

            html, method = smart_fetch(url, force_browser=False, use_proxy=False)
            if not html:
                continue

            text = clean_html(html, browser_rendered=(method == "browser"))
            if len(text) < MIN_CONTENT_LENGTH:
                continue

            faqs = extract_faqs(
                page_text=text,
                source_url=url,
                provider=None,
                model=None,
                raw_html=html,
                mode="free",
            )

            for faq in faqs:
                rows.append([name, url, faq["question"], faq["answer"], date.today().isoformat()])

            time.sleep(2)

        # Write CSV
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Competitor", "Source URL", "Question", "Answer", "Date"])
            if rows:
                writer.writerows(rows)
            else:
                writer.writerow(["No FAQs found", "", "Try URLs with a dedicated FAQ page", "", ""])

        return FileResponse(
            path=output_path,
            media_type="text/csv",
            filename="competitor-faqs.csv",
            background=_cleanup(output_path),
        )

    except HTTPException:
        raise
    except Exception as e:
        os.unlink(output_path)
        raise HTTPException(status_code=500, detail=str(e))


class _cleanup:
    """Delete temp file after response is sent."""
    def __init__(self, path):
        self.path = path

    async def __call__(self):
        try:
            os.unlink(self.path)
        except OSError:
            pass
