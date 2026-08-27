"""Live check of the configured LLM provider (Stage C). Loads .env, pings the endpoint, runs the seed week.
Run: .venv/bin/python scripts/llm_check.py [--no-ping]   (--no-ping saves one request on tight daily quotas)"""
from __future__ import annotations

import json
import math
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine import dotenv  # noqa: E402

dotenv.load(os.path.join(ROOT, ".env"))

from engine import llm as L  # noqa: E402
from engine.run import run_pipeline  # noqa: E402


if not L.llm_configured():
    sys.exit("No key found. Put ANTHROPIC_API_KEY=... or LLM_API_KEY=... (+ LLM_BASE_URL, LLM_MODEL) in .env")

model = os.environ.get("LLM_MODEL") or (L.DEFAULT_MODEL if L.llm_provider() == "anthropic" else L.DEFAULT_OPENAI_MODEL)
print(f"provider={L.llm_provider()} base_url={os.environ.get('LLM_BASE_URL') or L.DEFAULT_OPENAI_BASE_URL} model={model}")

if "--no-ping" not in sys.argv:
    ping = {"queued_sessions": [{"session_id": "S0", "session": {"batch_id": "B01", "subject": "Maths", "type": "class"},
                                 "candidates": [{"sme_id": "A", "name": "A", "score": 0.6}, {"sme_id": "B", "name": "B", "score": 0.58}]}],
            "flags": []}
    pings = [("primary", model, L.default_llm_call)]
    if L.fallback_cfg():
        pings.append(("fallback", L.fallback_cfg()["model"], L.fallback_llm_call))
    for label, m, call in pings:
        try:
            out = call(ping)
            print(f"ping {label} ({m}) ok →", json.dumps(out)[:200])
        except Exception as e:  # noqa: BLE001
            kind = L.classify(e)
            print(f"ping {label} ({m}) FAILED [{kind}]: {str(e)[:300]}")
            if label == "primary" and not L.fallback_cfg():
                sys.exit(1)

rd = lambda n: json.load(open(os.path.join(ROOT, "data", f"{n}.json")))  # noqa: E731
t0 = time.time()
# the next week is the drafted one — the only week that spends LLM quota
res = run_pipeline(rd("sessions_next"), rd("smes"), rd("history"), [])
st = res["stats"]
n_requests = math.ceil(st["llm"]["queued"] / L.CHUNK)
print(f"full run {time.time() - t0:.1f}s — requests this run: {n_requests} (chunk={L.CHUNK}, parallel={L.MAX_PARALLEL}, timeout={L.TIMEOUT_S:.0f}s)")
print(f"queued={st['llm']['queued']} llm_resolved={st['llm_resolved']} "
      f"(via fallback provider: {st['llm']['resolved_by_fallback_provider']}) "
      f"TIE_ESCALATED={st['flags_by_code'].get('TIE_ESCALATED', 0)} LLM_FALLBACK={st['flags_by_code'].get('LLM_FALLBACK', 0)} "
      f"unfilled={st['unfilled']}")
if st["llm"]["error_kind"]:
    print(f"error_kind={st['llm']['error_kind']}\n  {st['llm']['message']}\n  provider said: {(st['llm']['error'] or '')[:300]}")
    fo = st["llm"]["failover"]
    if fo and fo.get("error"):
        print(f"  fallback provider said: {fo['error'][:400]}")
for r in [r for r in res["draft"] if r["stage"] == "llm"][:3]:
    f = next(f for f in r["flags"] if f["code"] == "TIE_ESCALATED")
    print(f"  {r['session_id']} → {r['sme_name']}: {f['reason']}")
sys.exit(1 if st["llm"]["fallback"] else 0)
