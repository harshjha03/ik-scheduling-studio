"""Stage C — LLM adjudication of the exception queue. The LLM only chooses among candidates that
already passed Stage A; anything invalid falls back to the Stage B top score (LLM_FALLBACK).

Providers: Anthropic (ANTHROPIC_API_KEY) or any OpenAI-compatible endpoint (LLM_API_KEY + LLM_BASE_URL).
Optional failover: LLM_FALLBACK_API_KEY (+ LLM_FALLBACK_BASE_URL / LLM_FALLBACK_MODEL) is tried for a
chunk whose primary call failed on quota / rate limit / timeout, before the deterministic fallback."""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

_fallback_lock = threading.Lock()  # fallback tiers cap tokens/minute: never hit them from two chunks at once

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_OPENAI_BASE_URL = "https://api.groq.com/openai/v1"   # free tier; any OpenAI-compatible host works
DEFAULT_OPENAI_MODEL = "openai/gpt-oss-20b"  # Groq retired the llama-3.x names; check /models if this 404s
CHUNK = int(os.environ.get("LLM_CHUNK", "20"))          # queue items per call; chunks run concurrently
MAX_PARALLEL = int(os.environ.get("LLM_PARALLEL", "4"))  # free tiers rate-limit per minute; keep bursts small
TIMEOUT_S = float(os.environ.get("LLM_TIMEOUT", "45"))
FALLBACK_CHUNK = int(os.environ.get("LLM_FALLBACK_CHUNK", "12"))  # smaller: Groq free tier caps tokens/minute
FAILOVER_KINDS = {"daily_quota_exhausted", "rate_limited", "timeout", "provider_unavailable"}


class LLMError(RuntimeError):
    """Provider call failed. `kind` is a stable code the API/UI can switch on."""
    kind = "provider_error"


class LLMQuotaExhausted(LLMError):
    kind = "daily_quota_exhausted"


class LLMRateLimited(LLMError):
    kind = "rate_limited"


class LLMTimeout(LLMError):
    kind = "timeout"


class LLMUnavailable(LLMError):
    kind = "provider_unavailable"  # 5xx / "high demand" — transient, retried once


class LLMEmptyResponse(LLMError):
    """HTTP 200 with no usable text — a truncated completion, or a thinking model that spent the
    whole budget reasoning. A malformed answer, not an outage, so callers retry rather than fail over."""
    kind = "empty_response"


# most actionable first; used when several chunks fail for different reasons
KIND_PRIORITY = ["daily_quota_exhausted", "rate_limited", "provider_unavailable", "timeout", "empty_response", "provider_error", "not_configured"]


def classify(exc: BaseException) -> str:
    if isinstance(exc, LLMError):
        return exc.kind
    if isinstance(exc, TimeoutError) or type(exc).__name__ in ("timeout", "APITimeoutError"):
        return "timeout"
    if type(exc).__name__ == "RateLimitError":  # anthropic SDK 429
        return "rate_limited"
    return "provider_error"


def _cause(kind: str, model: str) -> str:
    return {
        "daily_quota_exhausted": f"LLM daily request limit reached for {model}.",
        "rate_limited": f"LLM rate limit hit for {model} (too many requests per minute).",
        "timeout": f"LLM call to {model} timed out after {TIMEOUT_S:.0f}s.",
        "provider_unavailable": f"LLM provider reported {model} temporarily unavailable (high demand / 5xx).",
        "not_configured": "No LLM key configured.",
    }.get(kind, f"LLM provider error from {model}.")


def _hint(kind: str) -> str:
    return {
        "daily_quota_exhausted": "Switch LLM_MODEL to a model with unused quota, or wait for the daily reset.",
        "rate_limited": "Re-run in a minute or lower LLM_PARALLEL / raise LLM_CHUNK.",
        "timeout": "Raise LLM_TIMEOUT or lower LLM_CHUNK.",
        "provider_unavailable": "Re-run in a moment, or switch LLM_MODEL to a less busy model.",
        "not_configured": "Set ANTHROPIC_API_KEY, or LLM_API_KEY + LLM_BASE_URL.",
    }.get(kind, "")


