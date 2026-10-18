"""Tests for the Q14 cache-miss answer.

The expected table is stated here explicitly, derived from the Docker cache rules
by hand rather than from the implementation - so a wrong judgement in
tools/derive.py has to argue with this file, not with itself. The remaining tests
assert the two things that must never drift apart: the listed steps and the
seconds, and the shape of the served JSON.

Run:  pytest -q
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.derive import COST, FIRST_MISS, STEPS, build_answer, derive

ANSWER_PATH = Path(__file__).resolve().parent.parent / "answer.json"
BUILD_ORDER = [step for step, _, _ in STEPS]

# Derived by hand from Docker's cache-invalidation rules.
EXPECTED = {
    "A": {"steps": ["L3", "L4", "L5"], "seconds": 36},
    "B": {"steps": ["L1", "L2", "L3", "L4", "L5"], "seconds": 81},
    "C": {"steps": [], "seconds": 0},
    "D": {"steps": [], "seconds": 0},
    "E": {"steps": ["L4", "L5"], "seconds": 36},
}


@pytest.mark.parametrize("case", ["A", "B", "C", "D", "E"])
def test_case_matches_the_hand_derived_table(case):
    assert build_answer()[case] == EXPECTED[case]


# --------------------------------------------------------------------------- #
# the rules themselves
# --------------------------------------------------------------------------- #


def test_a_miss_takes_every_later_step_with_it():
    for first in BUILD_ORDER:
        steps = derive(first)["steps"]
        assert steps == BUILD_ORDER[BUILD_ORDER.index(first):], first


def test_no_miss_rebuilds_nothing():
    assert derive(None) == {"steps": [], "seconds": 0}


def test_the_earliest_miss_is_what_counts():
    """The rule makes later misses irrelevant, so derive() only needs the first."""
    assert derive("L1") == {"steps": BUILD_ORDER, "seconds": 81}
    assert derive("L5") == {"steps": ["L5"], "seconds": 24}


def test_seconds_always_equals_the_cost_of_the_listed_steps():
    for case in EXPECTED:
        result = build_answer()[case]
        assert result["seconds"] == sum(COST[step] for step in result["steps"]), case


def test_reported_seconds_never_count_copy_steps():
    for case in EXPECTED:
        result = build_answer()[case]
        for step in result["steps"]:
            if step in ("L1", "L3"):
                assert COST[step] == 0, f"{case} charges for COPY {step}"


def test_exactly_two_cases_are_all_cache_hits():
    """C and D are the all-cache-hit cases, and C is not a duplicate of A.

    If a mtime touch invalidated L3, case C would list the same steps as case A
    and would be a pointless duplicate - which is itself evidence that C is meant
    to be empty.
    """
    empty = [case for case, value in build_answer().items() if value["steps"] == []]
    assert empty == ["C", "D"]
    assert EXPECTED["A"] != EXPECTED["C"]


def test_every_case_covers_only_steps_in_build_order():
    for case, value in build_answer().items():
        listed = [BUILD_ORDER.index(step) for step in value["steps"]]
        assert listed == sorted(listed), case
        assert all(step in BUILD_ORDER for step in value["steps"]), case


def test_every_case_has_a_stated_reason():
    for case, (first, why) in FIRST_MISS.items():
        assert why and len(why) > 40, f"{case} has no real justification"
        assert first is None or first in BUILD_ORDER


# --------------------------------------------------------------------------- #
# the served artifact
# --------------------------------------------------------------------------- #


def test_served_answer_matches_the_derivation():
    assert json.loads(ANSWER_PATH.read_text(encoding="utf-8")) == build_answer()


def test_served_shape_is_exactly_as_documented():
    served = json.loads(ANSWER_PATH.read_text(encoding="utf-8"))
    assert set(served) == set("ABCDE")
    for case, value in served.items():
        assert set(value) == {"steps", "seconds"}, case
        assert isinstance(value["seconds"], int), case
        assert isinstance(value["steps"], list), case
        assert all(isinstance(step, str) for step in value["steps"]), case


def test_served_answer_is_within_the_grader_limits():
    assert ANSWER_PATH.stat().st_size < 64 * 1024
