import gspread
from google.oauth2.service_account import Credentials


def _get_client():
    """Authenticate via Service Account and return a gspread client.
    Requires credentials.json (service account key file) in the project root.
    No browser or user interaction needed — works on any server.
    """
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.readonly",
    ]
    creds = Credentials.from_service_account_file("credentials.json", scopes=scopes)
    return gspread.authorize(creds)


def get_existing_faqs(spreadsheet_name, worksheet_name):
    """Read all existing FAQ rows from the Google Sheet.
    Returns a list of dicts with keys matching column headers.
    """
    client = _get_client()
    sheet = client.open(spreadsheet_name).worksheet(worksheet_name)
    records = sheet.get_all_records()
    return records


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
