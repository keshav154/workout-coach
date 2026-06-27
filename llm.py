"""
Single Groq LLM client shared across the app.
"""

import base64
import json
import logging
import os

from openai import OpenAI

log = logging.getLogger(__name__)

GROQ_KEY = os.environ["GROQ_API_KEY"].strip()
log.info(f"GROQ_API_KEY starts with: {GROQ_KEY[:8]}... length: {len(GROQ_KEY)}")

MODEL         = "llama-3.3-70b-versatile"
WHISPER_MODEL = os.environ.get("GROQ_WHISPER_MODEL", "whisper-large-v3").strip()
VISION_MODEL  = os.environ.get("GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct").strip()

_client = OpenAI(api_key=GROQ_KEY, base_url="https://api.groq.com/openai/v1")


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
    resp = _client.audio.transcriptions.create(
        model=WHISPER_MODEL,
        file=(filename, file_bytes),
    )
    return (getattr(resp, "text", "") or "").strip()


def vision(image_bytes: bytes, prompt: str, mime: str = "image/jpeg", temperature: float = 0.3) -> str:
    """Ask the vision model about an image. Returns the text content."""
    b64 = base64.b64encode(image_bytes).decode()
    resp = _client.chat.completions.create(
        model=VISION_MODEL,
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
