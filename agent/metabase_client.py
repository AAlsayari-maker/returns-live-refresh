"""Minimal Metabase REST client: raw SQL via /api/dataset, with transient-5xx retry."""
import time
import requests


class MetabaseClient:
    def __init__(self, base_url, api_key):
        if not base_url or not api_key:
            raise ValueError("Missing METABASE_URL or METABASE_API_KEY in .env.local")
        self.base = base_url.rstrip("/")
        self.key = api_key

    def _headers(self):
        return {"x-api-key": self.key, "Content-Type": "application/json"}

    def native_query(self, sql, database_id, timeout=300, retries=3):
        """Run raw SQL -> (col_names, rows). Retries 5xx/timeouts (the server hiccups)."""
        url = f"{self.base}/api/dataset"
        body = {"database": database_id, "type": "native", "native": {"query": sql}}
        last = None
        for attempt in range(retries):
            try:
                resp = requests.post(url, json=body, headers=self._headers(), timeout=timeout)
                if resp.status_code >= 500:
                    last = requests.HTTPError(f"{resp.status_code} {resp.reason}")
                    time.sleep(1.5 * (attempt + 1))
                    continue
                resp.raise_for_status()
                payload = resp.json()
                if payload.get("status") == "failed" or payload.get("error"):
                    raise RuntimeError(f"Metabase query failed: {payload.get('error') or payload.get('status')}")
                data = payload.get("data", {})
                return [c.get("name") for c in data.get("cols", [])], data.get("rows", [])
            except (requests.ConnectionError, requests.Timeout) as e:
                last = e
                time.sleep(1.5 * (attempt + 1))
        raise last or RuntimeError("Metabase query failed after retries")

    def query_card(self, card_id, timeout=300):
        """Run a saved Metabase question -> (headers, rows)."""
        r = requests.post(f"{self.base}/api/card/{card_id}/query", json={},
                          headers=self._headers(), timeout=timeout)
        r.raise_for_status()
        d = r.json().get("data", {})
        return [c.get("display_name") or c.get("name") for c in d.get("cols", [])], d.get("rows", [])
