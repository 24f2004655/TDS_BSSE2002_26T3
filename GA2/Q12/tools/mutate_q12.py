"""Mutation check for the Q12 test suite.

Each entry is a plausible wrong version of one middleware layer, applied to
main.py as a text substitution. A mutation that still passes the whole suite is
a hole in the tests, reported as SURVIVED. The middleware-order mutation is the
interesting one: reversing two layers leaves every status code unchanged and
only a test that counts OPTIONS catches it.

Usage (from the Q12 directory):
    python tools/mutate_q12.py
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "main.py"

MIDDLEWARE_BLOCK = """app.add_middleware(ClientRateLimitMiddleware, bucket=BUCKET)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,          # explicit list: never emits "*"
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],                    # preflight echoes what the page asks for
    # Without this the browser cannot read X-Request-ID, and the grader's
    # body-vs-header comparison fails on a correct service.
    expose_headers=["X-Request-ID", "Retry-After"],
    max_age=600,
)
app.add_middleware(RequestContextMiddleware)"""

MUTATIONS: list[tuple[str, str, str, str]] = [
    # --- middleware 1: request context ------------------------------------- #
    (
        "context-ignores-inbound",
        "always generates, ignoring the X-Request-ID the caller supplied",
        "        request_id = clean_request_id(incoming) or str(uuid.uuid4())",
        "        request_id = str(uuid.uuid4())",
    ),
    (
        "context-constant-generated-id",
        "generates the same id for every request instead of a unique one",
        "        request_id = clean_request_id(incoming) or str(uuid.uuid4())",
        '        request_id = clean_request_id(incoming) or "fixed-id"',
    ),
    (
        "context-no-response-header",
        "puts the id in the body but never in the response header",
        '            if message["type"] == "http.response.start":\n'
        '                MutableHeaders(scope=message)["X-Request-ID"] = request_id',
        '            if False:\n'
        '                MutableHeaders(scope=message)["X-Request-ID"] = request_id',
    ),
    # --- middleware 2: CORS ------------------------------------------------ #
    (
        "cors-wildcard-origin",
        'answers every origin with ACAO: "*"',
        '    allow_origins=ALLOWED_ORIGINS,          # explicit list: never emits "*"',
        '    allow_origins=["*"],',
    ),
    (
        "cors-drops-the-exam-origin",
        "allows only the assigned origin, so the exam page's browser checks fail",
        "ALLOWED_ORIGINS = [o for o in (ASSIGNED_ORIGIN, EXAM_ORIGIN) if o]",
        "ALLOWED_ORIGINS = [ASSIGNED_ORIGIN]",
    ),
    (
        "cors-hides-request-id",
        "does not expose X-Request-ID, so JS reads it as null",
        '    expose_headers=["X-Request-ID", "Retry-After"],',
        '    expose_headers=["Retry-After"],',
    ),
    # --- middleware 3: rate limiting --------------------------------------- #
    (
        "ratelimit-off-by-one",
        "allows B+1 requests per window",
        "            if len(q) >= self.limit:",
        "            if len(q) > self.limit:",
    ),
    (
        "ratelimit-one-global-bucket",
        "all clients share one bucket",
        '            q = self._hits.setdefault(key, deque())',
        '            q = self._hits.setdefault("__global__", deque())',
    ),
    (
        "ratelimit-counts-rejects",
        "records rejected hits too, so a retrying client refills its own bucket",
        "                return False, max(0.0, self.window - (now - q[0]))",
        "                q.append(now)\n                return False, max(0.0, self.window - (now - q[0]))",
    ),
    (
        "ratelimit-unlocked",
        "no mutual exclusion between the count and the append",
        """        now = time.monotonic()
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and q[0] <= now - self.window:
                q.popleft()
            if len(q) >= self.limit:
                # Rejected hits are not recorded: otherwise a client retrying
                # faster than the window drains refills its own bucket forever.
                return False, max(0.0, self.window - (now - q[0]))
            q.append(now)
            return True, 0.0""",
        """        now = time.monotonic()
        q = self._hits.setdefault(key, deque())
        while q and q[0] <= now - self.window:
            q.popleft()
        if len(q) >= self.limit:
            return False, max(0.0, self.window - (now - q[0]))
        q.append(now)
        return True, 0.0""",
    ),
    (
        "ratelimit-no-retry-after",
        "429 with no Retry-After header",
        '                headers={"Retry-After": str(max(1, math.ceil(retry_after)))},',
        "                headers={},",
    ),
    # --- composition ------------------------------------------------------- #
    (
        "rate-limiter-outside-cors",
        "middleware order reversed: preflights now spend the bucket",
        MIDDLEWARE_BLOCK,
        """app.add_middleware(RequestContextMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "Retry-After"],
    max_age=600,
)
app.add_middleware(ClientRateLimitMiddleware, bucket=BUCKET)""",
    ),
    # --- response shape ---------------------------------------------------- #
    (
        "ping-extra-keys",
        "adds an undocumented key to the /ping body",
        '    return {"email": EMAIL, "request_id": request_id_ctx.get()}',
        '    return {"email": EMAIL, "request_id": request_id_ctx.get(), "extra": 1}',
    ),
]


def run_suite() -> tuple[bool, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-x", "--no-header", "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True, timeout=600,
    )
    lines = (proc.stdout or proc.stderr).strip().splitlines()
    failed = [ln for ln in lines if ln.startswith("FAILED") or ln.startswith("ERROR")]
    summary = failed[0] if failed else (lines[-1] if lines else f"exit {proc.returncode}")
    return proc.returncode == 0, summary.strip()


def main() -> int:
    pristine = TARGET.read_text(encoding="utf-8")
    pristine_hash = hashlib.sha256(pristine.encode()).hexdigest()

    ok, summary = run_suite()
    print(f"baseline (unmutated): {'PASS' if ok else 'FAIL'} - {summary}")
    if not ok:
        print("Baseline is failing; fix that before trusting mutation results.")
        return 2

    caught, survived, skipped = [], [], []
    try:
        for label, description, old, new in MUTATIONS:
            if pristine.count(old) != 1:
                skipped.append(label)
                print(f"SKIP    {label}: anchor text found {pristine.count(old)} times")
                continue
            TARGET.write_text(pristine.replace(old, new), encoding="utf-8")
            try:
                passed, mutation_summary = run_suite()
            finally:
                TARGET.write_text(pristine, encoding="utf-8")
            (survived if passed else caught).append(label)
            print(f"{'SURVIVED' if passed else 'caught  '} {label:28s} {description}")
            print(f"         -> {mutation_summary}")
    finally:
        TARGET.write_text(pristine, encoding="utf-8")

    restored = hashlib.sha256(TARGET.read_text(encoding="utf-8").encode()).hexdigest()
    print()
    print(f"caught   {len(caught)}/{len(MUTATIONS)}")
    if survived:
        print(f"SURVIVED {len(survived)}: {', '.join(survived)}  <- holes in the suite")
    if skipped:
        print(f"skipped  {len(skipped)}: {', '.join(skipped)}")
    print(f"main.py restored unchanged: {restored == pristine_hash}")
    return 1 if survived or skipped else 0


if __name__ == "__main__":
    raise SystemExit(main())
