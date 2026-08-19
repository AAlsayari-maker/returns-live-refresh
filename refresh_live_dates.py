"""التذاكر live — recompute the date columns, honoring the manual 'id القطعة' column.

'id القطعة' may hold EITHER a pricing-request id (OrderPricingRequests.id) OR a single
item id (OrderPricingRequestItems.id). The two are told apart automatically: for a given
value, only one interpretation belongs to the ticket's own order.

Rule for 'تاريخ تسليم آخر قطعة للمركز':
  * id القطعة is a single item id -> use THAT exact item's DELIVERED_TO_CENTER date.
  * id القطعة is a PR id          -> use the latest DELIVERED_TO_CENTER among THAT PR's items.
    (date chosen = latest event on/before the ticket's creation; else latest event overall.)
    No guessing across the order's other pricing requests.
  * id القطعة empty               -> across-order fallback: latest DELIVERED_TO_CENTER of any
    of the order's items on/before the ticket, then PR updatedAt/createdAt <= ticket.
  * id القطعة set but matches neither a PR nor an item of the ticket's order (a typo) ->
    warn and use the across-order fallback.

'الفرق بين التاريخين' = (create date) - (deliver date) in days.
Columns are resolved BY HEADER NAME (row 2), so this survives column reordering.
Only the three date columns are written; every manual column is left untouched.
"""
import sys, os, datetime, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # portable: local PC or cloud runner
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
if sys.stdout is None:            # pythonw.exe (scheduled runs) has no console → guard print()
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
    sys.stderr = sys.stdout
from agent.envload import load_env
from agent.sheets_client import SheetsClient
from agent.metabase_client import MetabaseClient

SID = "1Yctw8-P1S0H1QeVzIGtQg88k2wIjZRc_Gcdt0PgkbmI"
TAB = "التذاكر live"
H_TICKET = "رقم التذكرة"
H_ITEM   = "id القطعة"
H_CREATE = "تاريخ إنشاء التذكرة"
H_DELIV  = "تاريخ تسليم آخر قطعة للمركز"
H_DIFF   = "الفرق بين التاريخين"
H_CAUSE  = "المتسبب"          # optional; derived from الفرق (>7 = فريق التشغيل, else فريق قطع الغيار)
H_ACCEPT_MADE = 'انشاء تذكرة "بانتظار قبول استرجاع القطع" وعمل منشن للمشرف'   # manual checkbox (N)
H_ACCEPT_STAT = 'هل تذكرة "بانتظار قبول استرجاع القطع" اغلقت؟'               # optional (O)
ACCEPT_CATEGORY = 322        # TicketCategory 'بانتظار قبول استرجاع القطع'
H_TAG14  = "tag (ننتظر العميل يجيب السيارة)"   # optional (AA): نعم if order has Tag 14, else لا يوجد
TAG_WAIT_CAR = 14            # Tag 'ننتظر العميل يجيب السيارة'
H_SUPPLIER = "مورد قطعة الغيار"   # optional (AC): supplier(s) of the id القطعة part(s)
H_CENTER   = "مركز الورشة"        # optional (AD): the order's assigned service center
                                  # = latest OrderAssignationSupplier.supplier.name for the order
H_ERRCLASS = "تصنيف خطأ"          # optional (G): default value where empty; preserve manual entries
ERRCLASS_DEFAULT = "لا تحتاج تعديل"
H_WAYBILL  = "هل تم عمل بوليصة الارجاع؟"   # optional (T): تم if a بوليصة-ارجاع mention is in the order chats
POLICY_PY  = r'بوليص[ةه]\s*(است|[اأإآ])رجاع'          # BigQuery regex
POLICY_MY  = r'بوليص[ةه][[:space:]]*(است|[اأإآ])رجاع'  # MySQL regex
STATUS = {1: 'جديدة', 2: 'قيد المعالجة', 3: 'بإنتظار الإغلاق', 4: 'بإنتظار العميل',
          5: 'تمت المعالجة', 6: 'ملغية', 7: 'مغلقة'}
