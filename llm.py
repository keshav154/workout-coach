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
# Point at Kimi:   LLM_API_KEY=<moonshot key>
#                  LLM_BASE_URL=https://api.moonshot.ai/v1
#                  LLM_MODEL=kimi-k2-0905-preview   (or kimi-latest)
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


def reason_loop(messages: list, tools: list, tool_impls: dict,
                max_steps: int = 5, temperature: float = 0.2) -> str:
    """
    ReAct-style reasoning loop: the model thinks, optionally calls a tool,
    observes the result, and repeats until it produces a final answer.
    `tool_impls` maps tool name -> python callable(**args) returning a string.
    """
    for _ in range(max_steps):
        resp = _client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=tools,
            temperature=temperature,
        )
        msg = resp.choices[0].message
        if not msg.tool_calls:
            return msg.content or ""

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
            except Exception as e:
                log.error(f"Tool {name} failed: {e}")
                result = f"Tool error: {e}"
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": str(result)[:4000],
            })

    # Ran out of steps — force a final answer without further tools
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
