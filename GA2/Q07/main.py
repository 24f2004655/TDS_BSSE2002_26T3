import os
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

API_KEY = os.environ.get("ANALYTICS_API_KEY", "ak_4v7187h051t31s1btrl6df7y")
EMAIL = os.environ.get("ANALYTICS_EMAIL", "24f2004655@ds.study.iitm.ac.in")

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return {"ok": True}


@app.post("/analytics")
async def analytics(request: Request):
    key = request.headers.get("X-API-Key")
    if key != API_KEY:
        return JSONResponse(status_code=401, content={"error": "unauthorized"})

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid JSON"})

    events = body.get("events") if isinstance(body, dict) else None
    if not isinstance(events, list):
        return JSONResponse(status_code=400, content={"error": "events must be a list"})

    total_events = len(events)
    users = set()
    revenue = 0.0
    positive_totals = {}

    for ev in events:
        if not isinstance(ev, dict):
            continue
        user = ev.get("user")
        amount = ev.get("amount", 0)
        try:
            amount = float(amount)
        except (TypeError, ValueError):
            amount = 0.0
        if user is not None:
            users.add(user)
        if amount > 0:
            revenue += amount
            positive_totals[user] = positive_totals.get(user, 0.0) + amount

    top_user = None
    if positive_totals:
        top_user = max(positive_totals.items(), key=lambda kv: kv[1])[0]

    return {
        "email": EMAIL,
        "total_events": total_events,
        "unique_users": len(users),
        "revenue": round(revenue, 6),
        "top_user": top_user,
    }
