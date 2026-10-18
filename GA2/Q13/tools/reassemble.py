"""Q13 - recover a fragmented Ollama-style NDJSON stream.

The captured byte chunks, the decoder, and the answer generator in one place so
`python tools/reassemble.py --write` regenerates answer.json from scratch.

The failure being modelled: a client that treats every network chunk as a whole
JSON object, and that decodes each chunk as text independently. Here the chunks
are hex-encoded *bytes*, and a record only becomes parseable once its terminating
newline arrives - so the buffer is bytes, and nothing is decoded to text until a
complete line exists.

Hand decode of stream A, used as an independent check on this code:

    7b22726573706f6e736522             | {"response"
    3a225265706f7274206a6f622d65626639663234393a | :"Report job-ebf9f249:
    20222c22646f6e65223a66616c73657d0a7b |  ","done":false}\\n{"
    ...
    e2 | 9c93                          | U+2713 CHECK MARK split across two chunks
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

CHUNKS: dict[str, list[str]] = {
    "A": [
        "7b22726573706f6e736522",
        "3a225265706f7274206a6f622d65626639663234393a",
        "20222c22646f6e65223a66616c73657d0a7b",
        "227468696e6b696e67223a22696e7465726e616c206e6f",
        "7465222c22726573706f6e7365223a22222c2264",
        "6f6e65223a66616c73657d0a7b22",
        "726573706f6e7365223a22636166c3",
        "a95c6e222c22646f6e65223a66616c7365",
        "7d0a7b22726573706f6e7365223a22526561647920",
        "e2",
        "9c93222c22646f6e65223a747275652c22",
        "646f6e655f726561736f6e223a2273746f70227d0a",
    ],
    "B": [
        "7b22726573706f6e736522",
        "3a225061727469616c206a6f622d6562663966323439",
        "222c22646f6e65223a66616c73657d0a7b22",
        "726573706f6e7365223a22756e66696e6973686564",
    ],
    "C": [
        "7b22726573706f6e736522",
        "3a224472616674206a6f622d6562663966323439222c",
        "22646f6e65223a66616c73657d0a7b226572",
        "726f72223a226d6f64656c20756e6c6f61646564227d0a",
        "7b22726573706f6e7365223a2249474e4f524522",
        "2c22646f6e65223a747275657d0a",
    ],
}


@dataclass
class Reassembled:
    text: str
    complete: bool
    error: str | None
    skipped_records: int = 0

    def as_json(self) -> dict:
        """Exactly the three documented keys - no extras, in case of an
        exact-equality comparison."""
        return {"text": self.text, "complete": self.complete, "error": self.error}


def reassemble(chunks: list[str]) -> Reassembled:
    """Replay one stream's byte chunks into a single result.

    Rules, in the order they are applied to each complete record:

    1. ``error`` is terminal: stop with ``complete = False`` and that string.
       The error record's own ``response`` is NOT appended - the spec says to
       preserve *earlier* text, and says the opposite ("include any response
       text") for the ``done`` case, so the contrast looks deliberate.
    2. ``response`` is appended verbatim, spaces and escapes included. ``thinking``
       is ignored: it is a different field, not a different record.
    3. ``done: true`` is terminal: append its response first (a done record may
       still carry the last piece of text), then stop with ``complete = True``.

    Records that never receive their terminating newline are not records: they
    stay in the buffer and are dropped at EOF without being parsed, which is what
    "ignore any incomplete trailing JSON record" means. A complete line that does
    not parse as JSON is counted in ``skipped_records`` and otherwise ignored - no
    such line occurs in the assigned data, and inventing an error for one would
    contradict the EOF rule.
    """
    buffer = b""
    text_parts: list[str] = []
    complete = False
    error: str | None = None
    skipped = 0

    for chunk in chunks:
        # Bytes, never text: a multi-byte UTF-8 character is allowed to straddle
        # a chunk boundary, and decoding chunk-by-chunk would corrupt it.
        buffer += bytes.fromhex(chunk)

        while b"\n" in buffer:
            line, _, buffer = buffer.partition(b"\n")
            try:
                record = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                skipped += 1
                continue
            if not isinstance(record, dict):
                skipped += 1
                continue

            if record.get("error") is not None:
                error = str(record["error"])
                complete = False
                return Reassembled("".join(text_parts), complete, error, skipped)

            response = record.get("response")
            if response:
                text_parts.append(str(response))

            if record.get("done") is True:
                return Reassembled("".join(text_parts), True, None, skipped)

    # Transport EOF with no terminal record.
    return Reassembled("".join(text_parts), complete, error, skipped)


def build_answer() -> dict:
    return {name: reassemble(chunks).as_json() for name, chunks in CHUNKS.items()}


def main() -> None:
    answer = build_answer()
    print(json.dumps(answer, indent=2, ensure_ascii=False))

    target = Path(__file__).resolve().parent.parent / "answer.json"
    target.write_text(
        json.dumps(answer, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"\nwrote {target} ({target.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