# base ("سيستم") columns also refreshed every run + used to append new tickets
H_URL    = "رابط الطلب"
H_UID    = "رقم الطلب الكامل"
H_CITY   = "مدينة الطلب"
H_STATUS = "الحالة"
RETURN_CATEGORY = 232               # TicketCategory 'استرجاع قطع' (the live-tab population)
POP_CUTOFF = "2026-08-13 00:00:00"  # Riyadh; live tab = tickets created on/after 13/8/2026
WAYBILL_YES, WAYBILL_NO = "✅", "❌"   # T shown as colored signs, easy to scan by eye
mention_re = re.compile(r'@\[[^\]]*\]\([^)]*\)')
def clean(t): return re.sub(r'\s+', ' ', mention_re.sub('', str(t or ''))).strip(' "')

LOGFILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "refresh_live_dates.log")
def log(msg):
    line = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    print(line)
    try:
        with open(LOGFILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
import traceback
def _excepthook(et, ev, tb):
    log("ERROR: " + "".join(traceback.format_exception(et, ev, tb)).strip())
    sys.__excepthook__(et, ev, tb)
sys.excepthook = _excepthook

# --- Arabic part-name matcher: derive id القطعة from the ticket description (empty cells only) ---
_STOP = set("""نحتاج استرجاع ارجاع من المركز للمورد الرجاء القطع القطعه يرجي الي الى في علي تسليم
    المستبدل الواصل خطاء خطا اليوم مباشره لعدم توجه العميل كامل كامله نرجو نرجع نرجعها المورد
    الورشه بديل وصلوه وتسليمه بلغت يوصل لكم اللي صحيح للمقاس للاستبدال استبدال لمره ثانيه المشرف
    افاد الميداني نحتاجه التاجر وبوصل وصل قطع قطعه هذي هذا انه ان مع تم عمل""".split())
def _norm(s):
    s = re.sub(r"[ًٌٍَُِّْـ]", "", str(s or ""))
    return (s.replace("أ","ا").replace("إ","ا").replace("آ","ا").replace("ى","ي").replace("ة","ه"))
def _toks(s):
    out = set()
    for t in re.split(r"[\s,+/\-()]+", _norm(s)):
        if t.startswith("ال") and len(t) > 4:
            t = t[2:]
        if len(t) >= 3 and t not in _STOP:
            out.add(t)
    return out
def match_items(desc, items):
    """items = [(opi_id, itemName)]. Return [opi_id] for the SINGLE best unique match, else []."""
    dt = _toks(desc)
    if not dt:
        return []
    best, score = [], 0
    for iid, name in items:
        sc_ = len(dt & _toks(name))
        if sc_ > score:
            best, score = [iid], sc_
        elif sc_ == score and sc_ > 0:
            best.append(iid)
    return best if (score > 0 and len(best) == 1) else []   # conservative: unique match only

def dstr(v): return str(v)[:10] if v else ""
def pdate(s): return datetime.date(int(s[0:4]), int(s[5:7]), int(s[8:10])) if s else None
def col_letter(i):
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s

env = load_env()
sc = SheetsClient(env.get("SHEETS_CLIENT_EMAIL"), env.get("SHEETS_PRIVATE_KEY"))
mb = MetabaseClient(env.get("METABASE_URL"), env.get("METABASE_API_KEY"))

# ---- resolve columns by header
grid = sc.read(SID, f"{TAB}!A2:AZ400", unformatted=False)
header = grid[0]
col = {name: header.index(name) for name in (H_TICKET, H_ITEM, H_CREATE, H_DELIV, H_DIFF)}
for opt in (H_CAUSE, H_ACCEPT_MADE, H_ACCEPT_STAT, H_TAG14, H_SUPPLIER, H_CENTER,
            H_ERRCLASS, H_WAYBILL, H_URL, H_UID, H_CITY, H_STATUS):   # optional columns
    if opt in header:
        col[opt] = header.index(opt)
H_DESC = next((h for h in header if h.startswith("وصف المشكلة")), None)   # header has a backslash
if H_DESC:
    col[H_DESC] = header.index(H_DESC)
print("columns:", {k: col_letter(v) for k, v in col.items()})

# ---- rows: ticket id + manual id القطعة (may be a dash/comma/space-separated LIST of PR or item ids)
rows_info = []            # [sheet_row, ticket_id, [ids]]
accept_made = {}          # sheet_row -> bool (column N checkbox)
cur_errclass = {}         # sheet_row -> current تصنيف خطأ value (to preserve manual entries)
cur_iditem = {}           # sheet_row -> original id القطعة cell text (to preserve manual entries)
for off, r in enumerate(grid[1:]):        # grid[1:] starts at sheet row 3
    tv = str(r[col[H_TICKET]]).strip() if len(r) > col[H_TICKET] else ""
    if not tv:
        continue
    iv = str(r[col[H_ITEM]]).strip() if len(r) > col[H_ITEM] else ""
    cur_iditem[3 + off] = iv
    ids = [int(tok) for tok in re.split(r"[-,\s/]+", iv) if tok.isdigit()]
    rows_info.append([3 + off, int(tv), ids])
    if H_ACCEPT_MADE in col:
        nv = str(r[col[H_ACCEPT_MADE]]).strip().upper() if len(r) > col[H_ACCEPT_MADE] else ""
        accept_made[3 + off] = (nv == "TRUE")
    if H_ERRCLASS in col:
        cur_errclass[3 + off] = str(r[col[H_ERRCLASS]]).strip() if len(r) > col[H_ERRCLASS] else ""

# ---- population: append NEW category-232 tickets (created on/after the cutoff) not already present
existing_ids = {t for _, t, _ in rows_info}
_, r = mb.native_query(f"""SELECT id FROM mismar_production.Ticket
    WHERE categoryId={RETURN_CATEGORY} AND deletedAt IS NULL
      AND CONVERT_TZ(createdAt,'+00:00','+03:00') >= '{POP_CUTOFF}'""", 2)
active_ids = {int(x[0]) for x in r}
new_ids = sorted(active_ids - existing_ids)
new_rows = set()
next_row = max((row for row, _, _ in rows_info), default=2) + 1
for tid in new_ids:
    rows_info.append([next_row, tid, []])
    cur_iditem[next_row] = ""
    accept_made[next_row] = False
    cur_errclass[next_row] = ""
    new_rows.add(next_row)
    next_row += 1

if not rows_info:
    log("no ticket rows and no new tickets — nothing to do")
    sys.exit(0)

tickets = [t for _, t, _ in rows_info]
idlist = ",".join(map(str, tickets))
print(f"{len(rows_info)} ticket rows ({len(new_ids)} new); "
      f"{sum(1 for _,_,ids in rows_info if ids)} have id القطعة")

# ---- ticket base info: create date, raw createdAt, orderId, description, uniqueId, status, city
_, r = mb.native_query(f"""SELECT t.id, DATE(CONVERT_TZ(t.createdAt,'+00:00','+03:00')),
    t.createdAt, t.orderId, t.description, t.orderUniqueId, t.status, c.name
    FROM mismar_production.Ticket t
    LEFT JOIN `mismar_production`.`Order` o ON o.id = t.orderId
    LEFT JOIN mismar_production.City c ON c.id = o.cityId
    WHERE t.id IN ({idlist})""", 2)
create   = {int(x[0]): dstr(x[1]) for x in r}
raw_crt  = {int(x[0]): str(x[2]) for x in r}
tdesc    = {int(x[0]): (x[4] or "") for x in r}
order_of = {int(x[0]): x[3] for x in r}
tuid     = {int(x[0]): (x[5] or "") for x in r}
tstatus  = {int(x[0]): (int(x[6]) if x[6] is not None else None) for x in r}
tcity    = {int(x[0]): (x[7] or "") for x in r}

# ---- id القطعة auto-fill: for EMPTY cells only, derive from the description by matching the
#      part name to the order's items (conservative: unique single match). Manual cells untouched.
derived_ids = {}          # sheet_row -> [opi_id]  (only rows we auto-filled)
empty_rows = [(row, tid) for row, tid, ids in rows_info if not ids]
if empty_rows:
    eorders = sorted({order_of[t] for _, t in empty_rows if order_of.get(t)})
    order_items = {}
    if eorders:
        ol = ",".join(str(o) for o in eorders)
        _, r = mb.native_query(f"""SELECT r.orderId, i.id, i.itemName
            FROM mismar_production.OrderPricingRequestItems i
            JOIN mismar_production.OrderPricingRequests r ON r.id=i.pricingRequestId
            WHERE r.orderId IN ({ol}) AND i.deletedAt IS NULL""", 2)
        for x in r:
            order_items.setdefault(x[0], []).append((int(x[1]), x[2] or ""))
    for entry in rows_info:                     # entry = [row, tid, ids]
        row, tid, ids = entry
        if ids:
            continue
        got = match_items(tdesc.get(tid, ""), order_items.get(order_of.get(tid), []))
        if got:
            entry[2] = list(got)                # feed downstream (delivery / supplier) this run
            derived_ids[row] = list(got)
    print(f"id القطعة auto-derived for {len(derived_ids)} of {len(empty_rows)} empty rows")

# ---- across-order DELIVERED_TO_CENTER (on/before ticket) + PR fallbacks
_, r = mb.native_query(f"""SELECT t.id,
    DATE(CONVERT_TZ(MAX(CASE WHEN spt.createdAt<=t.createdAt THEN spt.createdAt END),'+00:00','+03:00'))
    FROM mismar_production.Ticket t
    JOIN mismar_production.OrderPricingRequests r ON r.orderId=t.orderId AND r.deletedAt IS NULL
    JOIN mismar_production.OrderPricingRequestItems i ON i.pricingRequestId=r.id AND i.deletedAt IS NULL
    JOIN mismar_production.SparePartsTracking spt ON spt.itemType='OrderPricingRequestItems'
      AND CAST(spt.itemId AS UNSIGNED)=i.id AND spt.subAction LIKE '%To_DELIVERED_TO_CENTER' AND spt.deletedAt IS NULL
    WHERE t.id IN ({idlist}) GROUP BY t.id, t.createdAt""", 2)
center = {int(x[0]): dstr(x[1]) for x in r}
_, r = mb.native_query(f"""SELECT t.id,
    DATE(CONVERT_TZ(MAX(CASE WHEN r.updatedAt<=t.createdAt THEN r.updatedAt END),'+00:00','+03:00')) u,
    DATE(CONVERT_TZ(MAX(CASE WHEN r.createdAt<=t.createdAt THEN r.createdAt END),'+00:00','+03:00')) c
    FROM mismar_production.Ticket t
    JOIN mismar_production.OrderPricingRequests r ON r.orderId=t.orderId AND r.deletedAt IS NULL
    WHERE t.id IN ({idlist}) GROUP BY t.id, t.createdAt""", 2)
pr_u = {int(x[0]): dstr(x[1]) for x in r}
pr_c = {int(x[0]): dstr(x[2]) for x in r}

# ---- resolve each filled 'id القطعة' to the target item ids (direct case)
vals = sorted({v for _, _, ids in rows_info for v in ids})
pr_order, opi_order, items_of_pr = {}, {}, {}
if vals:
    vl = ",".join(map(str, vals))
    _, r = mb.native_query(f"SELECT id, orderId FROM mismar_production.OrderPricingRequests WHERE id IN ({vl})", 2)
    pr_order = {int(x[0]): x[1] for x in r}
    _, r = mb.native_query(f"""SELECT i.id, r.orderId FROM mismar_production.OrderPricingRequestItems i
        JOIN mismar_production.OrderPricingRequests r ON r.id=i.pricingRequestId WHERE i.id IN ({vl})""", 2)
    opi_order = {int(x[0]): x[1] for x in r}
    _, r = mb.native_query(f"""SELECT pricingRequestId, id FROM mismar_production.OrderPricingRequestItems
        WHERE pricingRequestId IN ({vl}) AND deletedAt IS NULL""", 2)
    for x in r:
        items_of_pr.setdefault(int(x[0]), []).append(int(x[1]))

def target_items(val, torder):
    """One id -> (list_of_opi_ids, kind). kind in {'item','pr','mismatch'}."""
    if opi_order.get(val) == torder:
        return [val], "item"
    if pr_order.get(val) == torder:
        return items_of_pr.get(val, []), "pr"
    return [], "mismatch"

def resolve_row(ids, torder):
    """A list of id القطعة values -> (union_opis, kinds_set, bad_ids). Order-preserving, deduped."""
    opis, kinds, bad = [], set(), []
    for v in ids:
        oo, k = target_items(v, torder)
        if k == "mismatch":
            bad.append(v)
        else:
            kinds.add(k)
            for o in oo:
                if o not in opis:
                    opis.append(o)
    return opis, kinds, bad

# ---- DELIVERED_TO_CENTER events for every opi we might need directly
universe = set()
for _, tid, ids in rows_info:
    if ids:
        opis, _k, _b = resolve_row(ids, order_of.get(tid))
        universe.update(opis)
item_events = {}   # opi_id -> list[(raw_createdAt, date_str)] ascending
if universe:
    ul = ",".join(map(str, sorted(universe)))
    _, r = mb.native_query(f"""SELECT CAST(spt.itemId AS UNSIGNED) iid, spt.createdAt,
        DATE(CONVERT_TZ(spt.createdAt,'+00:00','+03:00')) d
        FROM mismar_production.SparePartsTracking spt
        WHERE spt.itemType='OrderPricingRequestItems' AND spt.deletedAt IS NULL
          AND spt.subAction LIKE '%To_DELIVERED_TO_CENTER'
          AND CAST(spt.itemId AS UNSIGNED) IN ({ul})
        ORDER BY spt.createdAt""", 2)
    for x in r:
        item_events.setdefault(int(x[0]), []).append((str(x[1]), dstr(x[2])))

# ---- supplier name per target item (AC مورد قطعة الغيار)
item_supplier = {}   # opi_id -> supplier name
if H_SUPPLIER in col and universe:
    ul = ",".join(map(str, sorted(universe)))
    _, r = mb.native_query(f"""SELECT i.id, s.name FROM mismar_production.OrderPricingRequestItems i
        LEFT JOIN mismar_production.Supplier s ON s.id=i.supplierId
        WHERE i.id IN ({ul})""", 2)
    item_supplier = {int(x[0]): (x[1] or "") for x in r}

def suppliers_for(opis):
    out = []
    for o in opis:
        nm = item_supplier.get(o, "")
        if nm and nm not in out:
            out.append(nm)
    return " / ".join(out)

def direct_deliver(opis, tid):
    """Latest DELIVERED_TO_CENTER among the named items: on/before ticket, else latest any. '' if none."""
    evs = [e for oi in opis for e in item_events.get(oi, [])]
    if not evs:
        return ""
    evs.sort()
    tcrt = raw_crt.get(tid, "")
    le = [e for e in evs if e[0] <= tcrt] if tcrt else []
    return (le[-1][1] if le else evs[-1][1])

# ---- build the three columns in sheet-row order
first_row = rows_info[0][0]
last_row  = rows_info[-1][0]
n = last_row - first_row + 1
C = [[""] for _ in range(n)]   # create
D = [[""] for _ in range(n)]   # deliver
F = [[""] for _ in range(n)]   # diff
CA = [[""] for _ in range(n)]  # المتسبب (derived from diff)
AC = [[""] for _ in range(n)]  # O: status of the بانتظار قبول استرجاع القطع ticket (when N checked)
TG = [[""] for _ in range(n)]  # AA: نعم/لا يوجد for Tag 14 (ننتظر العميل يجيب السيارة)
SU = [[""] for _ in range(n)]  # AC: مورد قطعة الغيار (supplier of the id القطعة part[s])
CT = [[""] for _ in range(n)]  # AD: مركز الورشة (order's assigned service center)
EC = [[""] for _ in range(n)]  # G: تصنيف خطأ (default لا تحتاج تعديل, preserve manual)
WB = [[""] for _ in range(n)]  # T: بوليصة الارجاع (✅ / ❌)
STA  = [[""] for _ in range(n)]  # الحالة (status) — refreshed for ALL rows every run
TKc  = [[""] for _ in range(n)]  # base cols, written for NEW rows only:
URLc = [[""] for _ in range(n)]  #   رقم التذكرة / رابط الطلب / رقم الطلب الكامل /
UIDc = [[""] for _ in range(n)]  #   مدينة الطلب / وصف المشكلة
CTYc = [[""] for _ in range(n)]
DSCc = [[""] for _ in range(n)]

# ---- AA: which orders carry Tag 14
tag14_orders = set()
if H_TAG14 in col:
    allo = sorted({order_of[t] for _, t, _ in rows_info if order_of.get(t)})
    if allo:
        ol = ",".join(str(o) for o in allo)
        _, r = mb.native_query(f"""SELECT DISTINCT orderId FROM mismar_production.OrderTag
            WHERE tagId={TAG_WAIT_CAR} AND deletedAt IS NULL AND orderId IN ({ol})""", 2)
        tag14_orders = {x[0] for x in r}

# ---- O column: latest category-322 ticket status per order (only used where N is checked)
accept_status = {}   # orderId -> status label of latest بانتظار قبول استرجاع القطع ticket
if H_ACCEPT_STAT in col and H_ACCEPT_MADE in col:
    orders322 = sorted({order_of[t] for _, t, _ in rows_info if order_of.get(t)})
    if orders322:
        ol = ",".join(str(o) for o in orders322)
        _, r = mb.native_query(f"""SELECT x.orderId, x.status FROM mismar_production.Ticket x
            JOIN (SELECT orderId, MAX(createdAt) mx FROM mismar_production.Ticket
                  WHERE categoryId={ACCEPT_CATEGORY} AND deletedAt IS NULL AND orderId IN ({ol})
                  GROUP BY orderId) m ON x.orderId=m.orderId AND x.createdAt=m.mx
            WHERE x.categoryId={ACCEPT_CATEGORY} AND x.deletedAt IS NULL""", 2)
        accept_status = {x[0]: STATUS.get(int(x[1]), str(x[1])) for x in r}

# ---- AD مركز الورشة: latest OrderAssignationSupplier center name per order
workshop_center = {}   # orderId -> center (supplier) name
if H_CENTER in col:
    allo = sorted({order_of[t] for _, t, _ in rows_info if order_of.get(t)})
    if allo:
        ol = ",".join(str(o) for o in allo)
        _, r = mb.native_query(f"""SELECT x.orderId, JSON_UNQUOTE(JSON_EXTRACT(x.supplier,'$.name'))
            FROM mismar_production.OrderAssignationSupplier x
            JOIN (SELECT orderId, MAX(createdAt) mx FROM mismar_production.OrderAssignationSupplier
                  WHERE deletedAt IS NULL AND orderId IN ({ol}) GROUP BY orderId) m
              ON x.orderId=m.orderId AND x.createdAt=m.mx
            WHERE x.deletedAt IS NULL""", 2)
        workshop_center = {x[0]: (x[1] or "") for x in r}

# ---- T: orders whose CHAT mentions a بوليصة ارجاع (MySQL OrderComments + BigQuery order_messages)
waybill_orders = set()
if H_WAYBILL in col:
    allo = sorted({order_of[t] for _, t, _ in rows_info if order_of.get(t)})
    if allo:
        ol = ",".join(str(o) for o in allo)
        _, r = mb.native_query(f"""SELECT DISTINCT orderId FROM mismar_production.OrderComments
            WHERE deletedAt IS NULL AND orderId IN ({ol}) AND comment REGEXP '{POLICY_MY}'""", 2)
        waybill_orders |= {x[0] for x in r}
        quoted = ",".join(f"'{o}'" for o in allo)
        _, r = mb.native_query(f"""SELECT DISTINCT
            CAST(REGEXP_EXTRACT(order_id, r'order_(\\d+)') AS INT64) oid
            FROM `order_chat_sync`.`order_messages`
            WHERE REGEXP_CONTAINS(JSON_VALUE(data,'$.content'), r'{POLICY_PY}')
              AND REGEXP_EXTRACT(order_id, r'order_(\\d+)') IN ({quoted})""", 6)
        waybill_orders |= {x[0] for x in r}

def across_order(tid):
    if center.get(tid): return center[tid]
    if pr_u.get(tid):   return pr_u[tid]
    if pr_c.get(tid):   return pr_c[tid]
    return ""

stats = {"direct": 0, "direct_blank": 0, "across": 0, "empty_blank": 0, "bad_ids": 0}
for row, tid, ids in rows_info:
    idx = row - first_row
    cr = create.get(tid, "")
    if ids:
        opis, kinds, bad = resolve_row(ids, order_of.get(tid))
        if bad:
            print(f"  ⚠ row {row} ticket {tid}: id القطعة {bad} — neither a PR nor an item of the "
                  f"ticket's order ({order_of.get(tid)})")
            stats["bad_ids"] += 1
        if opis:
            dl = direct_deliver(opis, tid)
            stats["direct" if dl else "direct_blank"] += 1
            SU[idx] = [suppliers_for(opis)]   # AC مورد قطعة الغيار (only for named part[s]/PR[s])
        else:                                 # every id was bad -> across-order fallback
            dl = across_order(tid)
    else:
        dl = across_order(tid)
        stats["across" if dl else "empty_blank"] += 1
    C[idx] = [cr]
    D[idx] = [dl]
    crd, dld = pdate(cr), pdate(dl)
    diff = (crd - dld).days if (crd and dld) else ""
    F[idx] = [diff]
    CA[idx] = ["" if diff == "" else ("فريق التشغيل" if diff > 7 else "فريق قطع الغيار")]
    # O: only when the N checkbox is checked, show the بانتظار قبول ticket's status
    # (if N is checked but no such ticket exists for the order -> لا يوجد تذكرة)
    if accept_made.get(row):
        AC[idx] = [accept_status.get(order_of.get(tid), "لا يوجد تذكرة")]
    if H_TAG14 in col:
        TG[idx] = ["نعم" if order_of.get(tid) in tag14_orders else "لا يوجد"]
    if H_CENTER in col:
        CT[idx] = [workshop_center.get(order_of.get(tid), "")]
    if H_ERRCLASS in col:      # default where empty; keep manual entries
        EC[idx] = [cur_errclass.get(row) or ERRCLASS_DEFAULT]
    if H_WAYBILL in col:
        WB[idx] = [WAYBILL_YES if order_of.get(tid) in waybill_orders else WAYBILL_NO]
    # الحالة — refreshed for every row (status can change between runs)
    st = tstatus.get(tid)
    STA[idx] = [STATUS.get(st, "") if st is not None else ""]
    # base columns — only filled for newly-appended rows
    if row in new_rows:
        TKc[idx]  = [tid]
        URLc[idx] = [f"https://admin.mismarapp.com/home/orders/{order_of.get(tid)}" if order_of.get(tid) else ""]
        UIDc[idx] = [tuid.get(tid, "")]
        CTYc[idx] = [tcity.get(tid, "")]
        DSCc[idx] = [clean(tdesc.get(tid, ""))]

n_checked = sum(1 for v in accept_made.values() if v)
print("deliver-date sources:", stats)
if H_ACCEPT_STAT in col:
    print(f"O column: {n_checked} rows have N checked; "
          f"{sum(1 for x in AC if x[0])} got a بانتظار-قبول status")

# ---- write the columns
def put(cidx, vals):
    L = col_letter(cidx)
    sc.write(SID, f"{TAB}!{L}{first_row}:{L}{last_row}", vals, raw=True)
def put_range(cidx, vals, r0, r1):
    L = col_letter(cidx)
    sc.write(SID, f"{TAB}!{L}{r0}:{L}{r1}", vals, raw=True)
put(col[H_CREATE], C)
put(col[H_DELIV],  D)
put(col[H_DIFF],   F)
if H_CAUSE in col:
    put(col[H_CAUSE], CA)
if H_ACCEPT_STAT in col:
    put(col[H_ACCEPT_STAT], AC)
if H_TAG14 in col:
    put(col[H_TAG14], TG)
if H_SUPPLIER in col:
    put(col[H_SUPPLIER], SU)
if H_CENTER in col:
    put(col[H_CENTER], CT)
if H_ERRCLASS in col:
    put(col[H_ERRCLASS], EC)
if H_WAYBILL in col:
    put(col[H_WAYBILL], WB)
if H_STATUS in col:                 # الحالة refreshed for all rows
    put(col[H_STATUS], STA)
# base columns for NEW rows only (contiguous block at the bottom)
if new_rows:
    nf, nl = min(new_rows), max(new_rows)
    s0 = nf - first_row
    put_range(col[H_TICKET], TKc[s0:], nf, nl)
    if H_URL in col:  put_range(col[H_URL],  URLc[s0:], nf, nl)
    if H_UID in col:  put_range(col[H_UID],  UIDc[s0:], nf, nl)
    if H_CITY in col: put_range(col[H_CITY], CTYc[s0:], nf, nl)
    if H_DESC:        put_range(col[H_DESC], DSCc[s0:], nf, nl)
# id القطعة: write ONLY the cells we auto-derived (empty ones); never touch manual cells
if derived_ids:
    IL = col_letter(col[H_ITEM])
    for row, got in derived_ids.items():
        sc.write(SID, f"{TAB}!{IL}{row}:{IL}{row}", [["-".join(map(str, got))]], raw=True)

# ---- keep formats: create/deliver as date, diff as centered integer
gid = sc.sheet_ids(SID)[TAB]
def fmt(c, cell, fields):
    return {"repeatCell": {"range": {"sheetId": gid, "startRowIndex": first_row-1, "endRowIndex": last_row,
            "startColumnIndex": c, "endColumnIndex": c+1}, "cell": cell, "fields": fields}}
sc.batch_update(SID, [
    fmt(col[H_CREATE], {"userEnteredFormat":{"numberFormat":{"type":"DATE","pattern":"yyyy-mm-dd"}}}, "userEnteredFormat.numberFormat"),
    fmt(col[H_DELIV],  {"userEnteredFormat":{"numberFormat":{"type":"DATE","pattern":"yyyy-mm-dd"}}}, "userEnteredFormat.numberFormat"),
    fmt(col[H_DIFF],   {"userEnteredFormat":{"numberFormat":{"type":"NUMBER","pattern":"0"},"horizontalAlignment":"CENTER"}}, "userEnteredFormat(numberFormat,horizontalAlignment)"),
])
log(f"done: {len(rows_info)} rows (rows {first_row}..{last_row}); "
    f"{len(new_ids)} new tickets appended; {len(derived_ids)} id القطعة auto-derived; "
    f"waybill ✅={sum(1 for x in WB if x[0]==WAYBILL_YES)}")
