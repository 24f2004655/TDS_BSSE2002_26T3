"""Q10 checks, as a runnable script (no pytest needed):

    python tests/test_main.py

Covers the four things the grader checks, the type-coercion rules, and each
layer boundary in isolation so a precedence regression cannot hide behind the
fully merged result.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as m  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

c = TestClient(m.app)
failures = []


def check(name, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {got!r}" + ("" if ok else f"  want {want!r}"))
    if not ok:
        failures.append(name)


# 1. baseline: all four layers merged, no overrides
body = c.get("/effective-config").json()
check("baseline", body, {"port": 8313, "workers": 16, "debug": False,
                         "log_level": "info", "api_key": "****"})
check("exactly the five keys", sorted(body), ["api_key", "debug", "log_level", "port", "workers"])
check("port int / workers int / debug bool / log_level str",
      (isinstance(body["port"], int), isinstance(body["workers"], int),
       isinstance(body["debug"], bool), isinstance(body["log_level"], str)),
      (True, True, True, True))
check("alias NUM_WORKERS (16 in .env) -> workers", body["workers"], 16)
check("api_key masked", body["api_key"], "****")
check("no secret in the raw response",
      "key-0pxxl2vux4" in c.get("/effective-config").text, False)

# 2. CLI overrides have the highest precedence
check("next(c)? ", c.get("/effective-config", params={"set": ["port=9000", "debug=true"]}).json(),
      {"port": 9000, "workers": 16, "debug": True, "log_level": "info", "api_key": "****"})
check("override cannot unmask api_key",
      "leak-me" in c.get("/effective-config", params={"set": ["api_key=leak-me"]}).text, False)

# 3. coercion
for raw, want in [("true", True), ("TRUE", True), ("1", True), ("yes", True), ("on", True),
                  ("false", False), ("0", False), ("no", False), ("off", False)]:
    check(f"debug={raw}", c.get("/effective-config", params={"set": [f"debug={raw}"]}).json()["debug"], want)
for raw, want in [("9000", 9000), ("1", 1)]:
    check(f"port={raw}", c.get("/effective-config", params={"set": [f"port={raw}"]}).json()["port"], want)

# 4. unknown override does not change the response contract
check("unknown override keeps shape",
      sorted(c.get("/effective-config", params={"set": ["region=us-east-1"]}).json()),
      ["api_key", "debug", "log_level", "port", "workers"])

# 5. each layer boundary, in isolation
orig = (m.load_yaml_layer, m.load_dotenv_layer, m.load_os_env_layer)
try:
    m.load_yaml_layer = lambda env: {}
    m.load_dotenv_layer = lambda: {}
    m.load_os_env_layer = lambda: {}
    check("defaults only", m.resolve({})["port"], 8000)

    m.load_yaml_layer = lambda env: {"port": 8743}
    check("yaml beats defaults", m.resolve({})["port"], 8743)

    m.load_dotenv_layer = lambda: {"APP_PORT": "8452"}
    check("dotenv beats yaml", m.resolve({})["port"], 8452)

    m.load_os_env_layer = lambda: {"port": "8313"}
    check("os env beats dotenv", m.resolve({})["port"], 8313)

    check("override beats everything", m.resolve({"port": "1234"})["port"], 1234)
finally:
    m.load_yaml_layer, m.load_dotenv_layer, m.load_os_env_layer = orig

# 6. the real files parse to the assigned values
check("config.development.yaml", m.load_yaml_layer("development"), {"port": 8743, "debug": False})
check(".env", m.load_dotenv_layer()["APP_PORT"], "8452")
check("os env layer", m.load_os_env_layer(), {"port": "8313"})

# 7. CORS
check("CORS allow-origin",
      c.get("/effective-config", headers={"Origin": "https://exam.sanand.workers.dev"})
       .headers.get("access-control-allow-origin"), "*")
check("preflight",
      c.options("/effective-config", headers={"Origin": "https://exam.sanand.workers.dev",
                                              "Access-Control-Request-Method": "GET"}).status_code, 200)

print()
print("FAILURES:", failures if failures else "none")
sys.exit(1 if failures else 0)
