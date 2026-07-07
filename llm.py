"""
LLM clients. The reasoning brain is provider-configurable (Groq, Moonshot/Kimi,
etc. — anything OpenAI-compatible). Audio transcription + vision stay on Groq,
which is free and reliable for those (Moonshot has no Whisper endpoint).
"""

import base64
import json
import logging
import os

from openai import OpenAI

log = logging.getLogger(__name__)

GROQ_KEY = os.environ.get("GROQ_API_KEY", "").strip()

# ── Primary reasoning brain (configurable provider) ───────────────────────────
# Point at Kimi:      LLM_API_KEY=<moonshot key>
#                     LLM_BASE_URL=https://api.moonshot.ai/v1
#                     LLM_MODEL=kimi-k2-0905-preview   (or kimi-latest)
# Point at NVIDIA NIM: LLM_API_KEY=<nvapi-... key from build.nvidia.com>
#                     LLM_BASE_URL=https://integrate.api.nvidia.com/v1
#                     LLM_MODEL=nvidia/llama-3.1-nemotron-70b-instruct   (recommended —
#                       NVIDIA's own fine-tune, tuned specifically for instruction-
#                       following and tool-call execution; confirmed OpenAI-compatible
#                       function calling, needed for the !ask reasoning loop and the
#                       tool-grounded coach in messaging.ask_agent. Alternative worth
#                       A/B testing: "mistralai/mistral-nemotron", built by NVIDIA/
#                       Mistral specifically for agentic workflows + function calling.)
# Default: Groq Llama.
LLM_API_KEY  = os.environ.get("LLM_API_KEY", GROQ_KEY).strip()
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.groq.com/openai/v1").strip()
MODEL        = os.environ.get("LLM_MODEL", "llama-3.3-70b-versatile").strip()

if not LLM_API_KEY:
    raise RuntimeError("Set LLM_API_KEY (or GROQ_API_KEY) for the chat model.")

_client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)
log.info(f"LLM brain: base={LLM_BASE_URL} model={MODEL}")

# ── Groq client for audio (Whisper) + vision ──────────────────────────────────
WHISPER_MODEL = os.environ.get("GROQ_WHISPER_MODEL", "whisper-large-v3").strip()
VISION_MODEL  = os.environ.get("GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct").strip()
_groq_client  = OpenAI(api_key=GROQ_KEY, base_url="https://api.groq.com/openai/v1") if GROQ_KEY else None


def chat(messages: list, temperature: float = 0.7) -> str:
    """Call the LLM and return the text content (never None)."""
    resp = _client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=temperature,
    )
    return resp.choices[0].message.content or ""


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
        resp = _client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=tools,
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
            return msg.content or ""

        names_this_step = [tc.function.name for tc in msg.tool_calls]
        all_tool_names.extend(names_this_step)
        log.info(f"reason_loop step {step}: model called tool(s) {names_this_step}")

        # Record the assistant's tool-call turn
        messages.append({
            "role": "assistant",
            "content": msg.content or "",
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
    final = _client.chat.completions.create(
        model=MODEL, messages=messages, temperature=temperature,
    )
    return final.choices[0].message.content or "I couldn't finish reasoning through that."


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
    client = _groq_client or _client       # prefer Groq for vision; fall back to brain
    model  = VISION_MODEL if _groq_client else MODEL
    b64 = base64.b64encode(image_bytes).decode()
    resp = client.chat.completions.create(
        model=model,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ],
        }],
        temperature=temperature,
    )
    return resp.choices[0].message.content or ""
