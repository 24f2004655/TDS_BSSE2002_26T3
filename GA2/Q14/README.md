# Q14 — Diagnose Docker build cache misses

Answer JSON for exam question 14 (1.5 marks). A static file is enough: the exam
server does a plain `GET` and parses the body.

## Answer — serve `answer.json` as-is

```json
{
  "A": { "steps": ["L3", "L4", "L5"],                 "seconds": 36 },
  "B": { "steps": ["L1", "L2", "L3", "L4", "L5"],     "seconds": 81 },
  "C": { "steps": [],                                  "seconds": 0  },
  "D": { "steps": [],                                  "seconds": 0  },
  "E": { "steps": ["L4", "L5"],                        "seconds": 36 }
}
```

## Hosting

**Primary — the raw GitHub URL** (no deploy, cannot spin down or 404 later):

```
https://raw.githubusercontent.com/24f2004655/TDS_BSSE2002_26T3/main/GA2/Q14/answer.json
```

The same pattern was accepted by this exam's grader for question 1. Note the
content type is `text/plain`; the body is raw JSON, which is what the question
asks for. If Check rejects it, the fix is the pipe, not the answer:

**Fallback — a Render Static Site.** New → Static Site → this repo → **Root
Directory `GA2/Q14`**, **Publish Directory `.`**, empty build command → serves
`https://<name>.onrender.com/answer.json` with `Content-Type: application/json`.
Do not use a Render *web service*: free instances spin down and the question
requires a response within 10 seconds.

## Why each case is what it is

The exercise rule is "once a step misses cache, all later steps run again", so
only the **first** miss matters — the answer is that step and everything after
it. `tools/derive.py` stores one judgement per case (which step misses first) and
derives the rest mechanically, so the step list and the seconds cannot disagree.

| case | first miss | re-executed | seconds | why |
|---|---|---|---|---|
| **A** change `src/app.py` | L3 | L3, L4, L5 | 0+12+24 = **36** | `app.py` is a source file of the L3 `COPY`, so its content is an input to L3. `requirements.txt` is untouched, so L1 and L2 still hit — this is the case the "pip reinstalls unnecessarily" framing is about. |
| **B** change `requirements.txt` | L1 | L1, L2, L3, L4, L5 | 0+45+0+12+24 = **81** | `requirements.txt` is the source of the L1 `COPY`, so L1 misses (cost zero) and the cascade drags L2 — the 45 s pip install — and everything after it along, even though `src/` never changed. |
| **C** `touch src/app.py` | *(none)* | — | **0** | `mtime` is not part of a `COPY`'s cache checksum. This is the Make-vs-Docker distinction the case exists to test: with Make, a touched prerequisite rebuilds; with Docker, it does not. |
| **D** change `README.md` | *(none)* | — | **0** | Excluded by `.dockerignore`, so it never enters the build context and no instruction can depend on it. |
| **E** L4 becomes `compileall -q src` | L4 | L4, L5 | 12+24 = **36** | For `RUN`, the command string itself is the cache match, so the `-q` change misses L4 regardless of any file. L5 then re-runs as a later step. |

The prompt's note that `--no-cache-dir` "affects pip's download cache, not
Docker's layer cache" is a deliberate red herring: it changes nothing above,
including L2's 45-second cost.

Each judgement is what the reference page the question links actually says:

> "The modification time of a file (`mtime`) is not taken into account when
> calculating the cache checksum. If only the `mtime` of the copied files have
> changed, the cache is not invalidated."

> "Once the cache is invalidated, all subsequent Dockerfile commands generate new
> images and the cache isn't used."

> "Aside from the `ADD` and `COPY` commands, cache checking doesn't look at the
> files in the container to determine a cache match. […] just the command string
> itself is used to find a match."

## Verification

```bash
python tools/derive.py     # prints the answer, the reasons, and rewrites answer.json
pytest -q                  # 16 tests
```

The expected table in `tests/test_derive.py` is written out by hand from the
Docker rules, so `tools/derive.py` has to argue with that file rather than with
itself. The other tests assert what must never drift apart:

- **`seconds` always equals the summed cost of the listed steps** — for all five
  cases. This is the check that catches a typo in either number.
- **No COPY step is ever charged** time.
- **A miss always takes every later step with it**, and every list is in build
  order — so no case can list L4 without L5.
- **Exactly two cases are empty**, C and D.

That last one is also an argument about the answer, not just a test. If a
`touch` did invalidate L3, case C would be a byte-for-byte duplicate of case A —
same steps, same 36 seconds — and would have no reason to exist as a separate
case. Two cases in this exercise are all-cache-hits, and the question says so
("Use an empty array for all-cache-hit cases").
