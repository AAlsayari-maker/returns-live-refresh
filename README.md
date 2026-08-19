# Returns "التذاكر live" auto-refresh

Refreshes the **التذاكر live** tab of the returns tracking Google Sheet every 5 minutes,
running in GitHub Actions (no local PC required).

Each run:
- appends any new **استرجاع قطع** (category 232) tickets created on/after 13/8/2026,
- recomputes every system-owned ("سيستم") column: status, delivery dates, الفرق, المتسبب,
  supplier, service center, the بانتظار-قبول status, the بوليصة-ارجاع signs, the Tag-14 flag,
- fills `id القطعة` from the ticket description **only where the cell is empty**,
- never touches the columns owned by staff (الفولي / المشطي).

It talks only to Metabase (which also proxies the BigQuery chat data) and the Google Sheets
API — so it needs no database of its own and no local machine.

## Configuration (GitHub → Settings → Secrets and variables → Actions)

No secret is ever stored in this repository. The workflow reads four repository **secrets**:

| Secret | What it is |
| --- | --- |
| `METABASE_URL` | Metabase base URL |
| `METABASE_API_KEY` | Metabase API key (`x-api-key`) |
| `SHEETS_CLIENT_EMAIL` | Google service-account email |
| `SHEETS_PRIVATE_KEY` | Google service-account private key |

## Schedule

`.github/workflows/refresh.yml` runs on `cron: "*/5 * * * *"`. You can also trigger a run by
hand from the **Actions** tab → *refresh-returns-live* → **Run workflow**.