def cause(kind: str | None, model: str) -> str:
    """One plain sentence for a non-technical reader. The raw provider text stays in the caller's
    error field; a coordinator never needs to read a 429 body."""
    return _cause(kind, model) if kind else ""


def explain(kind: str | None, model: str, n_fallback: int, failover: dict | None = None) -> str | None:
    """Plain-language cause for ops. None when Stage C succeeded on the primary provider."""
    if not kind:
        return None
    parts = [_cause(kind, model)]
    if failover and failover.get("resolved"):
        parts.append(f"{failover['resolved']} queued row(s) were adjudicated by the fallback provider "
                     f"({failover['model']}) instead.")
        if n_fallback:
            parts.append(f"{n_fallback} still fell back to the deterministic score (LLM_FALLBACK)"
                         + (f" — fallback provider: {_cause(failover['error_kind'], failover['model']).rstrip('.')}."
                            if failover.get("error_kind") else "."))
    else:
        if failover and failover.get("error_kind"):
            parts.append(f"Fallback provider ({failover['model']}) also failed: "
                         f"{_cause(failover['error_kind'], failover['model'])}")
        parts.append(f"{n_fallback} queued row(s) were resolved by the deterministic score instead (LLM_FALLBACK).")
    if n_fallback:
        parts.append(_hint(kind))
    return " ".join(p for p in parts if p)


def _json_env(name: str) -> dict:
    """Optional JSON object from the environment. Malformed → {} (a bad knob must never 500 a run)."""
    raw = os.environ.get(name)
    if not raw:
        return {}
    try:
        val = json.loads(raw)
        return val if isinstance(val, dict) else {}
    except ValueError:
        return {}


def llm_configured() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("LLM_API_KEY"))


def llm_provider() -> str:
    """'anthropic' or 'openai' (OpenAI-compatible: Groq, xAI Grok, Gemini, OpenRouter, Ollama...)."""
    explicit = os.environ.get("LLM_PROVIDER")
    if explicit:
        return explicit.lower()
    return "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "openai"


def active_model() -> str:
    return os.environ.get("LLM_MODEL") or (DEFAULT_MODEL if llm_provider() == "anthropic" else DEFAULT_OPENAI_MODEL)


def primary_cfg() -> dict:
    return {"base_url": os.environ.get("LLM_BASE_URL") or DEFAULT_OPENAI_BASE_URL,
            "api_key": os.environ.get("LLM_API_KEY", ""),
            "model": os.environ.get("LLM_MODEL") or DEFAULT_OPENAI_MODEL,
            "extra": _json_env("LLM_EXTRA_BODY")}


def fallback_cfg() -> dict | None:
    """Failover provider. Either a separate key (LLM_FALLBACK_API_KEY, e.g. Groq) or — when only
    LLM_FALLBACK_MODEL is set — a second model on the primary key/base URL (e.g. another Gemini bucket)."""
    own_key = os.environ.get("LLM_FALLBACK_API_KEY")
    model = os.environ.get("LLM_FALLBACK_MODEL")
    if not own_key and not (model and os.environ.get("LLM_API_KEY")):
        return None
    if own_key:
        base = os.environ.get("LLM_FALLBACK_BASE_URL") or DEFAULT_OPENAI_BASE_URL
    else:
        base = os.environ.get("LLM_FALLBACK_BASE_URL") or os.environ.get("LLM_BASE_URL") or DEFAULT_OPENAI_BASE_URL
    return {"base_url": base,
            "api_key": own_key or os.environ.get("LLM_API_KEY", ""),
            "model": model or DEFAULT_OPENAI_MODEL,
            "extra": _json_env("LLM_FALLBACK_EXTRA_BODY")}


SCHEMA = {
    "type": "object",
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "chosen_sme_id": {"type": "string"},
                    "reason": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["session_id", "chosen_sme_id", "reason", "confidence"],
                "additionalProperties": False,
            },
        },
        "flag_reasons": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "code": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["session_id", "code", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["decisions", "flag_reasons"],
    "additionalProperties": False,
}

