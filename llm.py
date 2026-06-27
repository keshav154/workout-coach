"""
Single Groq LLM client shared across the app.
"""

import logging
import os

from openai import OpenAI

log = logging.getLogger(__name__)

GROQ_KEY = os.environ["GROQ_API_KEY"].strip()
log.info(f"GROQ_API_KEY starts with: {GROQ_KEY[:8]}... length: {len(GROQ_KEY)}")

MODEL   = "llama-3.3-70b-versatile"
_client = OpenAI(api_key=GROQ_KEY, base_url="https://api.groq.com/openai/v1")


def chat(messages: list, temperature: float = 0.7) -> str:
    """Call the LLM and return the text content (never None)."""
    resp = _client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=temperature,
    )
    return resp.choices[0].message.content or ""
