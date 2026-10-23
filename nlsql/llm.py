"""The only module that talks to a language model. Swap it out in tests."""

from __future__ import annotations

import json
import os
from typing import Any, Protocol

DEFAULT_MODEL = os.environ.get("NLSQL_MODEL", "claude-opus-5-5")
DEFAULT_EFFORT = os.environ.get("NLSQL_EFFORT", "medium")


class LLM(Protocol):
    def json(self, system: str, messages: list[dict], schema: dict) -> dict[str, Any]: ...


class ModelRefused(RuntimeError):
    pass


class ClaudeLLM:
    """Claude via the Anthropic API, returning JSON that matches a schema."""

    def __init__(self, model: str = DEFAULT_MODEL, effort: str = DEFAULT_EFFORT, fallbacks: bool = True):
        import anthropic

        self.client = anthropic.Anthropic()
        self.model = model
        self.effort = effort
        # Server-side fallback re-runs a declined request on another model inside the same call.
        self.fallbacks = fallbacks and os.environ.get("NLSQL_FALLBACKS", "1") != "0"

    def json(self, system: str, messages: list[dict], schema: dict) -> dict[str, Any]:
        kwargs: dict[str, Any] = dict(
            model=self.model,
            max_tokens=16000,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=messages,
            output_config={
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": schema},
            },
        )
        if self.fallbacks:
            resp = self.client.beta.messages.create(
                betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs
            )
        else:
            resp = self.client.messages.create(**kwargs)

        if resp.stop_reason == "refusal":
            raise ModelRefused(getattr(resp.stop_details, "explanation", None) or "Model declined.")
        if resp.stop_reason == "max_tokens":
            raise RuntimeError("Model output was cut off (max_tokens).")
        text = next(b.text for b in resp.content if b.type == "text")
        return json.loads(text)


class OllamaLLM:
    """A free local model served by Ollama (https://ollama.com). No API key needed.

    Smaller local models make more wrong-but-valid queries than Claude; the checks
    catch invalid SQL, not SQL that answers the wrong question.
    """

    def __init__(self, model: str | None = None, host: str | None = None, timeout: float = 300):
        self.model = model or os.environ.get("NLSQL_OLLAMA_MODEL", "qwen2.5-coder:7b")
        self.host = (host or os.environ.get("OLLAMA_HOST", "http://localhost:11434")).rstrip("/")
        if not self.host.startswith("http"):
            self.host = "http://" + self.host
        self.timeout = timeout

    def json(self, system: str, messages: list[dict], schema: dict) -> dict[str, Any]:
        import urllib.error
        import urllib.request

        # Small models follow the format better when the schema is also in the prompt.
        system = f"{system}\n\nRespond with only a JSON object matching this JSON schema:\n{json.dumps(schema)}"
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *messages],
            "format": schema,
            "stream": False,
            "options": {"temperature": 0},
        }
        req = urllib.request.Request(
            f"{self.host}/api/chat", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Ollama error {e.code}: {e.read().decode(errors='replace')[:300]}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"Cannot reach Ollama at {self.host}. Is the Ollama app running? ({e.reason})") from e
        return json.loads(data["message"]["content"])


def make_llm() -> LLM:
    """Pick the model from NLSQL_LLM: 'claude' (default, needs ANTHROPIC_API_KEY) or 'ollama' (free, local)."""
    choice = os.environ.get("NLSQL_LLM", "claude").strip().lower()
    if choice == "ollama":
        return OllamaLLM()
    if choice == "claude":
        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            raise SystemExit(
                "No ANTHROPIC_API_KEY is set, so Claude can't be used.\n"
                "To use the free local model instead, run Ollama and set NLSQL_LLM=ollama\n"
                '  PowerShell: $env:NLSQL_LLM = "ollama"'
            )
        return ClaudeLLM()
    raise ValueError(f"NLSQL_LLM must be 'claude' or 'ollama', not {choice!r}")
