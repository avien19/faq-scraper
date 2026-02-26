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
