"""
LLM clients. The reasoning brain is provider-configurable (Groq, Moonshot/Kimi,
etc. — anything OpenAI-compatible). Audio transcription + vision stay on Groq,
which is free and reliable for those (Moonshot has no Whisper endpoint).
"""

import base64
import json
import logging
import os
import re

from openai import OpenAI

log = logging.getLogger(__name__)

GROQ_KEY = os.environ.get("GROQ_API_KEY", "").strip()

# ── Primary reasoning brain (configurable provider) ───────────────────────────
# Point at Kimi:      LLM_API_KEY=<moonshot key>
#                     LLM_BASE_URL=https://api.moonshot.ai/v1
#                     LLM_MODEL=kimi-k2-0905-preview   (or kimi-latest)
# Point at NVIDIA NIM: LLM_API_KEY=<nvapi-... key from build.nvidia.com>
#                     LLM_BASE_URL=https://integrate.api.nvidia.com/v1
#                     LLM_MODEL=nvidia/nemotron-3.5-super-vl-120b-a12b   (recommended —
#                       tool calling + per-call reasoning_effort + image input, so
#                       one model covers the coach, smart weight decisions AND
#                       meal photos)
# Default: Groq gpt-oss-120b. (Groq retired llama-3.3-70b-versatile on
# 2026-08-16 and llama-4-scout on 2026-07-17 for free/dev tiers — the old
# defaults now 404 with model_not_found, which silently killed every AI feature.)
LLM_API_KEY  = os.environ.get("LLM_API_KEY", GROQ_KEY).strip()
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.groq.com/openai/v1").strip()
MODEL        = os.environ.get("LLM_MODEL", "openai/gpt-oss-120b").strip()
# Comma-separated models tried in order if the primary is retired/unavailable.
FALLBACK_MODELS = [m.strip() for m in os.environ.get(
    "LLM_FALLBACK_MODELS", "openai/gpt-oss-20b").split(",") if m.strip() and m.strip() != MODEL]

if not LLM_API_KEY:
    raise RuntimeError("Set LLM_API_KEY (or GROQ_API_KEY) for the chat model.")

_client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)
log.info(f"LLM brain: base={LLM_BASE_URL} model={MODEL} fallbacks={FALLBACK_MODELS}")

# ── Groq client for audio (Whisper) + vision ──────────────────────────────────
WHISPER_MODEL = os.environ.get("GROQ_WHISPER_MODEL", "whisper-large-v3").strip()
_groq_client  = OpenAI(api_key=GROQ_KEY, base_url="https://api.groq.com/openai/v1") if GROQ_KEY else None
# If the brain is itself a vision-language model (e.g. nvidia/nemotron-3.5-
# super-vl-120b-a12b) meal photos go to it; otherwise to Groq's vision model.
_BRAIN_IS_VL = bool(re.search(r"(^|[-/_])vl([-/_]|$)", MODEL.lower()))
VISION_MODEL  = (MODEL if _BRAIN_IS_VL else
                 os.environ.get("GROQ_VISION_MODEL", "qwen/qwen3.8-27b").strip())

# Last LLM failure, surfaced by 'system status' so outages aren't silent.
last_error: str | None = None

# Reasoning ("thinking") is chosen PER CALL, not globally:
#   think=True  -> decisions where quality beats latency: next weight/reps,
#                  the weekly self-tuner, the daily cross-domain read, swaps.
#   think=False -> conversational chat + the multi-step tool loop, which would
#                  otherwise burn thousands of hidden tokens per step and blow
#                  past gunicorn's 120s timeout.
# LLM_THINKING=always|never overrides (default: auto = per call).
_IS_NIM = "nvidia.com" in LLM_BASE_URL
LLM_THINKING = os.environ.get("LLM_THINKING", "auto").strip().lower()
# Per-request timeouts (s): fast calls fail fast; thinking calls get more room
# but stay under gunicorn's 120s worker timeout.
LLM_TIMEOUT       = float(os.environ.get("LLM_TIMEOUT", "60"))
LLM_THINK_TIMEOUT = float(os.environ.get("LLM_THINK_TIMEOUT", "105"))


def _reasoning_params(model: str, think: bool) -> dict:
    """Provider-specific knobs that turn reasoning up (think) or down."""
    if LLM_THINKING in ("always", "1", "true", "on"):
        think = True
    elif LLM_THINKING in ("never", "0", "false", "off"):
        think = False
    m = model.lower()
    if _IS_NIM and "nemotron" in m:
        # Nemotron 3.5+ documents reasoning_effort (none|low|high); Nemotron 3
        # reads chat_template_kwargs.enable_thinking. Send each only what it knows.
        v = re.search(r"nemotron-(\d+(?:\.\d+)?)", m)
        if v and float(v.group(1)) >= 3.5:
            return {"extra_body": {"reasoning_effort": "high" if think else "none"}}
        return {"extra_body": {"chat_template_kwargs": {"enable_thinking": think}}}
    if "gpt-oss" in m:                        # Groq / NIM gpt-oss: low|medium|high
        return {"extra_body": {"reasoning_effort": "high" if think else "low"}}
    return {}


_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)


def _clean(text: str | None) -> str:
    """Drop any leaked reasoning trace, keeping only the actual answer."""
    text = text or ""
    text = _THINK_RE.sub("", text)
    if "</think>" in text.lower():          # opening tag got stripped by the server
        text = re.split(r"</think>", text, flags=re.IGNORECASE)[-1]
    return text.strip()


def _is_model_gone(e: Exception) -> bool:
    """True for errors meaning 'this model id no longer works here'."""
    status = getattr(e, "status_code", None)
    text = str(e).lower()
    return status in (400, 404) and ("model" in text and (
        "not_found" in text or "not found" in text or "decommission" in text
        or "deprecat" in text or "does not exist" in text))


