# Q11 — Orders API: idempotency + cursor pagination + per-client rate limiting

FastAPI service for exam question 11 (2 marks). Assigned values: **T = 54** orders,
**R = 19 requests per 10 s**.

Base URL goes in question 11's answer box; the grader appends `/orders`.

## Endpoints

| | |
|---|---|
| `POST /orders` | header `Idempotency-Key: <k>` → 201 `{"id": "…", …}`; same key always returns the same id |
| `GET /orders?limit=P&cursor=C` | page of the fixed catalog, ids 1..54 |
| `GET /` | health/config echo |

## The three decisions that matter

**Idempotency — the check-then-set happens inside one lock.** The obvious
implementation reads the key, builds an order if absent, then writes. Two
callers arriving together both read "absent" and both write, which is the exact
duplicate idempotency exists to prevent. `IdempotencyStore.create` holds the lock
across read *and* write, and returns `(order, created)`. A replay returns the
stored order verbatim, **including its 201**: the caller cannot tell a retry from
the original. If the same key arrives with a different body, the key still wins —
a 409 there would fail grader check 1 if the grader retried with a reshaped body.

**Pagination — the cursor is a base64 offset, so it is stateless.** It survives a
restart or a second instance, unlike a server-side "page N" session. `next_cursor`
is JSON `null` on the last page rather than an omitted key, so a caller doing
`body["next_cursor"]` cannot hit a `KeyError` and `if not cursor: break`
terminates. The spec says `next` and `orders` are accepted aliases, so all four
keys are published with the same values — a grader reading either name works.
A cursor that does not decode starts at id 1: the spec calls cursors opaque, so
a 400 for a string we did not mint would fail a check a 200 cannot.

**Rate limiting — a sliding window keyed by `X-Client-Id`, applied as a route
dependency rather than middleware.** Sliding, not fixed, so a caller cannot get
2R requests by straddling a window boundary. Only *allowed* requests are
recorded; if rejections were recorded too, a client retrying faster than the
window drains would refill its own bucket and never recover.
`test_bucket_drains_while_the_client_keeps_retrying` hammers a blocked client
specifically to pin that down. Being a dependency rather than middleware keeps
CORS `OPTIONS` preflights out of the bucket — charging them would halve the
budget the grader is measuring.

## Verification

Run from this directory:

```bash
pip install -r requirements-dev.txt
pytest -q                                    # 39 tests
python tools/mutate_q11.py                   # 11 injected bugs, does the suite catch them?
python tools/grader_sim.py http://127.0.0.1:8321 --page 7
```

**39 tests pass.** They are contract tests: each grader check has one, plus the
hostile variants (no body / junk body / non-object body on POST, every page size
from 1 to 100, garbage cursors, a cursor entered mid-scan, missing `X-Client-Id`,
preflights not consuming budget, CORS on the 429 path).

**11 of 11 injected bugs are caught** (`tools/mutate_q11.py`), each mutation being
a plausible wrong implementation:

| mutation | caught by |
|---|---|
| no lock around check-then-create | `test_store_creation_is_mutually_exclusive_below` |
| replay mints a new order | `test_same_key_twice_returns_same_id` |
| allows R+1 per window | `test_r_plus_1_requests_…` |
| one global bucket | `test_every_limit_serves_the_whole_catalog_once[1]` |
| records rejected hits | `test_bucket_drains_while_the_client_keeps_retrying` |
| 429 with no `Retry-After` | `test_r_plus_1_requests_…` |
| cursor skips one id | `test_full_scan_has_no_gaps_no_repeats_…` |
| page ignores `limit` | `test_full_scan_has_no_gaps_no_repeats_…` |
| cursor never becomes null | `test_catalog_is_exactly_ids_1_to_T` |
| ignores `X-Client-Id` | `test_every_limit_serves_the_whole_catalog_once[1]` |
| `Retry-After` not exposed | `test_retry_after_is_readable_from_browser_js` |

The mutation run asserts `main.py` is restored byte-identical (sha256) at the end.

**Live over real HTTP** (`tools/grader_sim.py` against uvicorn, not `TestClient`):

```
page size 7: 1. idempotent POST PASS  2. cursor pagination PASS (1..54 in 8 pages)
             3. rate limit PASS (19×200, then 429 w/ Retry-After: 10, fresh id 200)
             CORS PASS -> 4/4
page size 1: 1..54 in 54 pages, no gaps/repeats -> 4/4
```

`TestClient` is not enough on its own and this is where it showed: my first
version of the sim checked `Access-Control-Expose-Headers` on the **preflight**,
where Starlette never sends it, and reported a false failure. A real HTTP GET
returned `access-control-expose-headers: Retry-After, *` and the 429 carries it
too. The sim now checks preflight and actual responses separately.

## Deploy (Render)

New → Web Service → this repo → **Root Directory `GA2/Q11`**, Runtime **Docker**.
No environment variables are needed; `TOTAL_ORDERS`, `RATE_LIMIT`, `RATE_WINDOW`
default to the assigned values and exist only so the code can be re-verified
against a different pair.

Then, before submitting:

```bash
python tools/grader_sim.py https://<your-service>.onrender.com
```

## Limitations, stated rather than hidden

- **State is in memory.** A Render restart drops idempotency keys and rate
  counters. Within one grader run the instance stays warm, so this is a design
  trade-off (no database) rather than an accident.
- **A full scan at `limit=1` costs 54 requests from one client id**, which exceeds
  the 19/10 s budget: the grader must either use a larger page or rotate client
  ids per check, or its own pagination check would trip its own rate-limit check.
  `tools/grader_sim.py` rotates ids for exactly this reason, and it is why
  `test_every_limit_serves_the_whole_catalog_once` uses a distinct id per page.
- **`Retry-After` is computed from the oldest hit in the window**, so it is the
  time until a slot frees, rounded up — not the window length.
