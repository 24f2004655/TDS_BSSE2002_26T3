"""Contract tests for the Q12 middleware stack.

One test per grader check, then the hostile cases: an origin that must NOT get
an ACAO header, a preflight that must not spend the rate bucket, a limiter that
must be reachable at all by a browser (i.e. its 429 must be readable through
CORS), and the two concurrency properties that a status code cannot show.

tools/mutate_q12.py injects the plausible wrong versions of each layer and
reports which of them this file misses.

Run:  pytest -q
"""

from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

import main as svc
from main import ASSIGNED_ORIGIN, EMAIL, EXAM_ORIGIN, RATE_LIMIT, app

OTHER_ORIGIN = "https://evil.example.com"
PAGE_ORIGINS = (ASSIGNED_ORIGIN, EXAM_ORIGIN)


@pytest.fixture(autouse=True)
def fresh_bucket():
    svc.BUCKET.clear()
    yield
    svc.BUCKET.clear()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def cid(name: str) -> dict:
    return {"X-Client-Id": name}


# --------------------------------------------------------------------------- #
# grader check 1 - supplied X-Request-ID is reused
# --------------------------------------------------------------------------- #


def test_grader_check_1_supplied_request_id_lands_in_body_and_header(client):
    given = "rid-abc-123"
    r = client.get("/ping", headers={**cid("check1"), "X-Request-ID": given})

    assert r.status_code == 200
    assert r.json()["request_id"] == given
    assert r.headers["x-request-id"] == given


def test_ping_body_has_exactly_the_two_documented_keys(client):
    """Guards against an exact-equality grader: no extra keys in the payload."""
    r = client.get("/ping", headers=cid("shape"))
    assert set(r.json()) == {"email", "request_id"}
    assert r.json()["email"] == EMAIL


def test_an_awkward_but_legal_request_id_is_preserved_verbatim(client):
    for given in ("req id:42/a~b!c", "x" * 200, "0" * 1):
        r = client.get("/ping", headers={**cid("awkward"), "X-Request-ID": given})
        assert r.json()["request_id"] == given
        assert r.headers["x-request-id"] == given


def test_an_empty_request_id_header_is_treated_as_absent(client):
    r = client.get("/ping", headers={**cid("empty-id"), "X-Request-ID": ""})
    assert r.status_code == 200
    assert r.json()["request_id"], "empty inbound id must not be reused"


# --------------------------------------------------------------------------- #
# grader check 2 - a fresh id is generated
# --------------------------------------------------------------------------- #


def test_grader_check_2_generated_id_appears_in_body_and_header(client):
    r = client.get("/ping", headers=cid("check2"))
    body = r.json()

    assert r.status_code == 200
    assert body["request_id"]
    assert r.headers["x-request-id"] == body["request_id"]


def test_generated_ids_are_unique_per_request(client):
    ids = {client.get("/ping", headers=cid("unique")).json()["request_id"] for _ in range(6)}
    assert len(ids) == 6, "generated ids repeated"


def test_a_supplied_id_is_reused_rather_than_regenerated(client):
    """The other half of check 1: adopting must not fall back to generating."""
    given = "stable-id-42"
    seen = {client.get("/ping", headers={**cid("reuse"), "X-Request-ID": given}
                       ).json()["request_id"] for _ in range(3)}
    assert seen == {given}


# --------------------------------------------------------------------------- #
# grader check 3 - per-client rate limiting
# --------------------------------------------------------------------------- #


def test_grader_check_3_B_requests_pass_and_the_next_is_429(client):
    h = cid("burst")
    codes = [client.get("/ping", headers=h).status_code for _ in range(RATE_LIMIT)]
    assert set(codes) == {200}, codes
    assert client.get("/ping", headers=h).status_code == 429


def test_twenty_requests_from_one_client_contain_a_429(client):
    """The grader's literal wording: 20 requests, a 429 must appear."""
    h = cid("twenty")
    codes = [client.get("/ping", headers=h).status_code for _ in range(20)]
    assert codes[0] == 200
    assert 429 in codes
    assert codes[-1] == 429


