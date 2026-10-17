"""Mutation check for the Q11 test suite.

"37 passed" only proves the tests run. This asks the harder question: does the
suite actually *fail* when the implementation is wrong? Each entry below is a
plausible wrong implementation - the kind a hurried solution would contain -
applied to main.py as a text substitution. A mutation that still passes the
whole suite is a hole in the tests, reported as SURVIVED.

Usage (from the Q11 directory):
    python tools/mutate_q11.py
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "main.py"

# (label, what it gets wrong, old_text, new_text)
MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "idempotency-unlocked",
        "no mutual exclusion around read-then-create",
        """        with self._lock:
            existing = self._orders.get(key)
            if existing is not None:
                return existing, False
            order = build()
            self._orders[key] = order
            return order, True""",
        """        existing = self._orders.get(key)
        if existing is not None:
            return existing, False
        order = build()
        self._orders[key] = order
        return order, True""",
    ),
    (
        "idempotency-always-creates",
        "ignores the stored key and mints a new order on every call",
        "            if existing is not None:\n                return existing, False",
        "            if False:\n                return existing, False",
    ),
    (
        "ratelimit-off-by-one",
        "allows R+1 requests per window instead of R",
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
        "429-without-retry-after",
        "returns 429 with no Retry-After header",
        '            headers={"Retry-After": str(max(1, math.ceil(retry_after)))},',
        '            headers={},',
    ),
    (
        "cursor-off-by-one",
        "advances the cursor one id too far, creating gaps",
        "    next_id = start + len(page)",
        "    next_id = start + len(page) + 1",
    ),
    (
        "page-ignores-limit",
        "returns the whole remaining catalog regardless of limit",
        "    page = CATALOG[start - 1:start - 1 + limit]",
        "    page = CATALOG[start - 1:]",
    ),
    (
        "cursor-returns-itself",
        "emits a next cursor even on the last page (a naive caller loops forever)",
        "    nxt = encode_cursor(next_id) if more else None",
        "    nxt = encode_cursor(next_id)",
    ),
    (
        "client-id-ignored",
        "buckets by caller identity instead of the X-Client-Id header",
        '    client = (request.headers.get("X-Client-Id") or "").strip()',
        '    client = ""',
    ),
    (
        "cors-hides-retry-after",
        "does not expose Retry-After, so browser JS cannot read it",
        '    expose_headers=["Retry-After", "*"],',
        "    expose_headers=[],",
    ),
]


def original_source() -> str:
    return TARGET.read_text(encoding="utf-8")


def run_suite() -> tuple[bool, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-x", "--no-header", "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True, timeout=600,
    )
    lines = (proc.stdout or proc.stderr).strip().splitlines()
    # Prefer the name of the test that failed over the counts line.
    failed = [ln for ln in lines if ln.startswith("FAILED") or ln.startswith("ERROR")]
    summary = failed[0] if failed else (lines[-1] if lines else f"exit {proc.returncode}")
    return proc.returncode == 0, summary.strip()


def main() -> int:
    pristine = original_source()
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
