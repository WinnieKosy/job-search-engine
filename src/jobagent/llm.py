"""Model providers. Everything else talks to the `LLM` interface, so adding a
provider (Anthropic, OpenAI, LM Studio, ...) means adding one class here."""
from __future__ import annotations

import json
import re

import httpx

_THINK = re.compile(r"<think>.*?</think>", re.S)


class LLMError(RuntimeError):
    pass


class LLM:
    def chat(self, system: str, user: str, *, schema: dict | None = None,
             temperature: float = 0.4) -> str:
        raise NotImplementedError

    def json(self, system: str, user: str, schema: dict, temperature: float = 0.1) -> dict:
        raw = self.chat(system, user, schema=schema, temperature=temperature)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", raw, re.S)
            if m:
                return json.loads(m.group(0))
            raise LLMError(f"Model did not return JSON: {raw[:300]}")


class OllamaLLM(LLM):
    def __init__(self, model: str, host: str = "http://localhost:11434", num_ctx: int = 12288):
        self.model, self.host, self.num_ctx = model, host.rstrip("/"), num_ctx

    def chat(self, system, user, *, schema=None, temperature=0.4):
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "stream": False,
            "options": {"temperature": temperature, "num_ctx": self.num_ctx},
        }
        if schema:
            body["format"] = schema
        try:
            r = httpx.post(f"{self.host}/api/chat", json=body, timeout=900)
        except httpx.ConnectError:
            raise LLMError(f"Can't reach Ollama at {self.host}. Start it with `ollama serve`.")
        if r.status_code == 404:
            raise LLMError(f"Model '{self.model}' isn't installed. Run: ollama pull {self.model}")
        r.raise_for_status()
        return _THINK.sub("", r.json()["message"]["content"]).strip()


PROVIDERS = {
    "ollama": lambda c: OllamaLLM(c["model"], c.get("host", "http://localhost:11434"),
                                  c.get("num_ctx", 12288)),
}


def make_llm(cfg: dict) -> LLM:
    provider = cfg.get("provider", "ollama")
    if provider not in PROVIDERS:
        raise LLMError(f"Unknown LLM provider '{provider}'. Known: {', '.join(PROVIDERS)}")
    return PROVIDERS[provider](cfg)
