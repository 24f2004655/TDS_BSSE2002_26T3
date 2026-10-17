"""Measure Q10 extraction accuracy on a synthetic invoice corpus.

The grader plants a vendor ("Acme-xxxx Industries Ltd."), an amount in 50-9050, a
currency in USD/EUR/GBP and a 2026-MM-DD date, then checks:

    vendor   : case-insensitive substring
    amount   : within +/- 0.01
    currency : exact 3-letter uppercase
    date     : contains the YYYY-MM-DD substring

This script plants exactly that, across eight plausible renderings (labelled
block, prose, table with decoy subtotal/tax, symbol amounts, month-name dates,
two dates where only one is the due date, identifier decoys), and reports per
field accuracy for each extraction variant.

    python tools/bench_extract.py --n 25 --modes rules,llm,hybrid
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main as extractor  # noqa: E402

VENDORS = ["Acme-{h} Industries Ltd.", "{h}-Tech Solutions", "Northwind {h} Ltd.",
           "Globex {h} Corporation", "{h} Traders Pvt. Ltd.", "Initech {h} GmbH"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]


def make_invoice(rng: random.Random) -> tuple[str, dict]:
    h = "".join(rng.choice("0123456789abcdef") for _ in range(4))
    vendor = rng.choice(VENDORS).replace("{h}", h).replace("{h}", h.upper())
    amount = round(rng.uniform(50, 9050), 2)
    cur = rng.choice(["USD", "EUR", "GBP"])
    sym = {"USD": "$", "EUR": "\u20ac", "GBP": "\u00a3"}[cur]
    due = f"2026-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
    issue = f"2026-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
    sub = round(amount * 0.88, 2)
    tax = round(amount - sub, 2)
    inv = f"INV-{rng.randint(1000, 9999)}"
    phone = f"+91 9{rng.randint(100000000, 999999999)}"

    template = rng.randrange(8)
    if template == 0:
        text = (f"{inv}\nVendor: {vendor}\nInvoice Date: {issue}\nCurrency: {cur}\n"
                f"Subtotal: {sub:,.2f}\nTax: {tax:,.2f}\nTotal Due: {amount:,.2f}\n"
                f"Payment due by {due}")
    elif template == 1:
        text = (f"Please pay {vendor} the amount of {amount:,.2f} {cur} by {due}. "
                f"This invoice {inv} was issued on {issue}. Questions? Call {phone}.")
    elif template == 2:
        text = (f"{vendor}\n{inv}\nDate {issue}\n\n"
                f"Item          Qty   Unit Price      Amount\n"
                f"Widget         3      120.00        360.00\n"
                f"Service        1      500.00        500.00\n"
                f"Subtotal       {sub:,.2f}\nTax (8.5%)     {tax:,.2f}\n"
                f"Total          {amount:,.2f}\nDue date: {due}\nCurrency: {cur}")
    elif template == 3:
        text = (f"{vendor} \u2014 amount due {sym}{amount:,.2f} ({cur}), "
                f"payable by {due}.")
    elif template == 4:
        text = f"{vendor}\nAmount Due: {cur} {amount:,.2f}\nDue: {due}"
    elif template == 5:
        y, m, d = due.split("-")
        text = (f"{vendor}\n{inv}\nTotal: {amount:,.2f} {cur}\n"
                f"Payment due: {MONTHS[int(m) - 1]} {int(d)}, {y}\nIssued {issue}")
    elif template == 6:
        text = (f"{vendor}\nBilled to: Ravi Kumar, 12 MG Road\nInvoice {inv}\n"
                f"Amount payable: {cur} {amount:,.2f}\nInvoice date: {issue}\n"
                f"Due date: {due}")
    else:
        text = (f"Invoice {inv} from {vendor}\n"
                f"Total amount due is {sym}{amount:,.2f}, in {cur}.\n"
                f"Pay before {due}. Purchase order PO-{rng.randint(100, 999)}.")

    truth = {"vendor": vendor, "amount": amount, "currency": cur, "date": due}
    return text, truth


def check(invoice, truth) -> dict[str, bool]:
    return {
        "vendor": truth["vendor"].lower() in (invoice.vendor or "").lower(),
        "amount": abs(float(invoice.amount or 0) - truth["amount"]) <= 0.01,
        "currency": (invoice.currency or "") == truth["currency"],
        "date": truth["date"] in (invoice.date or ""),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=25, help="invoices per template")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--modes", default="rules,llm,hybrid")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    corpus = [make_invoice(rng) for _ in range(args.n * 8)]

    for mode in args.modes.split(","):
        if mode == "rules":
            extractor.LLM_MODE = "never"
        elif mode == "llm":
            extractor.LLM_MODE = "always"
        else:
            extractor.LLM_MODE = "always"

        stats = {f: 0 for f in ("vendor", "amount", "currency", "date")}
        full = 0
        for text, truth in corpus:
            try:
                if mode == "rules":
                    invoice, _ = extractor.extract(text, mode="never")
                elif mode == "llm":
                    # What the local model alone proposes, after only the
                    # text-verification gate - no rule-based value in front of it.
                    llm = extractor.llm_extract(text)
                    det = extractor.deterministic(text)
                    vals = {}
                    for f in ("vendor", "amount", "currency", "date"):
                        got = extractor.verified(
                            f, (llm or {}).get(f), text, det) if llm else None
                        vals[f] = got if got is not None else (
                            det[f] if f != "amount" else det["amount"])
                    invoice = extractor.Invoice(
                        vendor=str(vals["vendor"] or ""),
                        amount=float(vals["amount"] or 0.0),
                        currency=str(vals["currency"] or "")[:3].upper(),
                        date=str(vals["date"] or ""))
                else:
                    invoice, _ = extractor.extract(text, mode="always")
            except Exception as e:                        # noqa: BLE001
                print("  crash:", type(e).__name__, e)
                continue
            got = check(invoice, truth)
            for f, ok in got.items():
                stats[f] += ok
            full += all(got.values())

        n = len(corpus)
        print(f"\n{mode:>7}: all-four {full}/{n}")
        for f, ok in stats.items():
            print(f"          {f:<8} {ok}/{n}  ({100 * ok / n:.1f}%)")


if __name__ == "__main__":
    main()