SYSTEM = (
    "You adjudicate ties for a live-class scheduler. For every queued session choose exactly one "
    "chosen_sme_id from that session's `candidates` list — never any other id. Weigh the deterministic "
    "score, preference_notes, per-topic ratings and recent batches; prefer the higher score unless notes "
    "or ratings give a concrete reason otherwise. `reason` is one plain-language sentence for an ops user. "
    "For each entry in `flags`, rewrite `template_reason` as one clear sentence keeping every fact "
    "(names, counts, times). Return strict JSON only."
)


def default_llm_call(payload: dict) -> dict:
    """Primary provider. Raises on any failure; callers treat that as fallback."""
    return anthropic_call(payload) if llm_provider() == "anthropic" else openai_compatible_call(payload, primary_cfg())


def fallback_llm_call(payload: dict) -> dict:
    return openai_compatible_call(payload, fallback_cfg())


def openai_compatible_call(payload: dict, cfg: dict | None = None) -> dict:
    """POST {base_url}/chat/completions with JSON-object mode. ponytail: stdlib urllib, no SDK.
    Providers without schema enforcement get the schema in the prompt; _validate() catches anything off."""
    system = SYSTEM + " Output a single JSON object matching this JSON schema exactly: " + json.dumps(SCHEMA)
    return chat_json(system, [{"role": "user", "content": json.dumps(payload)}], cfg)


def chat_json(system: str, messages: list[dict], cfg: dict | None = None) -> dict:
    """One JSON-mode chat completion over an OpenAI-compatible endpoint — same transport, retry ladder
    and error taxonomy as Stage C; the agent loop uses it with a running message list."""
    cfg = cfg or primary_cfg()
    model = cfg["model"]
    body = {
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        **cfg["extra"],  # provider-specific knobs, e.g. {"reasoning_effort":"low"} for Gemini
        "messages": [{"role": "system", "content": system}, *messages],
    }
    req = urllib.request.Request(
        f"{cfg['base_url'].rstrip('/')}/chat/completions", data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {cfg['api_key']}", "Content-Type": "application/json",
                 "User-Agent": "sme-scheduler/0.1"},  # Cloudflare (Groq) rejects the default Python-urllib UA (403 code 1010)
        method="POST")
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                data = json.load(resp)
            break
        except urllib.error.HTTPError as e:
            full = e.read().decode(errors="replace")
            quota_ids = re.findall(r'"quotaId"\s*:\s*"([^"]+)"', full)  # e.g. GenerateRequestsPerDayPerProjectPerModel-FreeTier
            detail = (f"quota: {', '.join(quota_ids)} — " if quota_ids else "") + full[:800]
            if e.code == 429 and re.search(r"PerDay|per day|daily", full, re.I):
                # a per-day quota won't clear in seconds — fail fast and say exactly why
                raise LLMQuotaExhausted(f"daily request quota exhausted for {model}: {detail}") from e
            if e.code == 429:
                if attempt == 0:
                    time.sleep(min(float(e.headers.get("Retry-After") or 3), 20))  # one per-minute retry
                    continue
                raise LLMRateLimited(f"rate limited by provider for {model}: {detail}") from e
            if e.code >= 500:
                if attempt == 0:
                    time.sleep(3)  # transient "high demand" — one retry
                    continue
                raise LLMUnavailable(f"{model} unavailable (HTTP {e.code}): {detail}") from e
            raise LLMError(f"HTTP {e.code} from provider: {detail}") from e
        except TimeoutError as e:
            raise LLMTimeout(f"no response from {model} within {TIMEOUT_S:.0f}s") from e
    return json.loads(_text_of(data, model))


def _text_of(data: dict, model: str) -> str:
    """The completion's text, or a named error. A missing/empty `content` used to surface as
    KeyError('content'), which told ops nothing — say which finish_reason produced nothing."""
    choice = (data.get("choices") or [{}])[0]
    text = ((choice.get("message") or {}).get("content") or "").strip()
    if not text:
        raise LLMEmptyResponse(f"{model} returned no text (finish_reason={choice.get('finish_reason')!r}, "
                               f"usage={data.get('usage')}); the completion was truncated or spent its budget thinking")
    return text


