"""Retired-model fallback: a 404 model_not_found on the primary must fall
through to the next model instead of killing every AI feature."""
from types import SimpleNamespace

import pytest


class _Gone(Exception):
    status_code = 404

    def __str__(self):
        return "Error code: 404 - model_not_found: The model `x` does not exist"


def _fake_client(fail_models, seen):
    def create(model, **kw):
        seen.append(model)
        if model in fail_models:
            raise _Gone()
        msg = SimpleNamespace(content=f"ok from {model}", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def test_falls_back_when_primary_retired(monkeypatch):
    import llm
    seen = []
    fake = _fake_client({"dead-model"}, seen)
    monkeypatch.setattr(llm, "_client", fake)
    monkeypatch.setattr(llm, "MODEL", "dead-model")
    monkeypatch.setattr(llm, "FALLBACK_MODELS", ["live-model"])
    resp = llm._create(messages=[{"role": "user", "content": "hi"}])
    assert resp.choices[0].message.content == "ok from live-model"
    assert seen == ["dead-model", "live-model"]
    assert llm.MODEL == "live-model"      # sticks with the working model
    assert llm.last_error is None


def test_non_model_errors_are_not_swallowed(monkeypatch):
    import llm

    class _RateLimit(Exception):
        status_code = 429

    def create(model, **kw):
        raise _RateLimit("rate limited")
    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(llm, "_client", fake)
    monkeypatch.setattr(llm, "FALLBACK_MODELS", ["other"])
    with pytest.raises(_RateLimit):
        llm._create(messages=[])
    assert "rate limited" in llm.last_error


def test_clean_strips_reasoning_trace():
    import llm
    assert llm._clean("<think>hmm, protein...</think>\nEat paneer.") == "Eat paneer."
    assert llm._clean("hmm reasoning only closing tag</think> Do 3 sets.") == "Do 3 sets."
    assert llm._clean(None) == ""
    assert llm._clean("plain answer") == "plain answer"


def test_reasoning_is_per_call(monkeypatch):
    import llm
    seen = []

    def create(model, **kw):
        seen.append(kw)
        msg = SimpleNamespace(content="ok", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])
    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(llm, "_client", fake)
    monkeypatch.setattr(llm, "_IS_NIM", True)
    monkeypatch.setattr(llm, "LLM_THINKING", "auto")
    monkeypatch.setattr(llm, "MODEL", "nvidia/nemotron-3.5-super-vl-120b-a12b")
    llm.chat([], think=True)
    llm.chat([])
    assert seen[0]["extra_body"]["reasoning_effort"] == "high"
    assert seen[0]["timeout"] == llm.LLM_THINK_TIMEOUT
    assert seen[1]["extra_body"]["reasoning_effort"] == "none"
    assert "chat_template_kwargs" not in seen[1]["extra_body"]
    assert seen[1]["timeout"] == llm.LLM_TIMEOUT
    monkeypatch.setattr(llm, "LLM_THINKING", "never")
    llm.chat([], think=True)
    assert seen[2]["extra_body"]["reasoning_effort"] == "none"


def test_non_reasoning_models_get_no_extra_params(monkeypatch):
    import llm
    monkeypatch.setattr(llm, "_IS_NIM", True)
    monkeypatch.setattr(llm, "LLM_THINKING", "auto")
    old = llm._reasoning_params("nvidia/nemotron-3-super-120b-a12b", False)
    assert old == {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}
    assert llm._reasoning_params("qwen/qwen3.8-27b", True) == {}
    assert llm._reasoning_params("openai/gpt-oss-120b", True)["extra_body"]["reasoning_effort"] == "high"
