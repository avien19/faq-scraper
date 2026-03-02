from urllib.parse import urlparse

import gspread


def _get_client():
    """Authenticate via OAuth and return a gspread client.
    First run opens a browser for authorization. Token is cached in authorized_user.json.
    """
    return gspread.oauth(
        credentials_filename="credentials.json",
        authorized_user_filename="authorized_user.json",
    )


def get_existing_faqs(spreadsheet_name, worksheet_name):
    """Read all existing FAQ rows from the Google Sheet.
    Returns a list of dicts with keys matching column headers.
    """
    client = _get_client()
    sheet = client.open(spreadsheet_name).worksheet(worksheet_name)
    records = sheet.get_all_records()
    return records


def get_competitor_urls(spreadsheet_name, worksheet_name):
    """Read competitor URLs from the source Google Sheet.

    Expects columns (by position, headers ignored):
      A: Competitor homepage URL  (e.g. https://www.cloudcon.com/)
      B: FAQ page URL             (optional)
      C: Blog page URL            (optional)

    Returns a list of dicts:
      [{"name": "CloudCon", "homepage": "https://...", "faq_urls": [...], "blog_urls": [...]}]
    """
    client = _get_client()
    sheet = client.open(spreadsheet_name).worksheet(worksheet_name)
    rows = sheet.get_all_values()

    competitors = []
    for row in rows[1:]:  # skip header row
        # Pad row to at least 3 columns
        while len(row) < 3:
            row.append("")

        def _fix_url(u):
            u = u.strip()
            if u and not u.startswith("http"):
                u = "https://" + u
            return u

        homepage = _fix_url(row[0])
        faq_url  = _fix_url(row[1])
        blog_url = _fix_url(row[2])

        if not homepage:
            continue

        # Derive a display name from the domain (e.g. "cloudcon.com" → "CloudCon")
        try:
            netloc = urlparse(homepage).netloc
            domain = netloc[4:] if netloc.startswith("www.") else netloc
        except Exception:
            domain = homepage
        name = domain.split(".")[0].title()

        competitors.append({
            "name":      name,
            "homepage":  homepage,
            "faq_urls":  [faq_url]  if faq_url  else [],
            "blog_urls": [blog_url] if blog_url else [],
        })

    return competitors


def append_faqs(spreadsheet_name, worksheet_name, new_rows):
    """Append new FAQ rows to the Google Sheet.
    Each row is a list: [Competitor, Source URL, Question, Answer, Date First Seen]
    Returns the number of rows added.
    """
    if not new_rows:
        return 0
    client = _get_client()
    sheet = client.open(spreadsheet_name).worksheet(worksheet_name)
    sheet.append_rows(new_rows, value_input_option="USER_ENTERED")
    return len(new_rows)
