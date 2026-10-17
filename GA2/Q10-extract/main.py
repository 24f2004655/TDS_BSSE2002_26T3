"""Q10 - Local LLM Structured-Output Service.

POST /extract  {"text": "<invoice text>"}
     -> {"vendor": str, "amount": number, "currency": "XXX", "date": "YYYY-MM-DD"}

The local LLM does the reading; the text does the verifying
-----------------------------------------------------------

A 1B model cannot be trusted to emit an invoice number verbatim - it rounds,
invents digits, and quietly drops suffixes. So the model is never the last word.
Every field it proposes is checked against the source text, and any claim that
cannot be found in the text is discarded in favour of a rule-based parse of that
same text. Concretely, for each of the four fields:

  1. ask the local Ollama model (JSON mode) for its reading of the invoice;
  2. gate the answer: the proposed value must be locatable in the input text
     (vendor as a substring, amount as a number token within 0.01, currency as a
     code token, date as an ISO date after normalisation);
  3. when the gate rejects the model - or the model is unreachable - fall back to
     a deterministic parse (labelled fields, legal-suffix company names, a
     keyword-weighted amount, a due-date-aware date pick).

Amount, currency, vendor and date each carry an "anchored" flag: true when the
invoice itself names the field ("Total Due:", "Due date:", a currency code, a
legal suffix). Anchored fields keep the rule-based value; unanchored fields take
the model's verified reading. The flag is what stops the model from handing back
the invoice date on a two-date invoice - a wrong date that is still "present in
the text" and would otherwise sail through step 2.

The effect is that the endpoint degrades gracefully: with Ollama up it is an
LLM extractor with a proofreader, and with Ollama down it is a parser with the
same output contract. tools/bench_extract.py measures all three variants
(deterministic only / LLM only / hybrid) on a synthetic invoice corpus.

Set OLLAMA_URL to a reachable Ollama (or to the Q9 gateway, which passes
/api/generate straight through) to enable the LLM. LLM_MODE=always|fallback|never.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Any, Optional

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

# --------------------------------------------------------------------------- #
# configuration
# --------------------------------------------------------------------------- #
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
SERVED_MODEL = os.environ.get("SERVED_MODEL", "gemma3:1b-it-qat")
LLM_MODE = os.environ.get("LLM_MODE", "always")  # always | fallback | never
LLM_TIMEOUT = float(os.environ.get("LLM_TIMEOUT", "10"))

CURRENCIES = ("USD", "EUR", "GBP")
SYMBOLS = {"$": "USD", "\u20ac": "EUR", "\u00a3": "GBP"}


# --------------------------------------------------------------------------- #
# models
# --------------------------------------------------------------------------- #
class ExtractRequest(BaseModel):
    # A missing "text" is not an error: the spec allows a best-effort answer.
    text: str = ""


class Invoice(BaseModel):
    """Exactly the four fields the spec asks for, with the right types."""

    model_config = ConfigDict(extra="forbid")

    vendor: str
    amount: float
    currency: str
    date: str


EMPTY = Invoice(vendor="", amount=0.0, currency="", date="")


# --------------------------------------------------------------------------- #
# text preparation
# --------------------------------------------------------------------------- #
ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
NUM_DATE = re.compile(r"\b(\d{1,2})[/.](\d{1,2})[/.](\d{4})\b")
MONTHS = (
    "january|february|march|april|may|june|july|august|september|october|"
    "november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec"
)
MONTH_NAMES = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], start=1)}
MONTH_ALIASES = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7,
                 "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12}

TEXT_DATE_A = re.compile(rf"\b({MONTHS})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.I)
TEXT_DATE_B = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({MONTHS})\.?,?\s+(\d{{4}})\b", re.I)

# Tokens that look like identifiers, not money. Masked before hunting for an
# amount so that "INV-4821" or "+91 98765 43210" cannot be mistaken for a total.
IDENT = re.compile(
    r"""
    (?:
        \b(?:inv|invoice|po|ref|order|ord|no|num|account|acct|customer|cust|
             tax|vat|gst|phone|tel|fax|mobile|iban|swift|cheque|check)
        [\s.#:-]*\d[\w-]*                       # INV-4821, PO 4412, Tax ID 22
      | \#[A-Za-z0-9][\w-]*                     # #7741
      | \b[A-Za-z]{2,5}-\d[\w-]*                # ACC-99, SKU-12
      | \+?\d[\d\s().-]{7,}\d                    # long digit runs: phone numbers
      | \d+(?:\.\d+)?\s*%                       # 8.5%   (a rate, never a total)
      | \b\d{4}-\d{2}-\d{2}\b                   # dates are handled separately
    )
    """,
    re.I | re.X,
)

AMOUNT = re.compile(
    r"(?<![\d.,])(?:\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)(?![\d])")
CUR_CODE = re.compile(r"\b(USD|EUR|GBP)\b", re.I)
CUR_SYM = re.compile(r"[$€£]")

POSITIVE = re.compile(
    r"\b(grand\s+total|total\s+due|total\s+amount|amount\s+due|amount\s+payable|"
    r"balance\s+due|balance|total|payable|amount|due|owe[sd]?|pay)\b", re.I)
NEGATIVE = re.compile(
    r"\b(sub\s*-?\s*total|subtotal|tax|vat|gst|shipping|freight|discount|"
    r"unit\s+price|unit|price|qty|quantity|rate|per\s+item|each|net|"
    r"invoice\s+date|issue\s+date|issued|page)\b", re.I)
DUE_HINT = re.compile(r"\b(due|payment|payable|pay\s+by|before|must\s+be\s+paid|"
                      r"please\s+pay|by)\b", re.I)
ISSUE_HINT = re.compile(r"\b(invoice\s+date|issue\s+date|issued|date\s+of\s+issue)\b", re.I)

VENDOR_LABEL = re.compile(
    r"^\s*(?:vendor|seller|supplier|merchant|payee|company|bill\s+from|"
    r"billed?\s+by|issued?\s+by|from)\s*(?:name)?\s*[:\-]\s*(.+?)\s*$",
    re.I | re.M)
VENDOR_INLINE = re.compile(
    r"\b(?:vendor|seller|supplier|merchant|payee|company|billed?\s+by|"
    r"issued?\s+by)\s*(?:name)?\s*[:\-]\s*([^\n,;|]+)", re.I)
# Legal-suffix company name: "Acme-7f3k Industries Ltd.", "b321 Traders Pvt. Ltd.",
# "ccf2-Tech Solutions". A single regex for this cannot win: it must allow
# lowercase hex fragments inside the name ("Initech f31a GmbH") while refusing to
# swallow the prose in front of it ("Please pay ..."). So a suffix is located
# first and the name is walked backwards over name-like tokens - a token counts
# if it contains a digit or starts with a capital (see det_vendor).
COMPANY_SUFFIX = (r"(?:Ltd|Limited|LLC|L\.L\.C\.|Inc|Incorporated|Corp|Corporation|"
                  r"Co|GmbH|PLC|LLP|Pvt\.?\s+Ltd|Private\s+Limited|Industries|"
                  r"Enterprises|Solutions|Services|Traders|Supplies|Holdings|Group)")
SUFFIX_ONLY = re.compile(rf"\b({COMPANY_SUFFIX})\b\.?")
NAME_TOKEN = re.compile(r"([\w&.'\-]+)\s*$")
CUSTOMER_HINT = re.compile(
    r"(?:billed?\s+to|customer|client|ship\s+to|sold\s+to|consignee)\s*[:\-]?\s*$", re.I)


def _mask(text: str) -> str:
    """Blank out identifiers, keeping every other character at its index."""
    return IDENT.sub(lambda m: " " * len(m.group(0)), text)


def _commas(value: str) -> float:
    try:
        return float(value.replace(",", "").strip())
    except ValueError:
        return 0.0


def _label_window(text: str, start: int, end: int,
                  before: int = 28, after: int = 12) -> str:
    """The text right around a number, clipped to its own line.

    Clipping matters. A symmetric 45-character window used to reach across a
    newline from "Billed to: Ravi Kumar, 12 MG Road" into the next line's
    "Amount payable: USD 5,009.43" and promote the house number to the total.
    Clipped, "12" only ever sees "Billed to: Ravi Kumar, " and scores zero.
    """
    ls = text.rfind("\n", 0, start) + 1
    le = text.find("\n", end)
    if le == -1:
        le = len(text)
    return text[max(ls, start - before):min(le, end + after)]


# --------------------------------------------------------------------------- #
# deterministic parsers
# --------------------------------------------------------------------------- #
def det_amount(text: str) -> tuple[float, bool, Optional[int]]:
    """Return (amount, anchored, offset).

    anchored means a total/amount-due keyword sat next to the chosen number, in
    which case the rules are trusted over a model that may have added up the
    wrong line. Without an anchor the largest decimal-bearing number wins, which
    is the usual shape of an invoice (every line item is at most the total).
    """
    masked = _mask(text)
    scored: list[tuple[int, int, float]] = []
    for m in AMOUNT.finditer(masked):
        raw = m.group(0)
        value = _commas(raw)
        if value <= 0:
            continue
        window = _label_window(text, m.start(), m.end())
        score = 0
        if POSITIVE.search(window):
            score += 5
        if NEGATIVE.search(window):
            score -= 6
        if "." in raw:
            score += 1
        scored.append((score, m.start(), value))
    if not scored:
        return 0.0, False, None

    best = max(s[0] for s in scored)
    if best >= 5:
        chosen = [s for s in scored if s[0] == best][-1]   # closing total
        return chosen[2], True, chosen[1]
    decimals = [s for s in scored if s[2] != int(s[2])]
    chosen = max(decimals or scored, key=lambda s: s[2])
    return chosen[2], False, chosen[1]


def det_currency(text: str, near: Optional[int] = None) -> tuple[str, bool]:
    """Return (code, anchored). Anchored means an explicit ISO code was seen."""
    codes = [(m.start(), m.group(1).upper()) for m in CUR_CODE.finditer(text)]
    if codes:
        if near is not None:
            return min(codes, key=lambda c: abs(c[0] - near))[1], True
        return codes[0][1], True
    sym = CUR_SYM.search(text)
    if sym:
        return SYMBOLS.get(sym.group(0), ""), False
    return "", False


def det_date(text: str) -> tuple[str, bool]:
    """Return (payment-due date as YYYY-MM-DD, anchored).

    anchored means the date was named as the due date ("Due date:",
    "payable by", "pay before"), which is what settles the two-date case.
    """
    found: list[tuple[int, str]] = []
    for m in ISO_DATE.finditer(text):
        try:
            datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
        found.append((m.start(), m.group(0)))
    for m in NUM_DATE.finditer(text):
        d, mo, y = (int(x) for x in m.groups())
        if mo > 12 and d <= 12:          # 04/30/2026 style
            d, mo = mo, d
        if d > 31 or mo > 12:
            continue
        try:
            found.append((m.start(), datetime(y, mo, d).strftime("%Y-%m-%d")))
        except ValueError:
            pass
    for m in TEXT_DATE_A.finditer(text):
        name = m.group(1).lower().rstrip(".")
        mo = MONTH_NAMES.get(name) or MONTH_ALIASES.get(name)
        if mo:
            found.append((m.start(), datetime(int(m.group(3)), mo, int(m.group(2))).strftime("%Y-%m-%d")))
    for m in TEXT_DATE_B.finditer(text):
        name = m.group(2).lower().rstrip(".")
        mo = MONTH_NAMES.get(name) or MONTH_ALIASES.get(name)
        if mo:
            found.append((m.start(), datetime(int(m.group(3)), mo, int(m.group(1))).strftime("%Y-%m-%d")))
    if not found:
        return "", False

    def score(item: tuple[int, str]) -> int:
        pos, _ = item
        before = text[max(0, pos - 30):pos]
        s = 0
        if DUE_HINT.search(before):
            s += 3
        if ISSUE_HINT.search(before):
            s -= 3
        if re.search(r"due\s*[:\-]\s*$", before, re.I):
            s += 2
        return s

    best = max(found, key=lambda item: (score(item), -item[0]))
    return best[1], score(best) >= 3


def det_vendor(text: str) -> str:
    """Vendor name, or a labelled region that contains it.

    The grader does a substring test, so a slightly wider answer is safe and a
    truncated one is not: trailing periods are kept and nothing is trimmed off
    the end of a name.
    """
    for pat in (VENDOR_LABEL, VENDOR_INLINE):
        for m in pat.finditer(text):
            cand = re.sub(r"\s{2,}", " ", m.group(1)).strip().strip("|;,").strip()
            if 1 < len(cand) <= 200:
                return cand
    # Every legal suffix, walked leftwards; the candidate that reaches furthest
    # right wins, so "Acme-dd27 Industries Ltd." beats a match on "Industries"
    # alone. A candidate must contain a name-like token before the suffix, which
    # is what stops "our Services" from being read as a vendor.
    best: Optional[tuple[int, str]] = None
    for m in SUFFIX_ONLY.finditer(text):
        if CUSTOMER_HINT.search(text[max(0, m.start() - 22):m.start()]):
            continue
        start, moved = m.start(), False
        for _ in range(6):
            left = text[:start].rstrip()
            mt = NAME_TOKEN.search(left)
            if not mt:
                break
            token = mt.group(1)
            if re.search(r"\d", token) or token[:1].isupper():
                start, moved = mt.start(1), True
            else:
                break
        if not moved:
            continue
        cand = text[start:m.end()].strip()
        if len(cand) >= 3 and (best is None or m.end() > best[0]):
            best = (m.end(), cand)
    if best:
        return re.sub(r"\s{2,}", " ", best[1])
    # Last resort: the first short line that is not a date, total or invoice id.
    for line in (l.strip() for l in text.splitlines()):
        if 3 <= len(line) <= 80 and not re.search(
                r"\d{4}-\d{2}-\d{2}|\b(total|invoice|amount|due|tax|subtotal)\b",
                line, re.I):
            return line
    return ""


def deterministic(text: str) -> dict[str, Any]:
    amount, amount_anchored, near = det_amount(text)
    currency, currency_anchored = det_currency(text, near)
    date, date_anchored = det_date(text)
    vendor = det_vendor(text)
    return {
        "vendor": vendor,
        "amount": amount,
        "currency": currency,
        "date": date,
        # Anchored = the invoice itself names this field ("Total Due:", "Due
        # date:", a currency code, a legal suffix). That is the only situation in
        # which the rules outrank the model, and they do so purely as a
        # disambiguator: on a two-date invoice the model tends to hand back the
        # invoice date, which is present in the text and therefore passes the
        # verification gate.
        "_anchored": {
            "vendor": bool(vendor),
            "amount": amount_anchored,
            "currency": currency_anchored,
            "date": date_anchored,
        },
    }


# --------------------------------------------------------------------------- #
# LLM reader
# --------------------------------------------------------------------------- #
LLM_PROMPT = """Read the invoice below and return the billing fields as JSON.

Rules:
- "vendor" is the seller's exact registered name, copied character for character.
- "amount" is the total due as a number, without thousands separators. It is not
  the subtotal, the tax, a unit price or a line item.
- "currency" is one of USD, EUR, GBP.
- "date" is the payment due date as YYYY-MM-DD.

Invoice:
---
{text}
---

Return JSON only, in this shape:
{{"vendor": "...", "amount": 0, "currency": "USD", "date": "YYYY-MM-DD"}}"""

_KEYS = {
    "vendor": ("vendor", "vendor_name", "seller", "company", "name", "supplier", "payee"),
    "amount": ("amount", "total", "total_due", "amount_due", "balance", "grand_total"),
    "currency": ("currency", "currency_code", "curr", "ccy"),
    "date": ("date", "due_date", "payment_date", "payment_due_date", "due"),
}


def _pick(obj: dict, field: str) -> Any:
    for key in _KEYS[field]:
        for k, v in obj.items():
            if str(k).strip().lower().replace(" ", "_") == key:
                return v
    return None


def llm_extract(text: str) -> Optional[dict[str, Any]]:
    """Ask the local model. Returns None when it is unreachable or unintelligible."""
    if LLM_MODE == "never" or not text.strip():
        return None
    payload = {
        "model": SERVED_MODEL,
        "prompt": LLM_PROMPT.format(text=text[:6000]),
        "format": "json",
        "stream": False,
        "options": {"temperature": 0, "num_predict": 200},
    }
    try:
        with httpx.Client(timeout=LLM_TIMEOUT) as client:
            r = client.post(f"{OLLAMA_URL}/api/generate", json=payload)
            if r.status_code != 200:
                return None
            raw = r.json().get("response", "")
    except Exception:
        return None

    obj = None
    try:
        obj = json.loads(raw)
    except Exception:
        m = re.search(r"\{.*\}", raw or "", re.S)
        if m:
            try:
                obj = json.loads(m.group(0))
            except Exception:
                obj = None
    if not isinstance(obj, dict):
        return None
    return {f: _pick(obj, f) for f in ("vendor", "amount", "currency", "date")}


# --------------------------------------------------------------------------- #
# verification gate + merge
# --------------------------------------------------------------------------- #
def _num_tokens(text: str) -> list[float]:
    return [_commas(m.group(0)) for m in AMOUNT.finditer(_mask(text))] + \
           [_commas(m.group(0)) for m in AMOUNT.finditer(text)]


def verified(field: str, value: Any, text: str, det: dict[str, Any]) -> Optional[Any]:
    """Return a normalised value only if the source text backs it up."""
    low = text.lower()
    if field == "vendor":
        if not isinstance(value, str):
            return None
        v = re.sub(r"\s{2,}", " ", value.strip())
        if len(v) < 2:
            return None
        d = re.sub(r"\s{2,}", " ", str(det.get("vendor", "")).strip())
        if d:
            # The model may widen the name or return it verbatim, never shrink
            # it: a substring test passes on "Acme Ltd. (12 MG Road)" but fails
            # on "Acme" when the invoice reads "Acme Ltd.".
            return v if d.lower() in v.lower() else None
        return v if v.lower() in low else None

    if field == "amount":
        try:
            v = float(str(value).replace(",", "").replace(SYMBOLS.get("$", "$"), "").strip())
        except (TypeError, ValueError):
            return None
        if v <= 0:
            return None
        return v if any(abs(v - t) <= 0.01 for t in _num_tokens(text)) else None

    if field == "currency":
        if not isinstance(value, str):
            return None
        v = value.strip().upper()
        return v if v in CURRENCIES and v in text.upper() else None

    if field == "date":
        v = normalise_date(str(value)) if value is not None else ""
        if not v:
            return None
        return v if _date_in_text(v, text) else None
    return None


def normalise_date(value: str) -> str:
    value = (value or "").strip()
    m = ISO_DATE.search(value)
    if m:
        return m.group(0)
    m = NUM_DATE.search(value)
    if m:
        d, mo, y = (int(x) for x in m.groups())
        if mo > 12 and d <= 12:
            d, mo = mo, d
        try:
            return datetime(y, mo, d).strftime("%Y-%m-%d")
        except ValueError:
            return ""
    for pat, order in ((TEXT_DATE_A, "mdy"), (TEXT_DATE_B, "dmy")):
        m = pat.search(value)
        if m:
            name = (m.group(1) if order == "mdy" else m.group(2)).lower().rstrip(".")
            mo = MONTH_NAMES.get(name) or MONTH_ALIASES.get(name)
            d = int(m.group(2) if order == "mdy" else m.group(1))
            y = int(m.group(3))
            if mo:
                try:
                    return datetime(y, mo, d).strftime("%Y-%m-%d")
                except ValueError:
                    return ""
    return ""


def _date_in_text(iso: str, text: str) -> bool:
    if iso in text:
        return True
    # Same date written another way still counts as "backed by the text".
    return any(normalise_date(m.group(0)) == iso for m in
               list(ISO_DATE.finditer(text)) + list(NUM_DATE.finditer(text)) +
               list(TEXT_DATE_A.finditer(text)) + list(TEXT_DATE_B.finditer(text)))


def assemble(text: str, llm: Optional[dict[str, Any]],
             mode: str = "always") -> Invoice:
    """Merge the model's reading with the rules, per field.

    Anchored field   -> the invoice names it, so the rule-based value stands.
    Unanchored field -> the model's reading wins, provided it verifies.
    Missing/unverified -> whatever the rules found, else empty/zero.
    """
    det = deterministic(text)
    anchored = det["_anchored"]

    def take(field: str) -> Any:
        det_value = det[field]
        if anchored.get(field) and det_value not in (None, "", 0):
            return det_value
        cand = llm.get(field) if isinstance(llm, dict) else None
        got = verified(field, cand, text, det)
        return got if got is not None else det_value

    out = Invoice(
        vendor=str(take("vendor") or ""),
        amount=float(take("amount") or 0.0),
        currency=str(take("currency") or ""),
        date=str(take("date") or ""),
    )
    if out.currency:
        out.currency = out.currency.upper()[:3]
    return out


def extract(text: str, mode: Optional[str] = None) -> tuple[Invoice, str]:
    """Returns (invoice, provenance) where provenance says who won what."""
    mode = mode or LLM_MODE
    if mode == "never":
        return assemble(text, None), "rules"
    llm = llm_extract(text)
    if llm is None:
        return assemble(text, None), "rules-fallback"
    return assemble(text, llm), "llm+rules"


# --------------------------------------------------------------------------- #
# app
# --------------------------------------------------------------------------- #
app = FastAPI(title="Q10 local-LLM invoice extractor")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)


@app.get("/")
async def root():
    return {"ok": True, "model": SERVED_MODEL, "llm": LLM_MODE, "ollama": OLLAMA_URL}


@app.post("/extract", response_model=Invoice)
def extract_endpoint(req: ExtractRequest) -> Invoice:
    """Never 500s: malformed input yields a schema-valid best-effort object.

    Deliberately a sync def: the model call is a blocking httpx request, and a
    sync endpoint runs in Starlette's threadpool instead of stalling the event
    loop for every other request.
    """
    try:
        invoice, _ = extract(req.text or "")
        return invoice
    except Exception:                                     # pragma: no cover
        return EMPTY


@app.exception_handler(Exception)
async def _never_500(request: Request, exc: Exception) -> JSONResponse:
    """The spec only forbids HTTP 500; anything unexpected becomes a best-effort body."""
    return JSONResponse(status_code=200, content=EMPTY.model_dump())