def _create(client=None, models=None, think: bool = False, **kwargs):
    """chat.completions.create with per-call reasoning control and model
    fallback on retired-model errors."""
    global MODEL, last_error
    client = client or _client
    candidates = models or [MODEL, *FALLBACK_MODELS]
    kwargs.setdefault("timeout", LLM_THINK_TIMEOUT if think else LLM_TIMEOUT)
    for i, model in enumerate(candidates):
        try:
            resp = client.chat.completions.create(
                model=model, **_reasoning_params(model, think), **kwargs)
            if client is _client and model != MODEL:
                log.warning(f"LLM: primary model unavailable, switching to {model}")
                MODEL = model           # stick with the working one this process
            last_error = None
            return resp
        except Exception as e:
            last_error = f"{model}: {e}"
            if _is_model_gone(e) and i < len(candidates) - 1:
                log.error(f"LLM model {model} unavailable ({e}); trying next fallback")
                continue
            log.error(f"LLM call failed on {model}: {e}")
            raise


def chat(messages: list, temperature: float = 0.7, think: bool = False) -> str:
    """Call the LLM and return the text content (never None). Pass think=True
    for decisions worth slower, deeper reasoning (weights/reps, tuning)."""
    resp = _create(messages=messages, temperature=temperature, think=think)
    return _clean(resp.choices[0].message.content)


def _record_tool_usage(used_tools: bool, tool_names: list[str], steps: int) -> None:
    """Best-effort rolling record of whether the model actually called tools,
    so this is verifiable (via 'system status') instead of taken on faith."""
    try:
        from agent_core import _col
        _col("tool_usage").insert_one({
            "used_tools": used_tools,
            "tool_names": tool_names,
            "steps": steps,
        })
        # Keep the collection small — trim to the last 200 entries.
        col = _col("tool_usage")
        count = col.count_documents({})
        if count > 200:
            for doc in col.find().sort("_id", 1).limit(count - 200):
                col.delete_one({"_id": doc["_id"]})
    except Exception as e:
        log.warning(f"Could not record tool-usage stat: {e}")


def reason_loop(messages: list, tools: list, tool_impls: dict,
                max_steps: int = 5, temperature: float = 0.2) -> str:
    """
    ReAct-style reasoning loop: the model thinks, optionally calls a tool,
    observes the result, and repeats until it produces a final answer.
    `tool_impls` maps tool name -> python callable(**args) returning a string.
    Every call to this function is logged (steps, tool names) and recorded so
    the model's actual tool-use behavior is auditable, not just assumed.
    """
    all_tool_names: list[str] = []
    for step in range(1, max_steps + 1):
        resp = _create(
            messages=messages,
            tools=tools,
            tool_choice="auto",   # some OpenAI-compatible providers (e.g. NIM) need
                                  # this explicit or they silently never enter
                                  # function-calling mode, even with tools= set.
            temperature=temperature,
        )
        msg = resp.choices[0].message
        if not msg.tool_calls:
            if all_tool_names:
                log.info(f"reason_loop: answered after {step} step(s), tools used: {all_tool_names}")
            else:
                log.info(f"reason_loop: answered with NO tool calls (step {step}) — "
                         f"model may not support tools, or judged none were needed")
            _record_tool_usage(bool(all_tool_names), all_tool_names, step)
            return _clean(msg.content)

        names_this_step = [tc.function.name for tc in msg.tool_calls]
        all_tool_names.extend(names_this_step)
        log.info(f"reason_loop step {step}: model called tool(s) {names_this_step}")

        # Record the assistant's tool-call turn
        messages.append({
            "role": "assistant",
            "content": _clean(msg.content),
            "tool_calls": [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in msg.tool_calls
            ],
        })
        # Execute each requested tool and feed results back
        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except Exception:
                args = {}
            try:
                result = tool_impls[name](**args) if name in tool_impls else f"Unknown tool: {name}"
                log.info(f"reason_loop: tool {name}({args}) -> {str(result)[:150]!r}")
            except Exception as e:
                log.error(f"Tool {name} failed: {e}")
                result = f"Tool error: {e}"
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": str(result)[:4000],
            })

    # Ran out of steps — force a final answer without further tools
    log.warning(f"reason_loop: hit max_steps={max_steps} without a final answer; forcing one")
    _record_tool_usage(bool(all_tool_names), all_tool_names, max_steps)
    final = _create(messages=messages, temperature=temperature)
    return _clean(final.choices[0].message.content) or "I couldn't finish reasoning through that."


def transcribe(file_bytes: bytes, filename: str = "voice.ogg") -> str:
    """Transcribe an audio clip via Groq Whisper. Returns the text."""
    if _groq_client is None:
        raise RuntimeError("GROQ_API_KEY not set — voice transcription unavailable.")
    resp = _groq_client.audio.transcriptions.create(
        model=WHISPER_MODEL,
        file=(filename, file_bytes),
    )
    return (getattr(resp, "text", "") or "").strip()


def vision(image_bytes: bytes, prompt: str, mime: str = "image/jpeg", temperature: float = 0.3) -> str:
    """Ask the vision model about an image. Returns the text content."""
    if _BRAIN_IS_VL or _groq_client is None:
        client, models = _client, None      # the brain sees images itself
    else:
        client, models = _groq_client, [VISION_MODEL]
    b64 = base64.b64encode(image_bytes).decode()
    resp = _create(
        client=client,
        models=models,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ],
        }],
        temperature=temperature,
    )
    return _clean(resp.choices[0].message.content)
