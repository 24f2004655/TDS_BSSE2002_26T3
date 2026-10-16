import time
import uuid
from fastapi import FastAPI, Request, Query, Response
from fastapi.responses import JSONResponse

ALLOWED_ORIGIN = "https://dash-k4ovul.example.com"
EMAIL = "24f2004655@ds.study.iitm.ac.in"

app = FastAPI()

@app.middleware("http")
async def add_context_headers(request: Request, call_next):
    start = time.perf_counter()
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    # NOTE Q3: always generate fresh UUID (ignore inbound), to guarantee uniqueness
    request_id = str(uuid.uuid4())
    response = await call_next(request)
    elapsed = time.perf_counter() - start
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Process-Time"] = str(max(elapsed, 0.0))
    # Manual CORS: only allowed origin gets ACAO, never *
    origin = request.headers.get("origin")
    if origin == ALLOWED_ORIGIN:
        response.headers["Access-Control-Allow-Origin"] = ALLOWED_ORIGIN
        response.headers["Vary"] = "Origin"
    return response

@app.get("/")
async def root():
    return {"ok": True, "email": EMAIL}

@app.options("/stats")
async def stats_options(request: Request):
    origin = request.headers.get("origin")
    if origin == ALLOWED_ORIGIN:
        return Response(
            status_code=200,
            headers={
                "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
                "Access-Control-Allow-Methods": "GET, OPTIONS",
                "Access-Control-Allow-Headers": "*",
                "Access-Control-Max-Age": "86400",
                "Vary": "Origin",
            },
        )
    # evil origin: reject = no ACAO header
    return Response(status_code=400, content="CORS rejected")

@app.get("/stats")
async def stats(values: str = Query(default="", description="comma-separated ints")):
    if not values:
        return JSONResponse(status_code=400, content={"detail": "missing values"})
    try:
        nums = [int(v.strip()) for v in values.split(",") if v.strip() != ""]
    except ValueError:
        return JSONResponse(status_code=400, content={"detail": "values must be integers"})
    if not nums:
        return JSONResponse(status_code=400, content={"detail": "no values"})
    n = len(nums)
    s = sum(nums)
    mn = min(nums)
    mx = max(nums)
    mean = s / n
    return {"email": EMAIL, "count": n, "sum": s, "min": mn, "max": mx, "mean": mean}
