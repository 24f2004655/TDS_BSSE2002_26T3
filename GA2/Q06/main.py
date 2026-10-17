import os
import time
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import redis

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_pool = redis.ConnectionPool.from_url(REDIS_URL, decode_responses=True)


def get_client() -> redis.Redis:
    return redis.Redis(connection_pool=_pool)


@app.get("/")
async def root():
    return {"ok": True}


@app.get("/healthz")
async def healthz():
    try:
        r = get_client()
        if r.ping():
            return {"status": "ok", "redis": "up"}
        return JSONResponse(status_code=503, content={"status": "degraded", "redis": "down"})
    except Exception:
        return JSONResponse(status_code=503, content={"status": "degraded", "redis": "down"})


@app.post("/hit/{key}")
async def hit(key: str):
    try:
        r = get_client()
        count = r.incr(f"counter:{key}")
        return {"key": key, "count": count}
    except Exception:
        return JSONResponse(status_code=503, content={"error": "redis unavailable"})


@app.get("/count/{key}")
async def count(key: str):
    try:
        r = get_client()
        val = r.get(f"counter:{key}")
        return {"key": key, "count": int(val) if val is not None else 0}
    except Exception:
        return JSONResponse(status_code=503, content={"error": "redis unavailable"})
