# Q12 — Middleware stack: request context + scoped CORS + per-client rate limit

FastAPI service for exam question 12 (1.5 marks). Assigned values:
**allowed origin `https://app-dt9gio.example.com`**, **bucket B = 15 requests / 10 s**.

`GET /ping` → `{"email": "…", "request_id": "…"}`. Submit the **base URL**; the
grader appends `/ping`.

## The composition is the answer

```
RequestContext  ->  CORS  ->  RateLimit  ->  routes
   (outermost)                                 (innermost)
```

Starlette inserts each middleware at the front of the stack, so the order of the
`add_middleware` calls is reversed from the order they run in. Each position is
load-bearing:

**RequestContext outermost.** Every response passes back through it, so
`X-Request-ID` lands on the 200, on the 429 the limiter raises, and on the CORS
preflight alike. It also mints the id *before* the limiter needs it for its error
body.

**CORS outside RateLimit.** Starlette's CORS layer answers preflights itself and
never calls the inner app, which is what keeps browser `OPTIONS` requests out of
the rate bucket. Reverse these two and every fetch the page makes costs two
tokens, so a page doing 20 requests spends 40 and trips the limiter around
request 8 — the grader would see a 429 during checks that must return 200. This
is the one composition mistake that changes no status code on its own, so
`test_preflights_do_not_spend_the_bucket` is written specifically to catch it.

**RateLimit innermost.** A layer, not a per-route dependency: it counts every
request that reaches it, whatever the path.

## Two details that fail silently

**`X-Request-ID` must be in `Access-Control-Expose-Headers`.** It is not a CORS
safelisted response header, so without that line the browser reports it as
`null` and the grader's "same value in body and header" comparison fails against
a service that is behaving perfectly. `test_request_id_header_is_exposed_to_browser_js`
pins it.

**ACAO is never `*`.** The allowlist is the assigned origin plus the exam page's
own origin. The spec asks for the exam origin but never states it; it is in the
exam file itself, in the `saved from url=` comment on line 2:
`https://exam.sanand.workers.dev/tds-2026-09-ga2`. Both are allowed exactly;
anything else gets no ACAO at all, not even on the preflight.

## Verification

```bash
pip install -r requirements-dev.txt
pytest -q                                  # 31 tests
python tools/mutate_q12.py                 # 13 injected bugs, does the suite catch them?
python tools/grader_sim.py http://127.0.0.1:8323
```

**31 tests pass.** One per grader check, plus the hostile cases: an origin that
must receive no ACAO, a preflight that must not spend the bucket, a 429 that must
still be readable through CORS, an awkward supplied id, an empty `X-Request-ID`,
and the response body having exactly the two documented keys.

**13 of 13 injected bugs are caught**, each a plausible wrong version of one
layer:

| mutation | caught by |
|---|---|
| ignores the inbound `X-Request-ID` | `test_grader_check_1_…` |
| reuses one generated id forever | `test_generated_ids_are_unique_per_request` |
| id in body but not in header | `test_grader_check_1_…` |
| `allow_origins=["*"]` | `test_a_disallowed_origin_receives_no_acao` |
| drops the exam origin | `test_preflight_succeeds_for_each_allowed_origin[exam]` |
| `X-Request-ID` not exposed | `test_request_id_header_is_exposed_to_browser_js` |
| allows B+1 per window | `test_grader_check_3_…` |
| one shared bucket | `test_a_fresh_client_id_is_still_accepted_…` |
| records rejected hits | `test_bucket_drains_while_the_client_keeps_retrying` |
| no lock around count-then-append | `test_hit_is_mutually_exclusive` |
| 429 without `Retry-After` | `test_429_carries_retry_after_and_the_request_id` |
| **rate limiter placed outside CORS** | `test_429_carries_retry_after_…` (order also breaks the id on error paths) |
| extra key in the `/ping` body | `test_ping_body_has_exactly_the_two_documented_keys` |

The harness asserts `main.py` is restored byte-identical (sha256) afterwards.

### The concurrency test that did not work, and what replaced it

My first mutual-exclusion test raced 24 threads through one bucket and asserted
the exact number allowed. It passed on an unlocked implementation, so I measured
instead of assuming: **20 rounds × 24 threads against an unlocked bucket never
overshot its limit once.** The critical section is about two bytecodes between
the length check and the append, and CPython's switch timer (~1 µs at best)
essentially never fires inside it — a green race test here would have been
evidence of nothing at all.

`test_hit_is_mutually_exclusive` asserts the operational definition instead:
while one caller holds the critical section, no other caller can complete. That
fails deterministically the moment the lock is removed. The racing test is kept
as a load smoke test, with a docstring saying plainly that it is not the lock
detector.

**Live over real HTTP** (`tools/grader_sim.py`, uvicorn, not `TestClient`): 4/4.
Raw header bytes from the exam origin:

```
OPTIONS /ping   Origin: https://exam.sanand.workers.dev
  access-control-allow-origin: https://exam.sanand.workers.dev      <- exact, not *
  access-control-allow-headers: x-request-id,x-client-id
  x-request-id: f2878f06-…                                          <- present on preflights too

GET /ping       Origin: https://not-allowed.example.com
  (no access-control-allow-origin)                                  <- refused without a wildcard
```

`TestClient` cannot show either of those bytes, and both are exactly what the
browser checks depend on.

## Deploy (Render)

New → Web Service → this repo → **Root Directory `GA2/Q12`**, Runtime **Docker**.
No environment variables are required; `EMAIL`, `ASSIGNED_ORIGIN`, `EXAM_ORIGIN`,
`RATE_LIMIT` and `RATE_WINDOW` default to the assigned values.

```bash
python tools/grader_sim.py https://<your-service>.onrender.com
```

## Limitations, stated rather than hidden

- **The bucket lives in memory.** A Render restart empties it, and it is not
  shared between instances. Fine for a single grader run; it is a deliberate
  trade-off (no Redis), not an oversight.
- **The window is the specified 10 s.** Checks 1 and 2 must not run within 10 s
  of check 3 *using the same `X-Client-Id`*, or they will see 429s. The grader's
  own order (1, 2, then the burst) avoids this, and its third check explicitly
  exercises a different client id.
- **`Retry-After` is the time until a slot frees**, rounded up, not the window
  length.
