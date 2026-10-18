# Q15 — recording steps (termlog + Ollama Modelfile)

The recording is the graded artifact, so these steps are the deliverable. Everything
below was rehearsed on this machine before being written down; the things that were
*checked* rather than assumed are listed at the end.

## Preflight (already verified)

| item | state |
|---|---|
| `termlog` | 3.2.1, at `/usr/local/bin/termlog`, logged in as `24f2004655@ds.study.iitm.ac.in` |
| `ollama` | 0.40.1, at `/usr/local/bin/ollama`, daemon answering on `localhost:11434` |
| base model | `gemma3:1b-it-qat` (1.0 GB) already pulled — no download needed |
| target name | `ga2-assistant-ebf9f249` does not exist yet, so `ollama create` really builds it |
| shell | `/bin/bash` (this is what `termlog rec` will drive) |
| disk | ~943 GB free |

All of this is in **WSL**, not Windows — Ollama runs in WSL, which is why there is no
`ollama` on the Windows PATH.

## 1. Before recording

```bash
mkdir -p ~/q15 && cd ~/q15
```

Both output files (`model.cast`, `model.cast.jwt`) land in this directory. `model.cast.jwt`
is only written after the final `exit` event, so the `exit` is not optional.

## 2. Start the recording

```bash
termlog rec model.cast
```

Maximise the terminal window first. The exported `SYSTEM` line is 108 characters, and a
narrow terminal is the standard way this question fails.

## 3. Inside the recording

```bash
stty cols 200
printf 'FROM gemma3:1b-it-qat\nSYSTEM You are the deployment assistant for project ebf9f249. Give concise answers and never invent commands.\nPARAMETER temperature 0.2\nPARAMETER num_ctx 4096\nPARAMETER seed 419\nPARAMETER num_predict 73\n' > Modelfile
tr -d '\r' < Modelfile > .mf && mv .mf Modelfile
cat Modelfile
ollama create ga2-assistant-ebf9f249 -f Modelfile
printf '\nMODELFILE_BEGIN ga2-assistant-ebf9f249\n'
ollama show --modelfile ga2-assistant-ebf9f249
printf '\nMODELFILE_END ga2-assistant-ebf9f249\n'
exit
```

Why each line is there:

- **`stty cols 200`** — makes the recorded width wide, so no grader-side line
  reconstruction can wrap the `SYSTEM` or `FROM` lines.
- **the `printf`** — writes the Modelfile *inside* the recording (the question asks for
  that). It is one line rather than a heredoc specifically so that pasting it from
  Windows cannot break a heredoc terminator on a stray carriage return.
- **`tr -d '\r'`** — belt and braces: if a paste did inject a CR, it is removed before
  Ollama ever parses the file. Visible in `cat` immediately after.
- **`cat Modelfile`** — shows the Modelfile as authored, including the base model name
  `gemma3:1b-it-qat`. The exported modelfile shows `FROM` as a blob path, so this is the
  only place the base model is named in the output.
- **`ollama create ...`** — must print `success` before the markers; it is what the
  marker block is then evidence *of*.
- **the two `printf` markers** — `'\n...\n'` produces exactly the bytes the question's
  multi-line `printf` produces: the marker alone on its own line.
- **`exit`** — ends the recording and triggers the signed receipt.

## 4. Before you exit — check these three things on screen

1. `cat Modelfile` shows exactly 6 lines, ending `PARAMETER num_predict 73`, with no
   `^M` or other junk.
2. `ollama create` ends with `success`.
3. Between the two markers, the output shows `SYSTEM You are the deployment assistant
   for project ebf9f249. Give concise answers and never invent commands.` on **one**
   line, and each `PARAMETER` line intact.

If any of those is wrong, finish the recording anyway (let it exit) and record again —
a `model.cast` from a second attempt is fine; only the submitted one matters.

## 5. Verification before submitting

Two independent checks, both run against the finished cast:

```bash
termlog verify ~/q15/model.cast --expect-email 24f2004655@ds.study.iitm.ac.in
python tools/verify_cast.py ~/q15/model.cast
```

- `termlog verify` is authoritative for the identity and integrity side: JWT signature,
  SHA-256 of the cast, the embedded proof, the Google identity, and the final exit event.
- `tools/verify_cast.py` covers the content side: that `ollama create` really ran and
  succeeded *before* the marker block, that the block's `SYSTEM` and four `PARAMETER`
  values are byte-exact and appear once each, that `FROM` is the intended base blob, that
  the base chat template was inherited, and that no secret was broadcast into a publicly
  hosted recording.

Only when both are clean is the archive built and uploaded:

```bash
cd ~/q15 && tar -czf submission.tar.gz model.cast model.cast.jwt
```

## What was verified rather than assumed

- The Modelfile was built once under a throwaway name (`ga2-rehearsal-ebf9f249`) and
  removed, to confirm from `ollama show --modelfile` that each assigned parameter
  appears **exactly once** and that `temperature 0.2` *replaces* the base model's
  `temperature 1` instead of duplicating it. The base's `top_k 64`, `top_p 0.95` and
  `stop <end_of_turn>` are inherited, which the question allows.
- No `TEMPLATE` line is written, so Gemma's default chat template is inherited, as the
  question requires.
- The base model's blob path was recorded from that rehearsal and is asserted by
  `tools/verify_cast.py`, so a recording built on a different base is caught.
- The gate itself is tested against 16 deliberately broken recordings (wrapped `SYSTEM`,
  duplicated parameter, wrong value, wrong base blob, missing `success`, `success` after
  the markers, missing end marker, missing receipt, unsigned cast, no `create` command,
  Modelfile never typed, no `exit`, oversized file, recorded secret, corrupt file, empty
  file) so a passing verdict means something.
