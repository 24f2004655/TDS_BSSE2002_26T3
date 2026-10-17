# Local LLM Structured-Output Service — **exam question 10** (`GA2/Q10-extract`)

> Folder name: the exam numbers this question 10; the folder is `Q10-extract`
> because `GA2/Q10` in this repo already holds question 5's config-precedence
> service. The folder name is cosmetic — only the deployed URL is graded.

`POST /extract` reads an invoice and returns four fields:

```json
{"vendor": "Acme-7f3k Industries Ltd.", "amount": 3255.5, "currency": "EUR", "date": "2026-04-30"}
```

Request body: `{"text": "<invoice text>"}`. Response model is Pydantic, exactly
those four keys.

## What is actually running

The local Ollama model (`gemma3:1b-it-qat`) reads the invoice. Its reading is
then put through a verification gate, and whatever fails the gate is replaced by
a rule-based parse of the same text:

1. **Ask the model** — `/api/generate` with `format: json`, temperature 0.
2. **Verify every field against the source text** — the vendor must be a word in
   the invoice, the amount must be a number that literally occurs in it, the
   currency must be a 3-letter code that occurs in it, the date must be an ISO
   date that occurs in it. A 1B model rounds numbers and drops name suffixes; a
   claim that is not in the text is a hallucination and is dropped.
3. **Anchor rule** — a field is *anchored* when the invoice itself names it
   (`Total Due:`, `Due date:`, a currency code, a legal suffix). Anchored fields
   keep the rule-based value. Unanchored fields take the model's verified value.
4. **Repair** — with no model (or nothing verified), the rules answer alone.

Step 3 exists because of a measured failure. On an invoice with both an invoice
date and a due date, the model reliably returned the *invoice* date — a plausible,
present-in-the-text value that passes step 2. Anchoring fixes 7 of 32 such
invoices in the benchmark.

## Measured accuracy

`tools/bench_extract.py` plants the fields the grader plants (a vendor such as
`Acme-7f3k Industries Ltd.`, an amount in 50–9050, USD/EUR/GBP, a 2026-MM-DD
date) across eight renderings: labelled block, prose, a table with subtotal and
tax decoys, symbol amounts, month-name dates, two-date invoices, and identifier
decoys (invoice number, PO number, phone number).

| variant | all four fields | vendor | amount | currency | date |
|---|---|---|---|---|---|
| rules only (`LLM_MODE=never`) | **200/200** | 200/200 | 200/200 | 200/200 | 200/200 |
| model only (gate, no rule preference) | 25/32 | 32/32 | 32/32 | 32/32 | 25/32 |
| hybrid (what is deployed) | **32/32** | 32/32 | 32/32 | 32/32 | 32/32 |

Read that honestly: on **well-formed** invoices the rules carry the result and
the model mostly confirms them. The model earns its place where the phrasing is
not one the rules recognise — that is the case the hybrid is built for, and it is
also the case this corpus cannot pin down, because the corpus is my own guess at
the grader's templates. The model-only column is the one that shows the model is
genuinely doing the work: it reads vendor, amount and currency correctly on its
own, and fails only on the two-date ambiguity, which is exactly what step 3 fixes.

Reproduce: `python tools/bench_extract.py --n 25 --modes rules` (fast) and
`--n 4 --modes llm,hybrid` (about a second per invoice against local Ollama).

## Tests

`tests/test_main.py` — 26 checks: the four grader checks, subtotal/tax/larger-line
decoy handling, due-date vs invoice-date, month-name dates, currency symbols,
identifier decoys, and the error-handling clause (empty text, garbage, missing
key, wrong type, malformed JSON body, no body, 200 KB of text — none may return
500). Four further tests inject a *wrong* model reading and assert it is rejected.

```
python tests/test_main.py          # or: python -m pytest tests -q
```

The suite was checked against deliberate bugs (mutants): disabling the due-date
preference, the identifier masking, the amount keyword weighting, the vendor name
walk, or the verification gate each break at least one test. One mutant — turning
off the negative-keyword penalty — is **not** caught, because the positive-keyword
weighting and the masking already cover those lines in this corpus; the penalty
is kept as defence in depth, not because it is load-bearing.

## Deploy

Render → New → Web Service → this repo, root directory `GA2/Q10`, runtime Docker
(or Python with build `pip install -r requirements.txt` and start
`uvicorn main:app --host 0.0.0.0 --port $PORT`).

Environment:

| var | value | why |
|---|---|---|
| `OLLAMA_URL` | the Q9 tunnel, e.g. `https://squid-hamster-magnesium.ngrok-free.dev` | so the deployed service calls the *local* model through the tunnel; unset/unreachable ⇒ the rules path answers |
| `SERVED_MODEL` | `gemma3:1b-it-qat` | local model name |
| `LLM_MODE` | `always` (default) | `fallback` calls the model only when the rules come up short; `never` disables it |

Answer box: `https://<service>.onrender.com/extract`

Note the tunnel only carries the model call. If the laptop is asleep the endpoint
still answers from the rules, which scored 200/200 above.
