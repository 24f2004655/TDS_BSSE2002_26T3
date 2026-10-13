import re, json, urllib.request, urllib.parse
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

EMAIL = "24f2004655@ds.study.iitm.ac.in"
QS = "lhHcrB9z5cux6rmPAOgA8Ub/Mr3zhZ9I6RfseJIsR5Occ7GnnScQH+e1/ZoN4gJFC2YMgYd1wrMXVXU9E9Pj9ifa1o7XJGI1vQnGYWx7QUDUw05rM3gx6OjJ2QK9nIjcQyBy55ySoqOTZsVnWMHsHisMxLLIuX0+ewp9IKHJySUuuvlHz3vQx56auhqD5N1JuNqpGjwI6aR+TNJJGTIeexSmp89zwm7xx23ghMUKjLzX8E1hUSCLqK87Q7O/M3DbzecYxr/BLTxFqJgWDY9mU8ALt1uvHDIpaM4TihmycQGDjuSDeens9fPmpjb1dtUdn0QqaCob8LndDNxMoqQSTw=="
BASE = "https://exam.sanand.workers.dev/questionData"
KOL = ZoneInfo("Asia/Kolkata")
RATES = {"USD": 1, "EUR": 1.07, "INR": 0.01195}
ORDERS = []
PRODUCTS = ["Air Fryer", "Blender", "Grinder", "Juicer", "Kettle", "Mixer", "Rice Cooker", "Toaster"]
REGIONS = ["North", "South", "East", "West", "Central"]
MONTHS = {"january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
          "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
          "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12}

BASE_QS = "email=24f2004655%40ds.study.iitm.ac.in&quizSign=lhHcrB9z5cux6rmPAOgA8Ub%2FMr3zhZ9I6RfseJIsR5Occ7GnnScQH%2Be1%2FZoN4gJFC2YMgYd1wrMXVXU9E9Pj9ifa1o7XJGI1vQnGYWx7QUDUw05rM3gx6OjJ2QK9nIjcQyBy55ySoqOTZsVnWMHsHisMxLLIuX0%2Bewp9IKHJySUuuvlHz3vQx56auhqD5N1JuNqpGjwI6aR%2BTNJJGTIeexSmp89zwm7xx23ghMUKjLzX8E1hUSCLqK87Q7O%2FM3DbzecYxr%2FBLTxFqJgWDY9mU8ALt1uvHDIpaM4TihmycQGDjuSDeens9fPmpjb1dtUdn0QqaCob8LndDNxMoqQSTw%3D%3D&questionId=q-ledger-agent-server"

UA = {"User-Agent": "Mozilla/5.0"}

def get(url, timeout=60):
    req = urllib.request.Request(url, headers=UA)
    return urllib.request.urlopen(req, timeout=timeout).read()

def qurl(path, page=None):
    u = BASE + "?" + BASE_QS + "&path=" + urllib.parse.quote(path, safe="")
    if page is not None:
        u += f"&page={page}"
    return u

def load():
    global RATES, ORDERS
    try:
        rates = json.loads(get(qurl("/rates"), timeout=30))
        RATES = rates.get("usd_per_unit", RATES)
    except Exception:
        pass
    rows = []
    try:
        raw = get(qurl("/export"), timeout=60).decode()
        for line in raw.splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    except Exception:
        # fallback paginated
        p = 1
        while True:
            d = json.loads(get(qurl("/orders", p), timeout=30))
            rows.extend(d.get("orders", []))
            nxt = d.get("next")
            if not nxt or p >= d.get("pages", p):
                break
            p += 1
    # dedup latest updated_at per id
    best = {}
    for r in rows:
        i = r["id"]
        if i not in best or r["updated_at"] > best[i]["updated_at"]:
            best[i] = r
    for r in best.values():
        ca = datetime.fromisoformat(r["created_at"].replace("Z", "+00:00")).astimezone(KOL)
        r["_ym"] = (ca.year, ca.month)
        r["_usd"] = round(r["amount"] * RATES.get(r["currency"], 1), 6)
    ORDERS = list(best.values())
    print(f"loaded {len(ORDERS)} orders")

load()

def parse(q):
    t = q.lower()
    region = next((r for r in REGIONS if r.lower() in t), None)
    product = next((p for p in PRODUCTS if p.lower() in t), None)
    m = re.search(r"(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+(\d{4})", t)
    if not m:
        m = re.search(r"(\d{4})-(\d{1,2})", t)
        ym = (int(m.group(1)), int(m.group(2))) if m else None
    else:
        ym = (int(m.group(2)), MONTHS[m.group(1)[:3] if len(m.group(1)) > 3 else m.group(1)])
        # fix: MONTHS keys are full+abbr; normalize
        mon = m.group(1).lower()
        mon = {"sept": "sep"}.get(mon, mon)
        key = mon if mon in MONTHS else mon[:3]
        ym = (int(m.group(2)), MONTHS[key])
    cust = (re.search(r"\b(c\d{3,})\b", t).group(1).upper() if re.search(r"\b(c\d{3,})\b", t) else None)
    is_refund = "refund" in t
    is_void = "void" in t or "cancel" in t
    is_count = bool(re.search(r"how many|number of|count", t))
    is_avg = bool(re.search(r"average|avg|mean", t))
    is_top_product = bool(re.search(r"top|best|most|highest|largest.*product|product.*most", t))
    is_top_customer = bool(re.search(r"customer.*most|most.*customer|top customer|best customer", t))
    return {"region": region, "product": product, "ym": ym, "cust": cust,
            "refund": is_refund, "void": is_void, "count": is_count,
            "avg": is_avg, "top_product": is_top_product, "top_customer": is_top_customer}

def filt(f):
    out = ORDERS
    if f["region"]:
        out = [r for r in out if r["region"] == f["region"]]
    if f["product"]:
        out = [r for r in out if r["product"] == f["product"]]
    if f["ym"]:
        out = [r for r in out if r["_ym"] == f["ym"]]
    if f["cust"]:
        out = [r for r in out if r["customer"] == f["cust"]]
    if f["refund"]:
        out = [r for r in out if r["status"] == "refunded"]
    elif f["void"]:
        out = [r for r in out if r["status"] == "void"]
    elif f["top_product"] or f["top_customer"] or f["count"] and "revenue" not in str(f):
        pass  # status decided below
    else:
        # money questions default to paid
        pass
    return out

def answer(q):
    f = parse(q)
    t = q.lower()
    rows = filt(f)
    # top product / top customer
    if f["top_product"]:
        pool = [r for r in rows if r["status"] == "paid"]
        sums = {}
        for r in pool:
            sums[r["product"]] = sums.get(r["product"], 0) + r["_usd"]
        return max(sums, key=sums.get) if sums else None
    if f["top_customer"]:
        pool = [r for r in rows if r["status"] == "paid"]
        sums = {}
        for r in pool:
            sums[r["customer"]] = sums.get(r["customer"], 0) + r["_usd"]
        return max(sums, key=sums.get) if sums else None
    # status scope
    if f["refund"]:
        pool = rows  # already refunded
    elif f["void"]:
        pool = rows
    elif f["count"] and not any(w in t for w in ["revenue", "sales", "refund", "usd", "$"]):
        pool = rows  # plain count = all current rows matching filters
    else:
        pool = [r for r in rows if r["status"] == "paid"]
    if f["count"]:
        return len(pool)
    if f["avg"]:
        return round(sum(r["_usd"] for r in pool) / len(pool), 2) if pool else 0
    return round(sum(r["_usd"] for r in pool), 2)

async def handle(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    q = body.get("question", "") if isinstance(body, dict) else ""
    try:
        return JSONResponse({"answer": answer(q)})
    except Exception as e:
        return JSONResponse({"answer": None, "error": str(e)})

app.post("/answer")(handle)
app.post("/")(handle)
app.post("/{p:path}")(handle)

@app.get("/")
def root():
    return {"ok": True, "orders": len(ORDERS)}
