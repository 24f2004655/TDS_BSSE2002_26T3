"""Tests for the Q13 stream reassembler.

The expected strings for A, B and C are not taken from the implementation - they
were decoded by hand from the hex before the code was written, so this file is an
independent check on it rather than a restatement. The synthetic cases then pin
each rule from the spec individually, including the ones the assigned data
happens not to exercise in isolation.

Run:  pytest -q
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.reassemble import CHUNKS, build_answer, reassemble

ANSWER_PATH = Path(__file__).resolve().parent.parent / "answer.json"

# Hand-decoded from the captured hex.
HAND_DECODED = {
    "A": {
        "text": "Report job-ebf9f249: café\nReady ✓",
        "complete": True,
        "error": None,
    },
    "B": {
        "text": "Partial job-ebf9f249",
        "complete": False,
        "error": None,
    },
    "C": {
        "text": "Draft job-ebf9f249",
        "complete": False,
        "error": "model unloaded",
    },
}


def hexed(text: str) -> str:
    return text.encode("utf-8").hex()


def stream(*records: str, split: int | None = None) -> list[str]:
    """NDJSON text as one chunk, or split into two at a byte offset."""
    raw = "".join(r + "\n" for r in records).encode("utf-8")
    if split is None:
        return [raw.hex()]
    return [raw[:split].hex(), raw[split:].hex()]


# --------------------------------------------------------------------------- #
# the assigned streams
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", ["A", "B", "C"])
def test_assigned_stream_matches_the_hand_decode(name):
    assert reassemble(CHUNKS[name]).as_json() == HAND_DECODED[name]


def test_full_answer_shape():
    answer = build_answer()
    assert set(answer) == {"A", "B", "C"}
    for value in answer.values():
        assert set(value) == {"text", "complete", "error"}


def test_committed_answer_file_matches_the_reassembler():
    """The served artifact must not drift from the code that derives it."""
    assert json.loads(ANSWER_PATH.read_text(encoding="utf-8")) == build_answer()


def test_answer_file_is_within_the_grader_limits():
    size = ANSWER_PATH.stat().st_size
    assert size < 64 * 1024, f"{size} bytes exceeds the 64 KiB limit"


# --------------------------------------------------------------------------- #
# A: unicode across boundaries, escapes, and a done record carrying text
# --------------------------------------------------------------------------- #


def test_multibyte_character_split_across_two_chunks():
    """U+00E9 inside 'café' is c3 | a9, split by a chunk boundary."""
    chunks = CHUNKS["A"]
    assert any(c.endswith("c3") for c in chunks), "test premise: the split is present"
    assert "café" in reassemble(chunks).text


def test_three_byte_character_split_across_two_chunks():
    """U+2713 CHECK MARK is e2 | 9c93 - the first byte alone is a lone
    continuation byte, which would corrupt under chunk-at-a-time decoding."""
    assert "✓" in reassemble(CHUNKS["A"]).text


def test_escaped_newline_is_preserved_as_a_real_newline():
    text = reassemble(CHUNKS["A"]).text
    assert "café\nReady" in text


def test_trailing_space_in_a_response_is_preserved():
    """'Preserve spaces exactly': the first record ends 'job-ebf9f249: '."""
    assert reassemble(CHUNKS["A"]).text.startswith("Report job-ebf9f249: ")


def test_thinking_text_is_ignored():
    assert "internal note" not in reassemble(CHUNKS["A"]).text


def test_response_on_the_done_record_is_included():
    assert reassemble(CHUNKS["A"]).text.endswith("Ready ✓")


# --------------------------------------------------------------------------- #
# B and C: the two non-success endings
# --------------------------------------------------------------------------- #


def test_incomplete_trailing_record_is_ignored():
    result = reassemble(CHUNKS["B"])
    assert result.text == "Partial job-ebf9f249"
    assert "unfinished" not in result.text


def test_eof_without_a_terminal_record_is_incomplete_and_errorless():
    result = reassemble(CHUNKS["B"])
    assert result.complete is False
    assert result.error is None


def test_error_record_stops_the_stream_and_ignores_later_records():
    result = reassemble(CHUNKS["C"])
    assert result.error == "model unloaded"
    assert result.complete is False
    assert "IGNORE" not in result.text


def test_error_record_preserves_earlier_response_text():
    assert reassemble(CHUNKS["C"]).text == "Draft job-ebf9f249"


# --------------------------------------------------------------------------- #
# the rules, on synthetic streams
# --------------------------------------------------------------------------- #


def test_a_valid_but_unterminated_final_record_is_dropped():
    """NDJSON records count only once their newline arrives."""
    raw = b'{"response":"kept"}\n{"response":"no newline"}'
    result = reassemble([raw.hex()])
    assert result.text == "kept"
    assert result.complete is False


def test_a_four_byte_character_split_across_three_chunks():
    """A 4-byte code point spread over three chunks, one byte each."""
    text = "ok 🙂 done"
    payload = b'{"response":"' + text.encode("utf-8") + b'"}\n'
    at = payload.index("🙂".encode("utf-8"))
    chunks = [
        payload[: at + 1].hex(),
        payload[at + 1: at + 3].hex(),
        payload[at + 3:].hex(),
    ]
    result = reassemble(chunks)
    assert result.text == text
    assert result.complete is False  # no done record
    assert result.skipped_records == 0, "a split code point must not break parsing"


def test_escape_sequence_split_across_chunks():
    """A backslash and the 'n' it escapes landing in different chunks."""
    raw = b'{"response":"line1\\nline2"}\n'
    result = reassemble([raw[: raw.index(b"\\") + 1].hex(), raw[raw.index(b"\\") + 1:].hex()])
    assert result.text == "line1\nline2"


def test_thinking_only_record_contributes_nothing():
    result = reassemble(stream('{"thinking":"hmm","done":false}',
                               '{"response":"answer","done":true}'))
    assert result.text == "answer"
    assert result.complete is True


def test_done_true_with_response_terminates_and_keeps_the_text():
    result = reassemble(stream('{"response":"a"}', '{"response":"b","done":true}',
                               '{"response":"after"}'))
    assert result.text == "ab"
    assert result.complete is True
    assert "after" not in result.text


def test_error_record_takes_precedence_and_its_own_response_is_not_added():
    """The spec says preserve *earlier* text on error, but explicitly says to
    include the done record's own text - so the contrast is treated as deliberate."""
    result = reassemble(stream('{"response":"before"}',
                               '{"response":"on-error","error":"boom"}'))
    assert result.text == "before"
    assert result.error == "boom"
    assert result.complete is False


def test_error_after_done_never_happens_because_done_stops_first():
    result = reassemble(stream('{"response":"a","done":true}', '{"error":"late"}'))
    assert result.text == "a"
    assert result.complete is True
    assert result.error is None


def test_a_record_that_is_not_an_object_is_skipped_not_fatal():
    result = reassemble(stream("[1,2,3]", '{"response":"kept","done":true}'))
    assert result.text == "kept"
    assert result.complete is True
    assert result.skipped_records == 1


def test_lone_continuation_bytes_never_reach_the_text():
    """A truncated multi-byte character in a *complete* record is a decode error,
    which must not crash the reassembler or leak a replacement character."""
    raw = b'{"response":"\xe2\x9c"}\n{"response":"ok","done":true}\n'
    result = reassemble([raw.hex()])
    assert result.text == "ok"
    assert "\ufffd" not in result.text


def test_empty_stream():
    result = reassemble([])
    assert result.as_json() == {"text": "", "complete": False, "error": None}


def test_multiple_newlines_in_one_chunk_are_all_processed():
    result = reassemble(stream('{"response":"1"}', '{"response":"2"}',
                               '{"response":"3","done":true}'))
    assert result.text == "123"
