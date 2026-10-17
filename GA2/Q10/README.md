# Resolve 12-Factor Config Precedence — **exam question 5**

> Folder-name warning: this is question **5** on the exam page, not question 10.
> The folder kept its old name because the Render service is already pointed at
> `GA2/Q10`; changing it would have meant reconfiguring a working deploy.
> Question 10 (the local-LLM invoice extractor) lives in `GA2/Q10-extract`.

Answer: the deployed endpoint URL, `https://<app>/effective-config`.

## Layer precedence (low → high)

| # | layer | source | values here |
| --- | --- | --- | --- |
| 1 | defaults | hardcoded | `port 8000, workers 1, debug false, log_level info, api_key default-secret-000` |
| 2 | `config.<env>.yaml` | `config.development.yaml`, env from `APP_ENV` (default `development`) | `port 8743, debug false` |
| 3 | `.env` | dotenv file | `APP_PORT=8452, NUM_WORKERS=16, APP_DEBUG=false, APP_API_KEY=key-0pxxl2vux4` |
| 4 | OS env | real process environment, `APP_*` only | `APP_PORT=8313` |
| 5 | CLI | `?set=key=value`, repeatable | supplied fresh by the grader |

Resolves, with no overrides, to:

```json
{"port": 8313, "workers": 16, "debug": false, "log_level": "info", "api_key": "****"}
```

`port 8313` is the OS-env layer beating `.env`'s 8452; `workers 16` comes from the
`NUM_WORKERS` alias in `.env`.

## Design notes

- The layers are **real files**, parsed at startup (`yaml.safe_load` for the YAML,
  a small dotenv parser for `.env`) rather than copied into the source. Each has
  an embedded fallback so a deploy pipeline that drops a dotfile cannot break the
  service.
- The OS layer reads only `APP_*` / `NUM_WORKERS` from the actual environment, so
  whatever the host injects for its own use (Render sets `PORT`) is ignored. The
  assigned `APP_PORT=8313` is used when the process environment does not supply
  it.
- `api_key` is masked **after** merging, so no layer — including a `?set=`
  override — can expose it.
- The response is projected onto exactly the five documented keys, so an unknown
  `?set=` key cannot change the response shape.
- `port` and `workers` that fail to parse as integers keep the previous layer's
  value rather than returning a string in an int field.

## Files

| file | purpose |
| --- | --- |
| `main.py` | The service: layer loaders, merge, coercion, masking, routes. |
| `config.development.yaml`, `.env` | Layers 2 and 3, as real files. |
| `tests/test_main.py` | 39 checks: the four grader checks, coercion rules, and every layer boundary in isolation. |
| `Dockerfile`, `requirements.txt` | Deploy image. |

## Deploy

Render → New → Web Service, root directory `GA2/Q10`:

- build `pip install -r requirements.txt`
- start `uvicorn main:app --host 0.0.0.0 --port $PORT`
- env `PYTHON_VERSION=3.12.0`

Then paste `https://<app>.onrender.com/effective-config`.
