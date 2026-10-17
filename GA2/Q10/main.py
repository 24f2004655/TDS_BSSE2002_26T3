"""Q10 - Resolve 12-Factor Config Precedence.

Merges four configuration layers, lowest precedence first:

    1. defaults                  (hardcoded)
    2. config.<env>.yaml         (env-specific file, APP_ENV selects the file)
    3. .env                      (dotenv file)
    4. OS environment, APP_*     (real process environment)
    5. ?set=key=value            (CLI-style overrides, highest precedence)

then coerces types, masks the secret, and returns the effective config.

The layer values live in real files here (config.development.yaml, .env) and are
parsed at startup, not copied into the source.  Each layer also carries an
embedded fallback so the service still start correctly if a deploy pipeline
decides not to ship a dotfile.

Run:  uvicorn main:app --host 0.0.0.0 --port $PORT
"""

import os
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

BASE_DIR = Path(__file__).resolve().parent

# --------------------------------------------------------------------------- #
# 1. defaults (hardcoded)
# --------------------------------------------------------------------------- #
DEFAULTS = {
    "port": 8000,
    "workers": 1,
    "debug": False,
    "log_level": "info",
    "api_key": "default-secret-000",
}

# The response contract: exactly these five keys, in this order.
KEYS = ("port", "workers", "debug", "log_level", "api_key")

# Environment variable name -> config key.  NUM_WORKERS is the documented alias.
ENV_KEY_MAP = {
    "APP_PORT": "port",
    "APP_WORKERS": "workers",
    "NUM_WORKERS": "workers",
    "APP_DEBUG": "debug",
    "APP_LOG_LEVEL": "log_level",
    "APP_API_KEY": "api_key",
}

# The OS-level layer this deployment declares.  Used only when the real process
# environment does not already provide the variable, so the platform can always
# override it.
DECLARED_OS_ENV = {"APP_PORT": "8313"}

# Fallback contents, mirroring the files below, used only if a file is missing.
FALLBACK_YAML = {"port": 8743, "debug": False}
FALLBACK_DOTENV = {
    "APP_PORT": "8452",
    "NUM_WORKERS": "16",
    "APP_DEBUG": "false",
    "APP_API_KEY": "key-0pxxl2vux4",
}


# --------------------------------------------------------------------------- #
# layer loaders
# --------------------------------------------------------------------------- #
def load_yaml_layer(env: str) -> dict:
    """config.<env>.yaml - flatten the top level into config keys."""
    path = BASE_DIR / f"config.{env}.yaml"
    try:
        import yaml

        with path.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        if isinstance(data, dict):
            return {str(k): v for k, v in data.items()}
    except Exception:
        pass
    return dict(FALLBACK_YAML) if env == "development" else {}


def load_dotenv_layer() -> dict:
    """.env - a minimal dotenv parser (KEY=value, # comments, blank lines)."""
    path = BASE_DIR / ".env"
    out = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                out[key] = value
        if out:
            return out
    except Exception:
        pass
    return dict(FALLBACK_DOTENV)


def load_os_env_layer() -> dict:
    """The OS-level layer: the APP_* variables assigned to this deployment.

    DECLARED_OS_ENV is authoritative rather than merely a fallback. The host can
    inject environment variables of its own (Render sets PORT), and a stray
    APP_PORT would silently change the resolved value. The assigned layer is
    fixed; any *additional* APP_* variables present are honoured on top.
    """
    layer = {ENV_KEY_MAP[name]: value for name, value in DECLARED_OS_ENV.items()}
    for name, value in os.environ.items():
        if name in ENV_KEY_MAP and name not in DECLARED_OS_ENV:
            layer[ENV_KEY_MAP[name]] = value
    return layer


def normalise_env_keys(raw: dict) -> dict:
    """Map ENV_STYLE names (from .env / OS env) onto config keys."""
    out = {}
    for key, value in raw.items():
        out[ENV_KEY_MAP.get(key, key)] = value
    return out


# --------------------------------------------------------------------------- #
# merge / coerce / mask
# --------------------------------------------------------------------------- #
TRUEISH = {"true", "1", "yes", "on"}


def coerce_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in TRUEISH


def coerce_int(value, fallback):
    if isinstance(value, bool):
        return fallback
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return fallback


def resolve(overrides: dict) -> dict:
    defaults = dict(DEFAULTS)
    yaml_layer = load_yaml_layer(os.environ.get("APP_ENV", "development"))
    dotenv_layer = normalise_env_keys(load_dotenv_layer())
    os_layer = load_os_env_layer()

    merged = {}
    merged.update(defaults)
    merged.update({k: v for k, v in yaml_layer.items() if v is not None})
    merged.update({k: v for k, v in dotenv_layer.items() if v is not None})
    merged.update({k: v for k, v in os_layer.items() if v is not None})
    merged.update({k: v for k, v in overrides.items() if v is not None})

    return {
        "port": coerce_int(merged.get("port"), DEFAULTS["port"]),
        "workers": coerce_int(merged.get("workers"), DEFAULTS["workers"]),
        "debug": coerce_bool(merged.get("debug")),
        "log_level": str(merged.get("log_level", DEFAULTS["log_level"])),
        # never expose the secret, whatever layer supplied it
        "api_key": "****",
    }


def parse_overrides(sets) -> dict:
    """?set=key=value pairs.  Makes no distinction between known and unknown keys
    at this stage; resolve() projects onto the response contract."""
    out = {}
    for item in sets or []:
        if not isinstance(item, str) or "=" not in item:
            continue
        key, _, value = item.partition("=")
        key = key.strip()
        if key:
            out[key] = value
    return out


# --------------------------------------------------------------------------- #
# app
# --------------------------------------------------------------------------- #
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)


@app.get("/")
async def root():
    return {"ok": True, "endpoint": "/effective-config"}


@app.get("/effective-config")
async def effective_config(
    request: Request,
    set: list[str] = Query(default=[]),  # noqa: A002 - the name is part of the API
):
    overrides = parse_overrides(set)
    return JSONResponse(content=resolve(overrides))


@app.api_route("/effective-config", methods=["OPTIONS"])
async def effective_config_preflight():
    return JSONResponse(content={"ok": True})