def test_429_carries_retry_after_and_the_request_id(client):
    h = {**cid("retry"), "X-Request-ID": "given-on-429"}
    for _ in range(RATE_LIMIT):
        client.get("/ping", headers=h)
    r = client.get("/ping", headers=h)

    assert r.status_code == 429
    assert 0 < float(r.headers["retry-after"]) <= 10
    # The context layer is outermost, so an error response carries the id too.
    assert r.json()["request_id"] == "given-on-429"
    assert r.headers["x-request-id"] == "given-on-429"


def test_a_fresh_client_id_is_still_accepted_while_one_is_blocked(client):
    noisy = cid("noisy")
    for _ in range(RATE_LIMIT + 1):
        last = client.get("/ping", headers=noisy).status_code
    assert last == 429

    assert client.get("/ping", headers=cid("quiet")).status_code == 200


def test_buckets_are_independent_per_client(client):
    for name in ("c-1", "c-2"):
        codes = [client.get("/ping", headers=cid(name)).status_code for _ in range(RATE_LIMIT)]
        assert set(codes) == {200}, name
    assert client.get("/ping", headers=cid("c-3")).status_code == 200


def test_missing_client_id_still_serves(client):
    assert client.get("/ping").status_code == 200


def test_rate_limiter_is_a_layer_not_a_ping_only_rule(client):
    """It is middleware: requests to any path spend the same client's bucket."""
    h = cid("all-paths")
    for _ in range(RATE_LIMIT):
        client.get("/", headers=h)
    assert client.get("/ping", headers=h).status_code == 429


def test_preflights_do_not_spend_the_bucket(client):
    """A browser sends OPTIONS before every fetch. If the limiter sits outside
    CORS, 20 fetches spend 40 tokens and trip at request 8."""
    h = {"Origin": ASSIGNED_ORIGIN, "Access-Control-Request-Method": "GET",
         "Access-Control-Request-Headers": "x-client-id", **cid("browser")}
    for _ in range(RATE_LIMIT + 10):
        assert client.options("/ping", headers=h).status_code == 200

    codes = [client.get("/ping", headers=cid("browser")).status_code for _ in range(RATE_LIMIT)]
    assert set(codes) == {200}, f"preflights were charged to the bucket: {codes}"
    assert client.get("/ping", headers=cid("browser")).status_code == 429


def test_bucket_drains_while_the_client_keeps_retrying(client, monkeypatch):
    """Retry fast: a test that sleeps past the window would let a limiter that
    records its own rejections pass."""
    monkeypatch.setattr(svc.BUCKET, "window", 0.4)
    h = cid("hammer")
    for _ in range(RATE_LIMIT):
        assert client.get("/ping", headers=h).status_code == 200
    assert client.get("/ping", headers=h).status_code == 429

    started = time.monotonic()
    rejections = 0
    recovered_after = None
    while time.monotonic() - started < 3.0:
        if client.get("/ping", headers=h).status_code == 200:
            recovered_after = time.monotonic() - started
            break
        rejections += 1

    assert recovered_after is not None, f"never recovered after {rejections} retries"
    assert rejections >= 1, "no lockout was observed, so the test proved nothing"


def test_hit_is_mutually_exclusive():
    """Deterministic mutual-exclusion test - and the honest one.

    Racing threads cannot prove this. hit()'s critical section is a couple of
    bytecodes between the length check and the append, so CPython's ~1us switch
    timer essentially never fires inside it: measured 20 rounds x 24 threads, an
    unlocked bucket never overshot its limit once. A green race test would
    therefore be evidence of nothing.

    So assert the operational definition instead: while one caller holds the
    critical section, no other caller can complete. That fails immediately and
    deterministically if the lock is removed.
    """
    bucket = svc.SlidingWindow(limit=5, window=60.0)
    finished = threading.Event()

    def worker() -> None:
        bucket.hit("shared")
        finished.set()

    bucket._lock.acquire()
    try:
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        assert not finished.wait(timeout=0.3), "hit() completed while the lock was held"
    finally:
        bucket._lock.release()

    assert finished.wait(timeout=5), "hit() never completed after the lock was released"
    thread.join(timeout=5)


