# Q13 — Recover a fragmented LLM stream

Answer JSON for exam question 13 (1.5 marks). No service is required: the exam
server does a plain `GET` and parses the body, so a static file is enough.

**Serve `answer.json` as-is** (302 bytes, UTF-8):

```json
{
  "A": { "text": "Report job-ebf9f249: café\nReady ✓", "complete": true,  "error": null },
  "B": { "text": "Partial job-ebf9f249",               "complete": false, "error": null },
  "C": { "text": "Draft job-ebf9f249",                 "complete": false, "error": "model unloaded" }
}
```

## Hosting

**Primary — the raw GitHub URL** (no deploy, cannot spin down or 404 later):

```
https://raw.githubusercontent.com/24f2004655/TDS_BSSE2002_26T3/main/GA2/Q13/answer.json
```

Raw GitHub serves the file bytes unchanged. The same pattern was already accepted
by this exam's grader for question 1's `submission.tar.gz`, so a raw URL is a
known-good shape for the fetch. Note the content type is `text/plain` — the body
is raw JSON, which is what the question asks for ("Serve raw JSON without
authentication or an HTML wrapper"), but it is not `application/json`.

**Fallback — a Render Static Site**, if the raw URL is refused. Static sites are
CDN-served and never cold-start, unlike a web service. OnRender → New → Static
Site → this repo → **Root Directory `GA2/Q13`**, **Publish Directory `.`**, build
command empty. The answer is then at `https://<name>.onrender.com/answer.json`
with `Content-Type: application/json`.

Do **not** use a Render *web service* for this one: free instances spin down, and
the question requires a response within 10 seconds.

## How the answer was derived

```bash
python tools/reassemble.py       # prints the answer and rewrites answer.json
pytest -q                        # 27 tests
```

The chunks are hex-encoded **bytes**, so the decoder buffers bytes and only
decodes text once a record is complete:

1. **Bytes, not text, until a newline arrives.** `café` has its `é` split as
   `c3 | a9` across two chunks, and `✓` split as `e2 | 9c93`. Decoding each chunk
   independently produces replacement characters; buffering does not.
2. **A record is only a record once its newline arrives.** Stream B's last chunk
   is `{"response":"unfinished` with no terminator, so it is dropped at EOF
   without being parsed — `complete: false`, `error: null`.
3. **`response` only; `thinking` is ignored.** Stream A's second record carries
   both, and contributes only its (empty) response.
4. **`error` and `done: true` both terminate.** On `error`, earlier text is kept
   and later records are ignored (stream C's `IGNORE` record never arrives). On
   `done: true`, the record's own response text *is* included before stopping.
   The spec says "include any response text" for `done` but "preserve earlier
   response text" for `error`, so the error record's own response, if it had one,
   would not be appended — that asymmetry is treated as deliberate.

Stream by stream:

| | records | outcome |
|---|---|---|
| **A** | 4: text, thinking-only, `café\n`, `done: true` with `Ready ✓` | `complete: true`, all four pieces in order |
| **B** | 1 complete + 1 unterminated | `complete: false`, `error: null` |
| **C** | text, then `error`, then a decoy `IGNORE` | `complete: false`, `error: "model unloaded"` |

### The hand decode that was wrong

The expected strings in `tests/test_reassemble.py` were decoded by hand from the
hex *before* the code existed, precisely so they could contradict it — and they
did. I had written the glyph as U+2613 (`☓`); the bytes `e2 9c 93` are U+2713
(`✓`), and a round-trip through `unicodedata` confirmed the code was right and my
note was wrong. The hand decode was still worth doing: it is the only reason the
`✓` was checked at all rather than assumed, and it caught one test of mine that
had been written without its terminating newline. Both were test bugs, not
implementation bugs, and both were found by the tests disagreeing with something.

## Limitations, stated rather than hidden

- A complete line that is not valid JSON is skipped and counted
  (`skipped_records`) rather than treated as an error. No such line occurs in the
  assigned data, and inventing an error for one would contradict the stated EOF
  rule.
- The counter does not affect the served JSON, which has exactly the three
  documented keys per stream and no extras — in case the comparison is an exact
  equality rather than a field-by-field one.
