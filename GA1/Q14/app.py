import hashlib
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

EMAIL = "24f2004655@ds.study.iitm.ac.in"

def solve(challenge: str) -> str:
    return hashlib.sha256(f"{challenge}:{EMAIL}".encode()).hexdigest()[:16]

TOOL = {
    "name": "solve_challenge",
    "description": "Solves exam challenge from HTTP headers",
    "inputSchema": {"type": "object", "properties": {}},
}

async def handle_rpc(body: dict, headers):
    method = body.get("method")
    bid = body.get("id")
    params = body.get("params", {}) or {}
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": bid, "result": {
            "protocolVersion": params.get("protocolVersion", "2024-11-05"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "q14-solver", "version": "1.0.0"}}}
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": bid, "result": {"tools": [TOOL]}}
    if method == "tools/call":
        challenge = headers.get("x-exam-challenge", "")
        text = solve(challenge)
        return {"jsonrpc": "2.0", "id": bid, "result": {
            "content": [{"type": "text", "text": text}]}}
    return {"jsonrpc": "2.0", "id": bid, "error": {"code": -32601, "message": f"unknown method {method}"}}

async def endpoint(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)
    if isinstance(body, list):
        out = []
        for b in body:
            r = await handle_rpc(b, request.headers)
            if r is not None:
                out.append(r)
        return JSONResponse(out if out else {})
    r = await handle_rpc(body, request.headers)
    if r is None:
        return JSONResponse({}, status_code=202)
    return JSONResponse(r)

app.post("/")(endpoint)
app.post("/mcp")(endpoint)
app.post("/mcp/")(endpoint)
app.post("/{full_path:path}")(endpoint)

@app.get("/")
def root():
    return {"ok": True, "tool": "solve_challenge"}

@app.get("/mcp")
def root_mcp():
    return {"ok": True, "tool": "solve_challenge"}
