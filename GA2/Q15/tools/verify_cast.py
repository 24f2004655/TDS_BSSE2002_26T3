"""Pre-submit gate for Q15: check the recorded cast before uploading it.

Everything this question grades is inside ``model.cast``: the signed identity, the
``ollama create`` command, the marker-delimited ``ollama show --modelfile`` output,
and the exported SYSTEM and parameter values. Rather than submit and hope, this
reads the recording and asserts each of those, so a bad capture is caught while it
is still cheap to re-record.

The checks that matter, and why each one exists:

* A cast that is not a termlog v2 recording has no ``proof`` in its header and
  cannot pass the identity check.
* The marker block is the *last* exact ``MODELFILE_BEGIN`` line, not the first:
  the typed ``printf`` command is echoed into the same output stream, so the
  first match is usually the command rather than its output.
* A ``SYSTEM`` or ``PARAMETER`` line that is present but not byte-exact is
  reported with the line that was found, because the usual cause is the terminal
  wrapping at a narrow width - which is also why the question says to widen it.
* The ``success`` from ``ollama create`` must appear *before* the marker block,
  so the block cannot be showing a stale model from an earlier build.

Read-only, stdlib only. The tests build synthetic casts, including deliberately
broken ones, to prove this gate actually rejects bad recordings.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

MODEL = "ga2-assistant-ebf9f249"
SYSTEM_PROMPT = (
    "You are the deployment assistant for project ebf9f249. "
    "Give concise answers and never invent commands."
)
PARAMS = {
    "temperature": "0.2",
    "num_ctx": "4096",
    "seed": "419",
    "num_predict": "73",
}
# The blob the rehearsal resolved gemma3:1b-it-qat to. A different FROM would mean
# the model was built on a base that was not the one intended.
EXPECTED_BASE_BLOB = (
    "/usr/share/ollama/.ollama/models/blobs/"
    "sha256-57c9b4a897df8d1ba26a152eb7b98e8dc743e21a5d5492f8183270565b84932b"
)

BEGIN_MARKER = f"MODELFILE_BEGIN {MODEL}"
END_MARKER = f"MODELFILE_END {MODEL}"

# CSI/OSC sequences, plus C0 controls other than tab, newline and carriage return.
_ANSI_RE = re.compile(
    r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
    r"|\x1b\[[0-9;?]*[a-zA-Z]"
    r"|\x1b[=>()][A-Za-z0-9]?"
    r"|[\x00-\x08\x0b\x0c\x0e-\x1f]"
)

CREATE_RE = re.compile(rf"ollama\s+create\s+{re.escape(MODEL)}\s+-f\s+Modelfile")

SECRET_HINTS = ("ghp_", "sk-", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "BEGIN OPENSSH", "BEGIN PRIVATE KEY")


def load_cast(path: Path) -> tuple[dict, str, str]:
    """Return (header, output_text, input_text) from an asciicast v2 file.

    A malformed line is skipped rather than fatal: a truncated or corrupted
    recording should still produce a reportable diagnosis, not a traceback.
    """
    raw_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if not raw_lines:
        raise ValueError(f"{path} is empty")
    try:
        header = json.loads(raw_lines[0])
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: the asciicast header is not valid JSON ({exc})") from None
    if not isinstance(header, dict):
        raise ValueError(f"{path}: the asciicast header is not a JSON object")
    out: list[str] = []
    inp: list[str] = []
    for line in raw_lines[1:]:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, list) or len(event) < 3:
            continue
        _, code, data = event[0], event[1], event[2]
        if code == "o":
            out.append(data)
        elif code == "i":
            inp.append(data)
    return header, "".join(out), "".join(inp)


def plain_lines(stream: str) -> list[str]:
    """Strip ANSI, then collapse carriage-return overwrites per line.

    ``\\r`` returns the cursor to column 0 without advancing, so the last thing
    written is what is visible. A trailing ``\\r`` (as in the ``\\r\\n`` a TTY emits)
    must not blank the line, hence "last non-empty write" rather than "last field".
    """
    text = _ANSI_RE.sub("", stream)
    lines: list[str] = []
    for segment in text.split("\n"):
        writes = [part for part in segment.split("\r") if part != ""]
        lines.append((writes[-1] if writes else "").rstrip())
    return lines


def marker_block(out_lines: list[str], begin: str = BEGIN_MARKER, end: str = END_MARKER) -> list[str]:
    """Lines between the last exact begin marker and the end marker after it.

    Taking the *last* begin marker is deliberate: the echoed ``printf`` command is
    also an output line, and for the single-line form it contains the marker text
    but not on its own line. When the marker is typed literally on its own line,
    the echo matches too - and the last match is then the printed one.
    """
    starts = [i for i, line in enumerate(out_lines) if line.strip() == begin]
    for start in reversed(starts):
        for i in range(start + 1, len(out_lines)):
            if out_lines[i].strip() == end:
                return out_lines[start + 1: i]
    raise ValueError("marker block not found")


def check_cast(cast_path: Path, jwt_path: Path | None = None) -> list[str]:
    """Return a list of human-readable failures; empty means good to submit."""
    failures: list[str] = []
    cast_path = Path(cast_path)
    jwt_path = Path(jwt_path) if jwt_path else Path(str(cast_path) + ".jwt")

    if not cast_path.exists():
        return [f"missing recording: {cast_path}"]

    # Size before parsing: cheap, and it keeps a huge or truncated file from
    # turning into a parser error instead of the diagnosis it deserves.
    size = cast_path.stat().st_size
    if size > 5 * 1024 * 1024:
        failures.append(f"recording is {size} bytes, over the 5 MB uncompressed limit")
    if size == 0:
        failures.append(f"recording is empty: {cast_path}")

    try:
        header, out_text, in_text = load_cast(cast_path)
    except ValueError as exc:
        failures.append(str(exc))
        return failures

    out_lines = plain_lines(out_text)
    in_lines = plain_lines(in_text)

    # --- the recording must be a signed termlog capture ----------------------
    if header.get("version") != 2:
        failures.append(f"cast version is {header.get('version')!r}, expected 2")
    if "proof" not in header:
        failures.append(
            "cast header has no 'proof' object - this is not a termlog audited recording"
        )
    if not jwt_path.exists():
        failures.append(f"missing receipt: {jwt_path} (written only after the exit event)")
    elif jwt_path.stat().st_size == 0:
        failures.append(f"empty receipt: {jwt_path}")

    # --- the create must have happened, and before the marker block ----------
    # Searched in the raw input stream: a paste arrives without newlines between
    # the characters, so a line-based search would miss it.
    if not CREATE_RE.search(in_text):
        failures.append(f"'{MODEL} -f Modelfile' was never run inside the recording")

    success_idx = next((i for i, line in enumerate(out_lines) if line.strip() == "success"), None)
    if success_idx is None:
        failures.append("'ollama create' never printed 'success' in the recording")

    try:
        begin_idx = max(i for i, line in enumerate(out_lines) if line.strip() == BEGIN_MARKER)
    except ValueError:
        begin_idx = None

    if success_idx is not None and begin_idx is not None and success_idx > begin_idx:
        failures.append("'success' appears after the marker block - the block shows the wrong model")

    # --- the Modelfile was authored in the recording ------------------------
    typed = in_text
    if f"FROM " not in typed:
        failures.append("the Modelfile's FROM line was never typed in the recording")
    if SYSTEM_PROMPT not in typed:
        failures.append("the SYSTEM prompt was never typed in the recording")
    for name, value in PARAMS.items():
        if f"PARAMETER {name} {value}" not in typed:
            failures.append(f"PARAMETER {name} {value} was never typed in the recording")

    # --- the exported modelfile between the markers -------------------------
    try:
        block = marker_block(out_lines)
    except ValueError as exc:
        failures.append(f"{exc} - were the markers printed on their own output lines?")
        block = []

    if block:
        system_lines = [line for line in block if line.startswith("SYSTEM ")]
        if len(system_lines) != 1:
            failures.append(
                f"expected exactly 1 SYSTEM line in the block, found {len(system_lines)}"
            )
        elif system_lines[0] != f"SYSTEM {SYSTEM_PROMPT}":
            failures.append(
                "SYSTEM line is not exact (often a terminal-wrap artefact): "
                f"found {system_lines[0]!r}"
            )

        for name, value in PARAMS.items():
            found = [line for line in block if line.startswith(f"PARAMETER {name} ")]
            if len(found) != 1:
                failures.append(
                    f"expected exactly 1 'PARAMETER {name}' in the block, found {len(found)}"
                )
            elif found[0] != f"PARAMETER {name} {value}":
                failures.append(
                    f"PARAMETER {name} is {found[0]!r}, expected 'PARAMETER {name} {value}'"
                )

        from_lines = [line for line in block if line.startswith("FROM ")]
        if len(from_lines) != 1:
            failures.append(f"expected exactly 1 FROM line in the block, found {len(from_lines)}")
        elif from_lines[0] != f"FROM {EXPECTED_BASE_BLOB}":
            failures.append(
                "FROM in the exported modelfile is not the gemma3:1b-it-qat blob; "
                f"found {from_lines[0]!r} (wrapped lines show up here)"
            )
        if not any(line.startswith("TEMPLATE") for line in block):
            failures.append("no TEMPLATE line in the block - the base chat template is missing")

        # Is the model itself identifiable? "To build a new Modelfile based on this"
        # names it in a comment, which is a cheap confirmation of the right build.
        if not any(MODEL in line for line in block[:4]):
            failures.append("the block's header comment does not name the built model")

    # --- the session ended properly ----------------------------------------
    end_idx = next(
        (i for i, line in enumerate(out_lines) if line.strip() == END_MARKER), None
    )
    if end_idx is None:
        failures.append("no MODELFILE_END marker - the recording was cut short")
    elif not any(line.strip() == "exit" for line in in_lines):
        # input is captured as a stream; a bare 'exit' line is what a student types
        if "exit" not in in_text.replace("\r", "\n").split("\n"):
            failures.append("'exit' was never typed - the receipt needs the final exit event")

    # --- hygiene: this is a public recording --------------------------------
    for hint in SECRET_HINTS:
        if hint in out_text or hint in in_text:
            failures.append(f"WARNING: the recording contains what looks like a secret ({hint!r})")

    return failures


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(f"usage: {argv[0]} model.cast [model.cast.jwt]")
        return 2
    cast = Path(argv[1])
    jwt = Path(argv[2]) if len(argv) > 2 else None
    failures = check_cast(cast, jwt)

    try:
        header, out_text, _ = load_cast(cast)
    except ValueError as exc:
        header, out_text = {}, f"({exc})\n"
    print(f"cast: {cast} ({cast.stat().st_size if cast.exists() else 0} bytes, "
          f"version {header.get('version')}, "
          f"proof={'yes' if 'proof' in header else 'no'})")
    try:
        block = marker_block(plain_lines(out_text))
        print(f"\n--- exported modelfile between the markers ({len(block)} lines) ---")
        for line in block:
            print(f"| {line}")
        print("--- end block ---\n")
    except ValueError:
        print("\n(no marker block could be extracted)\n")

    if failures:
        print(f"NOT READY TO SUBMIT - {len(failures)} problem(s):")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("READY TO SUBMIT: all Q15 checks pass.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