def anthropic_chat_json(system: str, messages: list[dict]) -> dict:
    """Anthropic Messages API, free-form JSON (the agent protocol is a union the schema mode cannot express)."""
    import anthropic

    client = anthropic.Anthropic(timeout=TIMEOUT_S, max_retries=0)
    response = client.messages.create(model=os.environ.get("LLM_MODEL") or DEFAULT_MODEL, max_tokens=4000,
                                      system=system, messages=messages)
    if response.stop_reason == "refusal":
        raise RuntimeError("LLM refused the request")
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
    if not text:
        raise LLMEmptyResponse(f"{active_model()} returned no text (stop_reason={response.stop_reason!r})")
    return json.loads(text)


def agent_llm_call(system: str, messages: list[dict]) -> dict:
    """The agent's one call shape: primary provider, then the failover provider for the same kinds
    Stage C fails over on. Raises LLMError (classified) when both are out."""
    if not llm_configured():
        err = LLMError("no LLM key configured (set ANTHROPIC_API_KEY, or LLM_API_KEY + LLM_BASE_URL)")
        err.kind = "not_configured"
        raise err
    try:
        if llm_provider() == "anthropic":
            return anthropic_chat_json(system, messages)
        return chat_json(system, messages, primary_cfg())
    except Exception as exc:
        fcfg = fallback_cfg()
        if fcfg and classify(exc) in FAILOVER_KINDS:
            with _fallback_lock:
                return chat_json(system, messages, fcfg)
        raise


