import json
import logging
import os
import sys
import time
import uuid
from collections import deque
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import Counter, generate_latest, CONTENT_TYPE_LATEST

EMAIL = os.environ.get("OBS_EMAIL", "24f2004655@ds.study.iitm.ac.in")
MAX_LOG_ENTRIES = 1000

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Prometheus counter -> exposed as http_requests_total (single unlabeled series)
REQUEST_COUNTER = Counter("http_requests", "Total HTTP requests handled")

# In-memory structured log buffer
LOG_BUFFER = deque(maxlen=MAX_LOG_ENTRIES)

START_TIME = time.time()

# Structured JSON logging to stdout
logger = logging.getLogger("observability")
logger.setLevel(logging.INFO)
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(logging.Formatter("%(message)s"))
logger.addHandler(_handler)
logger.propagate = False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@app.middleware("http")
async def observability_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    path = request.url.path
    start = time.perf_counter()
    ts = _now_iso()

    # count the request before handling so /metrics reflects it
    REQUEST_COUNTER.inc()

    try:
        response = await call_next(request)
    except Exception:
        elapsed = time.perf_counter() - start
        entry = {
            "level": "error",
            "ts": ts,
            "path": path,
            "request_id": request_id,
            "method": request.method,
            "status": 500,
            "duration_ms": round(elapsed * 1000, 3),
        }
        LOG_BUFFER.append(entry)
        logger.info(json.dumps(entry))
        raise

    status = response.status_code

    elapsed = time.perf_counter() - start
    level = "error" if status >= 500 else ("warning" if status >= 400 else "info")
    entry = {
        "level": level,
        "ts": ts,
        "path": path,
        "request_id": request_id,
        "method": request.method,
        "status": status,
        "duration_ms": round(elapsed * 1000, 3),
    }
    LOG_BUFFER.append(entry)
    logger.info(json.dumps(entry))

    response.headers["X-Request-ID"] = request_id
    return response


@app.get("/")
async def root():
    return {"ok": True, "email": EMAIL}


@app.get("/work")
async def work(n: int = 0):
    n = max(0, min(n, 1_000_000))
    total = 0
    for _ in range(n):
        total += 1
    return {"email": EMAIL, "done": n}


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/healthz")
async def healthz():
    uptime = time.time() - START_TIME
    return {"status": "ok", "uptime_s": round(max(uptime, 0.0), 6)}


@app.get("/logs/tail")
async def logs_tail(limit: int = 50):
    limit = max(1, min(limit, MAX_LOG_ENTRIES))
    entries = list(LOG_BUFFER)[-limit:]
    return entries
