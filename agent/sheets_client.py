"""Google Sheets read/write using the service account from .env.local."""
from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


class SheetsClient:
    def __init__(self, client_email, private_key):
        private_key = (private_key or "").replace("\\n", "\n")   # keys are stored one-line
        if not client_email or not private_key:
            raise ValueError("Missing SHEETS_CLIENT_EMAIL or SHEETS_PRIVATE_KEY in .env.local")
        info = {"type": "service_account", "client_email": client_email,
                "private_key": private_key, "token_uri": "https://oauth2.googleapis.com/token"}
        creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
        self.svc = build("sheets", "v4", credentials=creds, cache_discovery=False)

    def read(self, sid, a1, unformatted=True):
        vro = "UNFORMATTED_VALUE" if unformatted else "FORMATTED_VALUE"
        return (self.svc.spreadsheets().values()
                .get(spreadsheetId=sid, range=a1, valueRenderOption=vro,
                     dateTimeRenderOption="FORMATTED_STRING").execute()).get("values", [])

    def write(self, sid, a1, values, raw=True):
        return (self.svc.spreadsheets().values()
                .update(spreadsheetId=sid, range=a1,
                        valueInputOption=("RAW" if raw else "USER_ENTERED"),
                        body={"values": values}).execute())

    def append(self, sid, a1, values, raw=True):
        return (self.svc.spreadsheets().values()
                .append(spreadsheetId=sid, range=a1,
                        valueInputOption=("RAW" if raw else "USER_ENTERED"),
                        insertDataOption="INSERT_ROWS", body={"values": values}).execute())

    def clear(self, sid, a1):
        return self.svc.spreadsheets().values().clear(spreadsheetId=sid, range=a1, body={}).execute()

    def meta(self, sid):
        return self.svc.spreadsheets().get(spreadsheetId=sid).execute()

    def tab_titles(self, sid):
        return [s["properties"]["title"] for s in self.meta(sid).get("sheets", [])]

    def sheet_ids(self, sid):
        """{tab title: sheetId(gid)} - needed for formatting/protection requests."""
        return {s["properties"]["title"]: s["properties"]["sheetId"]
                for s in self.meta(sid).get("sheets", [])}

    def ensure_tab(self, sid, title):
        if title in self.tab_titles(sid):
            return False
        self.batch_update(sid, [{"addSheet": {"properties": {"title": title}}}])
        return True

    def batch_update(self, sid, requests):
        return self.svc.spreadsheets().batchUpdate(spreadsheetId=sid, body={"requests": requests}).execute()