def test_hit_counts_exactly_the_limit_under_concurrent_callers():
    """Smoke test under load, not the lock detector.

    A missing lock survives this (see test_hit_is_mutually_exclusive); what it
    does catch is a counting mistake that only shows up when many callers arrive
    at once.
    """
    n = 24
    bucket = svc.SlidingWindow(limit=RATE_LIMIT, window=60.0)
    barrier = threading.Barrier(n)
    results: list[bool] = []
    lock = threading.Lock()

    def worker() -> None:
        barrier.wait()
        allowed, _ = bucket.hit("shared")
        with lock:
            results.append(allowed)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert len(results) == n
    assert sum(results) == RATE_LIMIT, f"{sum(results)} requests allowed, limit {RATE_LIMIT}"


# --------------------------------------------------------------------------- #
# middleware 2 - scoped CORS
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("origin", PAGE_ORIGINS)
def test_preflight_succeeds_for_each_allowed_origin(client, origin):
    r = client.options("/ping", headers={
        "Origin": origin,
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "x-request-id,x-client-id",
    })
    assert r.status_code in (200, 204)
    assert r.headers["access-control-allow-origin"] == origin
    allowed = r.headers["access-control-allow-headers"].lower()
    for needed in ("x-request-id", "x-client-id"):
        assert needed in allowed or "*" in allowed


@pytest.mark.parametrize("origin", PAGE_ORIGINS)
def test_acao_is_the_exact_origin_never_a_wildcard(client, origin):
    r = client.get("/ping", headers={**cid("acao"), "Origin": origin})
    assert r.headers["access-control-allow-origin"] == origin
    assert r.headers["access-control-allow-origin"] != "*"


@pytest.mark.parametrize("origin", PAGE_ORIGINS)
def test_request_id_header_is_exposed_to_browser_js(client, origin):
    """X-Request-ID is not a CORS-safelisted response header. Without
    Access-Control-Expose-Headers the browser reports it as null and grader
    checks 1 and 2 fail against a correct service."""
    exposed = client.get("/ping", headers={**cid("expose"), "Origin": origin},
                         ).headers.get("access-control-expose-headers", "").lower()
    assert "x-request-id" in exposed


def test_a_disallowed_origin_receives_no_acao(client):
    r = client.get("/ping", headers={**cid("other"), "Origin": OTHER_ORIGIN})
    assert r.status_code == 200
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}


def test_a_preflight_from_a_disallowed_origin_receives_no_acao(client):
    r = client.options("/ping", headers={
        "Origin": OTHER_ORIGIN,
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "x-client-id",
    })
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}


def test_a_request_with_no_origin_is_served_without_cors_headers(client):
    r = client.get("/ping", headers=cid("curl"))
    assert r.status_code == 200
    assert r.json()["request_id"]


def test_the_429_is_readable_from_the_page(client):
    """Check 3 runs in a browser, so a 429 without ACAO surfaces as a CORS error
    instead of the 429 the grader is looking for."""
    h = {**cid("browser-429"), "Origin": ASSIGNED_ORIGIN}
    for _ in range(RATE_LIMIT + 1):
        r = client.get("/ping", headers=h)
    assert r.status_code == 429
    assert r.headers["access-control-allow-origin"] == ASSIGNED_ORIGIN
    assert "x-request-id" in r.headers.get("access-control-expose-headers", "").lower()


def test_allowed_origins_are_configurable_and_default_to_the_assigned_pair():
    assert ASSIGNED_ORIGIN == "https://app-dt9gio.example.com"
    assert EXAM_ORIGIN == "https://exam.sanand.workers.dev"
    assert svc.ALLOWED_ORIGINS == [ASSIGNED_ORIGIN, EXAM_ORIGIN]


# --------------------------------------------------------------------------- #
# middleware 1 - context is published on every response, not just /ping
# --------------------------------------------------------------------------- #


def test_every_response_carries_the_request_id_header(client):
    """Preflight, health and 429 all pass back through the context layer."""
    responses = [
        client.get("/ping", headers=cid("ctx")),
        client.get("/", headers=cid("ctx")),
        client.options("/ping", headers={"Origin": ASSIGNED_ORIGIN,
                                         "Access-Control-Request-Method": "GET"}),
    ]
    for r in responses:
        assert r.headers.get("x-request-id"), "a response left without X-Request-ID"


def test_health_endpoint_reports_configuration(client):
    body = client.get("/", headers=cid("health")).json()
    assert body["email"] == EMAIL
    assert body["rate_limit"] == RATE_LIMIT
    assert body["allowed_origins"] == [ASSIGNED_ORIGIN, EXAM_ORIGIN]