def anthropic_call(payload: dict) -> dict:
    """Anthropic Messages API with strict JSON-schema output."""
    import anthropic

    client = anthropic.Anthropic(timeout=TIMEOUT_S, max_retries=0)
    response = client.messages.create(
        model=os.environ.get("LLM_MODEL") or DEFAULT_MODEL,
        max_tokens=16000,
        system=SYSTEM,
        messages=[{"role": "user", "content": json.dumps(payload)}],
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("LLM refused the request")
    text = "".join(b.text for b in response.content if b.type == "text")
    return json.loads(text)


def _validate(result: dict, items: list[dict]) -> tuple[dict, dict, list[str]]:
    """Return (decisions_by_session, reasons_by_(session,code), invalid_session_ids)."""
    eligible = {it["session_id"]: {c["sme_id"] for c in it["candidates"]} for it in items}
    decisions, invalid = {}, []
    if not isinstance(result, dict):
        return {}, {}, list(eligible)
    for d in result.get("decisions") or []:
        sid = d.get("session_id") if isinstance(d, dict) else None
        if sid in eligible and d.get("chosen_sme_id") in eligible[sid] and isinstance(d.get("reason"), str):
            decisions[sid] = {"sme_id": d["chosen_sme_id"], "reason": d["reason"].strip(),
                              "confidence": float(d.get("confidence") or 0)}
    invalid = [sid for sid in eligible if sid not in decisions]
    reasons = {}
    for fr in result.get("flag_reasons") or []:
        if isinstance(fr, dict) and isinstance(fr.get("reason"), str) and fr["reason"].strip():
            reasons[(fr.get("session_id"), fr.get("code"))] = fr["reason"].strip()
    return decisions, reasons, invalid


def _attempt(call, chunk: list[dict], chunk_flags: list[dict]) -> tuple[dict, dict, str | None, str | None, list[dict]]:
    """One call + one retry for invalid picks. Returns (decisions, reasons, error, kind, unresolved_items)."""
    decisions, reasons, error, kind = {}, {}, None, None
    payload = {"queued_sessions": chunk, "flags": chunk_flags}
    pending = chunk
    for attempt in range(2):
        try:
            result = call(payload)
        except Exception as exc:  # network, auth, quota, bad JSON, refusal — all fall back, classified
            return decisions, reasons, f"{type(exc).__name__}: {exc}", classify(exc), pending
        got, got_reasons, invalid = _validate(result, pending)
        decisions.update(got)
        reasons.update(got_reasons)
        pending = [it for it in pending if it["session_id"] in invalid]
        if not pending:
            break
        payload = {"queued_sessions": pending, "flags": [],
                   "note": "Previous answer was invalid or chose an ineligible sme_id for these sessions; "
                           "choose only from `candidates`."}
        if attempt == 1:
            error, kind = f"ineligible/missing choice for {len(pending)} session(s)", "provider_error"
    return decisions, reasons, error, kind, pending


def stage_c_llm_adjudicate(items: list[dict], flags: list[dict], llm_call=None, fallback_call=None) -> dict:
    """items: [{session_id, session: {...}, candidates: [{sme_id, name, score, components,
    preference_notes, per_topic_rating, recent_batches}]}]. flags: [{session_id, code, template_reason}].
    Returns {"decisions": {session_id: {sme_id, reason, confidence, via}}, "reasons": {(session_id, code): str},
    "fallback_ids": [...], "error": str|None, "error_kind": str|None,
    "failover": {"kind", "resolved", "error_kind", "error"}|None}. Never raises."""
    empty = {"decisions": {}, "reasons": {}, "fallback_ids": [], "error": None, "error_kind": None, "failover": None}
    if not items:
        return empty
    if llm_call is None:
        if not llm_configured():
            return {**empty, "fallback_ids": [it["session_id"] for it in items], "error_kind": "not_configured",
                    "error": "no LLM key configured (set ANTHROPIC_API_KEY, or LLM_API_KEY + LLM_BASE_URL)"}
        llm_call = default_llm_call
        if fallback_call is None and fallback_cfg():
            fallback_call = fallback_llm_call

    def run_chunk(chunk: list[dict], chunk_flags: list[dict]):
        d, r, e, k, left = _attempt(llm_call, chunk, chunk_flags)
        for v in d.values():
            v["via"] = "primary"
        failover = None
        if e and k in FAILOVER_KINDS and fallback_call and left:
            failover = {"kind": k, "resolved": 0, "error_kind": None, "error": None}
            # sequential, smaller sub-chunks: the fallback is usually a stricter free tier
            for i in range(0, len(left), FALLBACK_CHUNK):
                with _fallback_lock:
                    d2, r2, e2, k2, _ = _attempt(fallback_call, left[i:i + FALLBACK_CHUNK], chunk_flags if i == 0 else [])
                for v in d2.values():
                    v["via"] = "fallback"
                d.update(d2)
                r.update(r2)
                failover["resolved"] += len(d2)
                if e2:
                    failover["error_kind"], failover["error"] = k2, e2
                    if k2 in FAILOVER_KINDS:
                        break  # fallback is limited too — stop hammering it
            if all(it["session_id"] in d for it in left):
                e = k = None  # fully rescued: the primary failure is reported via `failover`, not as an error
        return d, r, e, k, failover

    chunks = [items[i:i + CHUNK] for i in range(0, len(items), CHUNK)]
    with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL, len(chunks))) as pool:
        # flag-reason rewriting rides on the first chunk only
        results = list(pool.map(lambda ic: run_chunk(ic[1], flags if ic[0] == 0 else []), enumerate(chunks)))

    decisions, reasons, failures, failovers = {}, {}, [], []
    for d, r, e, k, fo in results:
        decisions.update(d)
        reasons.update(r)
        if e:
            failures.append((KIND_PRIORITY.index(k) if k in KIND_PRIORITY else 99, k, e))
        if fo:
            failovers.append(fo)
    failures.sort()
    failover = None
    if failovers:
        failover = {"kind": failovers[0]["kind"], "resolved": sum(f["resolved"] for f in failovers),
                    "error_kind": next((f["error_kind"] for f in failovers if f["error_kind"]), None),
                    "error": next((f["error"] for f in failovers if f["error"]), None)}
    fallback_ids = [it["session_id"] for it in items if it["session_id"] not in decisions]
    return {"decisions": decisions, "reasons": reasons, "fallback_ids": fallback_ids,
            "error": failures[0][2] if failures else None, "error_kind": failures[0][1] if failures else None,
            "failover": failover}
