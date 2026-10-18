"""Simulate the Q12 grader checks against a running service, over real HTTP.

Standard library only:

    python tools/grader_sim.py http://127.0.0.1:8323
    python tools/grader_sim.py https://my-mw.onrender.com

What this adds over tests/test_main.py: real header bytes. TestClient never
proves that Access-Control-Expose-Headers actually reached the wire, or that the
X-Request-ID header survives uvicorn's encoder - and those two are exactly what
the browser-based checks depend on.

    --limit B        assigned bucket size
    --origin ORIGIN  assigned allowed origin
    --exam-origin    the origin the exam page is served from
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
import uuid

DEFAULT_OTHER_ORIGIN = "https://not-allowed.example.com"


def call(method: str, url: str, headers: dict | None = None,
         timeout: float = 20.0) -> tuple[int, dict, str]:
    req = urllib.request.Request(url, method=method)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, {k.lower(): v for k, v in exc.headers.items()}, exc.read().decode()


def check_supplied_request_id(base: str) -> tuple[bool, str]:
    """Grader check 1."""
    given = f"rid-{uuid.uuid4()}"
    status, headers, body = call("GET", f"{base}/ping",
                                 {"X-Request-ID": given, "X-Client-Id": f"sim-1-{uuid.uuid4()}"})
    if status != 200:
        return False, f"GET /ping returned {status}"
    try:
        data = json.loads(body)
    except Exception as exc:
        return False, f"body was not JSON: {exc}"
    if data.get("request_id") != given:
        return False, f"body request_id {data.get('request_id')!r} != supplied {given!r}"
    if headers.get("x-request-id") != given:
        return False, f"X-Request-ID header {headers.get('x-request-id')!r} != supplied {given!r}"
    if not data.get("email"):
        return False, "body has no email"
    return True, f"supplied id echoed in body and header ({given})"


def check_generated_request_id(base: str) -> tuple[bool, str]:
    """Grader check 2."""
    seen = set()
    for _ in range(3):
        status, headers, body = call("GET", f"{base}/ping", {"X-Client-Id": f"sim-2-{uuid.uuid4()}"})
        if status != 200:
            return False, f"GET /ping returned {status}"
        data = json.loads(body)
        rid_body, rid_header = data.get("request_id"), headers.get("x-request-id")
        if not rid_body or rid_body != rid_header:
            return False, f"body {rid_body!r} != header {rid_header!r} (or empty)"
        seen.add(rid_body)
    if len(seen) != 3:
        return False, f"generated ids were not unique across 3 calls: {seen}"
    return True, f"3 calls generated 3 unique ids, each in body and header"


def check_rate_limit(base: str, limit: int) -> tuple[bool, str]:
    """Grader check 3: 20 requests from one client id must produce a 429; a
    different client id must still succeed."""
    victim = f"sim-burst-{uuid.uuid4()}"
    codes = []
    last_headers = {}
    for _ in range(20):
        status, headers, _ = call("GET", f"{base}/ping", {"X-Client-Id": victim})
        codes.append(status)
        last_headers = headers
    if 200 not in codes:
        return False, f"every request in the burst failed: {codes}"
    if 429 not in codes:
        return False, f"20 requests from one client id produced no 429: {codes}"
    first_429 = codes.index(429) + 1
    if first_429 != limit + 1:
        return False, f"budget of {limit} spent, but the first 429 was request {first_429}"

    other = f"sim-quiet-{uuid.uuid4()}"
    status2, _, _ = call("GET", f"{base}/ping", {"X-Client-Id": other})
    if status2 != 200:
        return False, f"a fresh client id got {status2} while another was blocked"
    if "retry-after" not in last_headers:
        return False, "the 429 carried no Retry-After header"
    return True, (f"first {limit} requests 200, request {limit + 1} -> 429 "
                  f"(Retry-After: {last_headers['retry-after']}), fresh id 200")


def check_cors(base: str, origin: str, exam_origin: str, limit: int,
               other_origin: str) -> tuple[bool, str]:
    """The CORS policy, from the browser's side of the wire."""
    for allowed in (origin, exam_origin):
        status, headers, _ = call("OPTIONS", f"{base}/ping", {
            "Origin": allowed,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-request-id,x-client-id",
        })
        if status not in (200, 204):
            return False, f"preflight from {allowed} returned {status}"
        if headers.get("access-control-allow-origin") != allowed:
            return False, (f"preflight from {allowed} got ACAO "
                           f"{headers.get('access-control-allow-origin')!r}")
        allowed_headers = headers.get("access-control-allow-headers", "").lower()
        for needed in ("x-request-id", "x-client-id"):
            if needed not in allowed_headers and "*" not in allowed_headers:
                return False, f"preflight from {allowed} did not allow {needed}"

        status, headers, _ = call("GET", f"{base}/ping",
                                  {"Origin": allowed, "X-Client-Id": f"sim-cors-{uuid.uuid4()}"})
        acao = headers.get("access-control-allow-origin")
        if acao != allowed:
            return False, f"GET with Origin {allowed} got ACAO {acao!r}"
        if acao == "*":
            return False, "ACAO was a wildcard"
        exposed = headers.get("access-control-expose-headers", "").lower()
        if "x-request-id" not in exposed:
            return False, (f"X-Request-ID is not exposed to browser JS for {allowed} "
                           f"(expose-headers={exposed!r}) -> JS reads it as null")

    _s, headers, _ = call("GET", f"{base}/ping",
                          {"Origin": other_origin, "X-Client-Id": f"sim-cors-{uuid.uuid4()}"})
    if "access-control-allow-origin" in headers:
        return False, (f"a disallowed origin ({other_origin}) received ACAO "
                       f"{headers['access-control-allow-origin']!r}")

    _s, headers, _ = call("OPTIONS", f"{base}/ping", {
        "Origin": other_origin,
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "x-client-id",
    })
    if "access-control-allow-origin" in headers:
        return False, f"a disallowed origin ({other_origin}) received ACAO on preflight"

    # The 429 is what grader check 3 looks at, in a browser: without ACAO it
    # surfaces as a CORS error rather than the 429 being tested.
    burst = f"sim-cors429-{uuid.uuid4()}"
    for _ in range(limit + 1):
        status, headers, body = call("GET", f"{base}/ping",
                                     {"Origin": origin, "X-Client-Id": burst})
    if status != 429:
        return False, f"expected a 429 while checking CORS, got {status}"
    if headers.get("access-control-allow-origin") != origin:
        return False, "the 429 had no ACAO, so the browser cannot read its status"
    if "x-request-id" not in headers:
        return False, "the 429 had no X-Request-ID header"
    if not json.loads(body).get("request_id"):
        return False, "the 429 body carried no request_id"

    return True, (f"preflights OK for {origin} and {exam_origin}; ACAO exact (never '*'); "
                  f"X-Request-ID exposed; disallowed origin gets no ACAO; 429 readable")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Q12 grader checks against a base URL.")
    parser.add_argument("base_url", help="e.g. https://my-mw.onrender.com")
    parser.add_argument("--limit", type=int, default=15, help="assigned bucket size B")
    parser.add_argument("--origin", default="https://app-dt9gio.example.com",
                        help="assigned allowed origin")
    parser.add_argument("--exam-origin", default="https://exam.sanand.workers.dev",
                        help="origin the exam page is served from")
    parser.add_argument("--other-origin", default=DEFAULT_OTHER_ORIGIN,
                        help="an origin that must NOT receive ACAO")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    results = []
    for name, fn in (
        ("1. supplied X-Request-ID echoed", lambda: check_supplied_request_id(base)),
        ("2. fresh X-Request-ID generated", lambda: check_generated_request_id(base)),
        ("3. per-client rate limit", lambda: check_rate_limit(base, args.limit)),
        ("+  CORS matrix", lambda: check_cors(base, args.origin, args.exam_origin,
                                              args.limit, args.other_origin)),
    ):
        try:
            ok, detail = fn()
        except Exception as exc:                     # a crash is a failed check
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        results.append(ok)
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")

    print()
    print(f"{sum(results)}/{len(results)} checks passed against {base}")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
