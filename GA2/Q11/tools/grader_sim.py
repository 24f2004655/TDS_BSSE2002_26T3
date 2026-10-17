"""Simulate the three grader checks against a running service.

Standard library only, so it runs anywhere - WSL, Windows Python, CI:

    python tools/grader_sim.py http://127.0.0.1:8321
    python tools/grader_sim.py https://my-orders.onrender.com

Unlike tests/test_main.py this goes over real HTTP through a real server, which
is the only way to exercise CORS middleware, header casing and the 201 status
line as the grader will meet them. Run it against the deployed URL before
submitting.

    --total T    expected catalog size (assigned value)
    --limit R    expected requests per 10s window (assigned value)
    --page P     page size to scan with
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid


def call(method: str, url: str, headers: dict | None = None, body: bytes | None = None,
         timeout: float = 20.0) -> tuple[int, dict, str]:
    req = urllib.request.Request(url, method=method, data=body)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read().decode()
    except urllib.error.HTTPError as exc:            # 4xx/5xx still carry headers
        return exc.code, {k.lower(): v for k, v in exc.headers.items()}, exc.read().decode()


def check_idempotency(base: str) -> tuple[bool, str]:
    key = f"sim-{uuid.uuid4()}"
    headers = {"Idempotency-Key": key, "X-Client-Id": "sim-idem",
               "Content-Type": "application/json"}
    payload = json.dumps({"item": "sim-widget", "quantity": 2}).encode()

    s1, _, b1 = call("POST", f"{base}/orders", headers, payload)
    s2, _, b2 = call("POST", f"{base}/orders", headers, payload)

    if s1 != 201:
        return False, f"first POST returned {s1}, expected 201 (body: {b1[:200]})"
    try:
        id1, id2 = json.loads(b1)["id"], json.loads(b2)["id"]
    except Exception as exc:
        return False, f"could not read id from response: {exc} ({b1[:200]})"
    if s2 not in (200, 201):
        return False, f"replay returned {s2}, expected 200 or 201"
    if id1 != id2:
        return False, f"replay created a second order: {id1} != {id2}"
    return True, f"key replayed -> same id {id1} (statuses {s1}, {s2})"


def check_pagination(base: str, total: int, page: int, distinct_clients: bool) -> tuple[bool, str]:
    ids: list[int] = []
    sizes: list[int] = []
    cursor = None
    pages = 0
    while True:
        pages += 1
        if pages > total + 5:
            return False, "pagination did not terminate - is next_cursor ever null?"
        params = f"?limit={page}"
        if cursor is not None:
            params += f"&cursor={urllib.parse.quote(str(cursor))}"
        # At limit=1 a full scan is total requests, which can exceed the rate
        # window; the grader must either use a bigger page or rotate ids. We
        # rotate, so this check measures pagination rather than the limiter.
        client_id = f"sim-page-{pages}" if distinct_clients else "sim-page"
        status, _, body = call("GET", f"{base}/orders{params}", {"X-Client-Id": client_id})
        if status != 200:
            return False, f"page {pages} returned {status} (body: {body[:200]})"
        try:
            data = json.loads(body)
        except Exception as exc:
            return False, f"page {pages} was not JSON: {exc}"
        items = data.get("items") or data.get("orders") or []
        sizes.append(len(items))
        if len(items) > page:
            return False, f"page {pages} returned {len(items)} items for limit={page}"
        ids.extend(o["id"] for o in items)
        cursor = data.get("next_cursor") or data.get("next")
        if not cursor:
            break

    expected = list(range(1, total + 1))
    if ids != expected:
        missing = sorted(set(expected) - set(ids))
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        extra = sorted(set(ids) - set(expected))
        return False, (f"scan of 1..{total} in {pages} pages was wrong: "
                       f"{len(ids)} ids, missing={missing[:10]}, duplicates={dupes[:10]}, "
                       f"unexpected={extra[:10]}")
    return True, f"1..{total} served in {pages} pages of <={page}, no gaps, no repeats"


def check_rate_limit(base: str, limit: int) -> tuple[bool, str]:
    victim = f"sim-burst-{uuid.uuid4()}"
    statuses = []
    for _ in range(limit + 1):
        status, headers, _ = call("GET", f"{base}/orders?limit=1", {"X-Client-Id": victim})
        statuses.append((status, headers))

    early = [i + 1 for i, (s, _) in enumerate(statuses[:limit]) if s != 200]
    if early:
        return False, f"requests {early} from one client were rejected before the limit of {limit}"
    status, headers = statuses[limit]
    if status != 429:
        return False, f"request {limit + 1} returned {status}, expected 429"
    retry_after = headers.get("retry-after")
    if not retry_after:
        return False, "429 had no Retry-After header"
    try:
        if not 0 < float(retry_after) <= 10:
            return False, f"Retry-After was {retry_after}, expected 0 < value <= 10"
    except ValueError:
        return False, f"Retry-After was not numeric: {retry_after!r}"

    other = f"sim-quiet-{uuid.uuid4()}"
    status2, _, _ = call("GET", f"{base}/orders?limit=1", {"X-Client-Id": other})
    if status2 != 200:
        return False, f"a different client id got {status2} while another was blocked"

    return True, (f"first {limit} requests 200, request {limit + 1} -> 429 "
                  f"(Retry-After: {retry_after}), a fresh client id still got 200")


def check_cors(base: str, limit: int) -> tuple[bool, str]:
    """Preflight allows the custom headers; actual responses expose Retry-After.

    These are deliberately checked on different responses. Starlette sends
    Access-Control-Expose-Headers on real responses only, never on the preflight,
    so looking for it in the OPTIONS reply would fail a correct service.
    """
    origin = "https://study.iitm.ac.in"
    status, headers, _ = call("OPTIONS", f"{base}/orders", {
        "Origin": origin,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "idempotency-key,x-client-id,content-type",
    })
    if status not in (200, 204):
        return False, f"preflight returned {status}"
    allow_origin = headers.get("access-control-allow-origin")
    if allow_origin not in ("*", origin):
        return False, f"preflight allowed origin {allow_origin!r}"
    allowed_headers = headers.get("access-control-allow-headers", "").lower()
    for needed in ("idempotency-key", "x-client-id"):
        if needed not in allowed_headers and "*" not in allowed_headers:
            return False, f"preflight did not allow the {needed} header"

    _s, get_headers, _ = call("GET", f"{base}/orders?limit=1",
                              {"Origin": origin, "X-Client-Id": "sim-cors-get"})
    exposed = get_headers.get("access-control-expose-headers", "").lower()
    if "retry-after" not in exposed and "*" not in exposed:
        return False, f"Retry-After is not exposed to browser JS (expose-headers={exposed!r})"

    # The 429 is exactly where the browser needs to read Retry-After, so the
    # headers must survive the error path too.
    burst = f"sim-cors-429-{uuid.uuid4()}"
    for _ in range(limit + 1):
        s429, h429, _ = call("GET", f"{base}/orders?limit=1",
                             {"Origin": origin, "X-Client-Id": burst})
    if s429 != 429:
        return False, f"expected a 429 while checking CORS headers, got {s429}"
    if h429.get("access-control-allow-origin") not in ("*", origin):
        return False, "the 429 had no Access-Control-Allow-Origin"
    if "retry-after" not in h429:
        return False, "the 429 had no Retry-After header"
    return True, ("preflight allows Idempotency-Key/X-Client-Id; Retry-After exposed on "
                  "200 and 429 responses")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Q11 grader checks against a base URL.")
    parser.add_argument("base_url", help="e.g. https://my-orders.onrender.com")
    parser.add_argument("--total", type=int, default=54, help="assigned T")
    parser.add_argument("--limit", type=int, default=19, help="assigned R per 10s")
    parser.add_argument("--page", type=int, default=7, help="page size for the scan")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    results = []

    for name, fn in (
        ("1. idempotent POST", lambda: check_idempotency(base)),
        ("2. cursor pagination", lambda: check_pagination(base, args.total, args.page,
                                                          distinct_clients=True)),
        ("3. per-client rate limit", lambda: check_rate_limit(base, args.limit)),
        ("+  CORS / Retry-After exposure", lambda: check_cors(base, args.limit)),
    ):
        try:
            ok, detail = fn()
        except Exception as exc:                     # a crash is a failed check, not a crash
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        results.append(ok)
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")

    print()
    print(f"{sum(results)}/{len(results)} checks passed against {base}")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
