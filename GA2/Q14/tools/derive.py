"""Q14 - diagnose Docker build cache misses.

The Dockerfile under diagnosis, with the given costs:

    FROM python:3.12-slim
    WORKDIR /app
    # L1
    COPY requirements.txt ./                      0s   (COPY)
    # L2
    RUN pip install --no-cache-dir -r requirements.txt
                                                 45s
    # L3
    COPY src/ ./src/                              0s   (COPY)
    # L4
    RUN python -m compileall src                 12s
    # L5
    RUN python -m unittest discover -s src       24s

The exercise rule "once a step misses cache, all later steps run again" means the
answer is fully determined by the *first* step that misses: the re-executed set
is that step and every step after it. So this module stores one judgement per
case - which step misses first, and why - and derives the step list and the
seconds from it. The judgement is the Docker knowledge; the cascade and the
arithmetic are mechanical, and a test asserts the two can never disagree.
"""

from __future__ import annotations

import json
from pathlib import Path

# (step, instruction, re-execution cost in seconds), in build order.
STEPS: list[tuple[str, str, int]] = [
    ("L1", "COPY requirements.txt ./", 0),
    ("L2", "RUN pip install --no-cache-dir -r requirements.txt", 45),
    ("L3", "COPY src/ ./src/", 0),
    ("L4", "RUN python -m compileall src", 12),
    ("L5", "RUN python -m unittest discover -s src", 24),
]

COST = {step: cost for step, _, cost in STEPS}

# case -> (first step to miss, why). None means every step is a cache hit.
FIRST_MISS: dict[str, tuple[str | None, str]] = {
    "A": (
        "L3",
        "src/app.py is a source file of the L3 COPY, so its content change is an "
        "input change to L3. requirements.txt is untouched, so L1 and L2 still hit.",
    ),
    "B": (
        "L1",
        "requirements.txt is the source of the L1 COPY, so L1 misses (a COPY step, "
        "cost zero) and the cascade takes L2 - the 45s pip install - with it.",
    ),
    "C": (
        None,
        "Only app.py's modification time changed. A COPY's cache key is built from "
        "file content, not timestamps, so nothing misses: this is the Make-vs-Docker "
        "distinction the case exists to test.",
    ),
    "D": (
        None,
        "README.md is excluded by .dockerignore, so it never enters the build context "
        "and no instruction can depend on it. Changing it is invisible to the build.",
    ),
    "E": (
        "L4",
        "The instruction text is part of its own cache key, so adding -q misses L4 "
        "regardless of the files; L5 then re-runs as a later step.",
    ),
}


def derive(first_miss: str | None) -> dict:
    """Steps from the first miss onward, and their total cost."""
    if first_miss is None:
        return {"steps": [], "seconds": 0}
    order = [step for step, _, _ in STEPS]
    rebuilt = STEPS[order.index(first_miss):]
    return {"steps": [step for step, _, _ in rebuilt],
            "seconds": sum(cost for _, _, cost in rebuilt)}


def build_answer() -> dict:
    return {case: derive(first) for case, (first, _) in FIRST_MISS.items()}


def main() -> None:
    answer = build_answer()
    print(json.dumps(answer, indent=2))
    print()
    for case, (first, why) in FIRST_MISS.items():
        print(f"{case}: first miss {first or '(none)'} - {why}")

    target = Path(__file__).resolve().parent.parent / "answer.json"
    target.write_text(json.dumps(answer, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {target} ({target.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
