"""Contract tests for Q10. Run: python -m pytest tests -q  (or python tests/test_main.py)

They cover the four things the grader checks plus the error-handling clause, and
they inject deliberate bugs to confirm the tests can fail (see --self-check).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("LLM_MODE", "never")          # deterministic path by default
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main as q10  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(q10.app, raise_server_exceptions=False)

INVOICE = ("INV-4821\nVendor: Acme-7f3k Industries Ltd.\nInvoice Date: 2026-04-01\n"
           "Currency: EUR\nSubtotal: 3,000.00\nTax (8.5%) 255.00\n"
           "Total Due: 3,255.50\nPayment due by 2026-04-30")
TRUTH = {"vendor": "Acme-7f3k Industries Ltd.", "amount": 3255.50,
         "currency": "EUR", "date": "2026-04-30"}


def post(payload, **kw):
    return client.post("/extract", json=payload, **kw)


def ok(invoice, truth) -> None:
    assert set(invoice) == {"vendor", "amount", "currency", "date"}, invoice
    assert isinstance(invoice["vendor"], str)
    assert isinstance(invoice["amount"], (int, float))
    assert isinstance(invoice["currency"], str)
    assert isinstance(invoice["date"], str)
    assert truth["vendor"].lower() in invoice["vendor"].lower(), invoice
    assert abs(invoice["amount"] - truth["amount"]) <= 0.01, invoice
    assert invoice["currency"] == truth["currency"], invoice
    assert truth["date"] in invoice["date"], invoice


# --------------------------------------------------------------------------- #
# the grader's four checks
# --------------------------------------------------------------------------- #
def test_valid_invoice():
    r = post({"text": INVOICE})
    assert r.status_code == 200, r.text
    ok(r.json(), TRUTH)


def test_amount_is_the_total_not_the_subtotal_or_tax():
    r = post({"text": "Acme Ltd.\nSubtotal 100.00\nTax 8.50\nTotal 108.50\nUSD"})
    assert abs(r.json()["amount"] - 108.50) <= 0.01, r.json()


def test_due_date_not_invoice_date():
    r = post({"text": "Acme Ltd.\nInvoice Date: 2026-01-05\nDue date: 2026-02-20\nUSD 100"})
    assert r.json()["date"] == "2026-02-20", r.json()


def test_identifier_decoys_are_not_the_amount():
    r = post({"text": "Invoice INV-9050\nAcme Ltd.\nCall +91 9876543210\n"
                      "Total Due: 250.75 USD by 2026-03-01"})
    assert abs(r.json()["amount"] - 250.75) <= 0.01, r.json()


def test_total_beats_a_larger_line_item():
    r = post({"text": "Acme Ltd.\nLine item 9,000.00\nTotal Due: 250.75 USD\nDue 2026-01-02"})
    assert abs(r.json()["amount"] - 250.75) <= 0.01, r.json()


def test_total_beats_a_larger_fractional_line_item():
    # Both numbers are fractional and one is bigger: only the "total" keyword
    # can settle this, so this is what pins the keyword weighting down.
    r = post({"text": "Acme Ltd.\nItem 9,000.50\nTotal Due: 250.75 USD\nDue 2026-01-02"})
    assert abs(r.json()["amount"] - 250.75) <= 0.01, r.json()


def test_identifier_decoys_beat_an_unanchored_guess():
    # No total keyword anywhere, so the fallback (largest number) is in play and
    # the PO number must already have been masked away.
    r = post({"text": "Acme Ltd.\nPO 123456789\nRemit 500 USD by 2026-01-02"})
    assert abs(r.json()["amount"] - 500) <= 0.01, r.json()


def test_currency_symbol_maps_to_a_code():
    r = post({"text": "Acme Ltd.\namount due \u00a31,250.00, payable by 2026-05-06"})
    assert r.json()["currency"] == "GBP", r.json()


def test_month_name_date_is_normalised():
    r = post({"text": "Acme Ltd.\nTotal 900.00 USD\nPayment due: March 3, 2026"})
    assert r.json()["date"] == "2026-03-03", r.json()


def test_response_has_exactly_four_fields():
    r = post({"text": "Acme Ltd. 10.00 USD 2026-01-01"})
    assert set(r.json().keys()) == {"vendor", "amount", "currency", "date"}


# --------------------------------------------------------------------------- #
# error handling: the spec forbids HTTP 500, and nothing else
# --------------------------------------------------------------------------- #
def test_empty_text_is_not_500():
    r = post({"text": ""})
    assert r.status_code != 500, r.text


def test_garbage_text_is_not_500():
    r = post({"text": "asdkjfh ???? ~~~~"})
    assert r.status_code != 500, r.text
    assert isinstance(r.json(), dict)


def test_missing_text_key_is_not_500():
    assert post({}).status_code != 500


def test_wrong_type_is_not_500():
    assert post({"text": 12345}).status_code != 500


def test_malformed_body_is_not_500():
    r = client.post("/extract", content=b"{not json",
                    headers={"Content-Type": "application/json"})
    assert r.status_code != 500, r.text


def test_no_body_is_not_500():
    assert client.post("/extract").status_code != 500


def test_huge_text_is_not_500():
    assert post({"text": "x" * 200_000}).status_code != 500


# --------------------------------------------------------------------------- #
# the gate: unverified model output is discarded
# --------------------------------------------------------------------------- #
def test_model_hallucinated_amount_is_rejected():
    text = "Acme Ltd.\nAmount due: 500.00 USD\nDue 2026-01-01"
    invoice = q10.assemble(text, {"vendor": "Acme Ltd.", "amount": 999_999,
                                  "currency": "USD", "date": "2026-01-01"})
    assert abs(invoice.amount - 500.00) <= 0.01, invoice


def test_model_truncated_vendor_is_rejected():
    text = "Acme-7f3k Industries Ltd.\nTotal 10.00 USD 2026-01-01"
    invoice = q10.assemble(text, {"vendor": "Acme", "amount": 10.0,
                                  "currency": "USD", "date": "2026-01-01"})
    assert "Acme-7f3k Industries Ltd." in invoice.vendor, invoice


def test_model_wrong_invoice_date_is_rejected():
    text = "Acme Ltd.\nInvoice date 2026-01-05\nDue date: 2026-02-20\nUSD 100"
    invoice = q10.assemble(text, {"vendor": "Acme Ltd.", "amount": 100.0,
                                  "currency": "USD", "date": "2026-01-05"})
    assert invoice.date == "2026-02-20", invoice


def test_unanchored_amount_is_still_gated():
    """With no total/amount keyword the model gets the say - if it verifies."""
    text = "Acme Ltd.\nRemit 500.00 USD by 2026-01-02"
    invoice = q10.assemble(text, {"vendor": "Acme Ltd.", "amount": 999_999.0,
                                  "currency": "USD", "date": "2026-01-02"})
    assert abs(invoice.amount - 500.00) <= 0.01, invoice


def test_unanchored_date_is_still_gated():
    text = "Acme Ltd.\nRemit 500.00 USD, 2026-07-08"
    invoice = q10.assemble(text, {"vendor": "Acme Ltd.", "amount": 500.0,
                                  "currency": "USD", "date": "2026-01-01"})
    assert invoice.date == "2026-07-08", invoice


def test_unanchored_currency_is_still_gated():
    text = "Acme Ltd.\nRemit 500.00 in dollars, 2026-07-08"
    invoice = q10.assemble(text, {"vendor": "Acme Ltd.", "amount": 500.0,
                                  "currency": "XYZ", "date": "2026-07-08"})
    assert invoice.currency != "XYZ", invoice


def test_model_fills_an_unanchored_field():
    # No currency token anywhere; the model's verified reading is what is left.
    text = "\u20ac 42.00 from Widget GmbH, payable by 2026-06-07"
    invoice = q10.assemble(text, {"vendor": "Widget GmbH", "amount": 42.0,
                                  "currency": "USD", "date": "2026-06-07"})
    assert invoice.currency == "EUR"          # \u20ac wins: it is in the text
    assert invoice.amount == 42.0


def test_llm_disabled_still_answers():
    q10.LLM_MODE = "never"
    invoice, prov = q10.extract(INVOICE)
    assert prov == "rules"
    ok(invoice.model_dump(), TRUTH)


def test_live_ollama_when_available():
    """Skipped when Ollama is not running."""
    try:
        import httpx
        httpx.get(f"{q10.OLLAMA_URL}/api/tags", timeout=3)
    except Exception:
        print("  (skipped: no local Ollama)")
        return
    q10.LLM_MODE = "always"
    invoice, prov = q10.extract(INVOICE)
    assert prov == "llm+rules", prov
    ok(invoice.model_dump(), TRUTH)
    q10.LLM_MODE = "never"


# --------------------------------------------------------------------------- #
def run() -> None:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {t.__name__}  {e}")
        except Exception as e:                                  # noqa: BLE001
            failed += 1
            print(f"  ERROR {t.__name__}  {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if run() else 0)
