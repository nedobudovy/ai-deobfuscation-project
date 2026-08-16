"""Model provider implementations — all return (text, metadata_dict)."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any

import httpx


class ModelError(Exception):
    pass


class BaseModel(ABC):
    def __init__(self, model_id: str, api_key: str, timeout: int = 120):
        self.model_id = model_id
        self.api_key = api_key
        self.timeout = timeout

    @abstractmethod
    def complete(self, messages: list[dict]) -> tuple[str, dict]:
        """Returns (response_text, metadata)."""
        ...

    def _base_metadata(self) -> dict:
        return {"model_id": self.model_id, "provider": self.__class__.__name__}

    def _httpx_timeout(self) -> "httpx.Timeout":
        """
        Fine-grained timeouts so a server that establishes a connection then
        stops responding cannot burn an hour. read-timeout is the dominant
        signal here — GitHub Models has been known to hang silently.
        """
        return httpx.Timeout(
            connect=10.0,
            read=float(self.timeout),
            write=30.0,
            pool=10.0,
        )


# ─── OpenAI-compatible (OpenAI, xAI, GitHub Models) ───────────────────────────

class OpenAIModel(BaseModel):
    BASE_URL = "https://api.openai.com/v1"

    def complete(self, messages: list[dict]) -> tuple[str, dict]:
        t0 = time.monotonic()
        with httpx.Client(timeout=self._httpx_timeout()) as client:
            resp = client.post(
                f"{self.BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={"model": self.model_id, "messages": messages},
            )
        resp.raise_for_status()
        data = resp.json()
        latency = time.monotonic() - t0
        text = data["choices"][0]["message"]["content"]
        meta = {
            **self._base_metadata(),
            "latency_s": round(latency, 3),
            "prompt_tokens": data.get("usage", {}).get("prompt_tokens"),
            "completion_tokens": data.get("usage", {}).get("completion_tokens"),
            "total_tokens": data.get("usage", {}).get("total_tokens"),
            "finish_reason": data["choices"][0].get("finish_reason"),
        }
        return text, meta


class XAIModel(OpenAIModel):
    BASE_URL = "https://api.x.ai/v1"


class GitHubModel(OpenAIModel):
    BASE_URL = "https://models.inference.ai.azure.com"


# ─── Anthropic ────────────────────────────────────────────────────────────────

class AnthropicModel(BaseModel):
    def complete(self, messages: list[dict]) -> tuple[str, dict]:
        # Extract system message (Anthropic uses separate param)
        system = ""
        chat_messages = []
        for m in messages:
            if m["role"] == "system":
                system = m["content"]
            else:
                chat_messages.append(m)

        t0 = time.monotonic()
        with httpx.Client(timeout=self._httpx_timeout()) as client:
            resp = client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": self.model_id,
                    "max_tokens": 8096,
                    "system": system,
                    "messages": chat_messages,
                },
            )
        resp.raise_for_status()
        data = resp.json()
        latency = time.monotonic() - t0
        text = data["content"][0]["text"]
        usage = data.get("usage", {})
        meta = {
            **self._base_metadata(),
            "latency_s": round(latency, 3),
            "prompt_tokens": usage.get("input_tokens"),
            "completion_tokens": usage.get("output_tokens"),
            "total_tokens": (usage.get("input_tokens", 0) + usage.get("output_tokens", 0)),
            "finish_reason": data.get("stop_reason"),
        }
        return text, meta


# ─── Google Gemini ────────────────────────────────────────────────────────────

class GeminiModel(BaseModel):
    def complete(self, messages: list[dict]) -> tuple[str, dict]:
        # Merge system + user into Gemini format
        parts = []
        for m in messages:
            role = "user" if m["role"] in ("user", "system") else "model"
            parts.append({"role": role, "parts": [{"text": m["content"]}]})

        # Gemini doesn't support consecutive same-role messages; merge system into first user
        merged: list[dict] = []
        pending_system = ""
        for p in parts:
            if p["role"] == "user" and pending_system:
                merged.append({
                    "role": "user",
                    "parts": [{"text": pending_system + "\n\n" + p["parts"][0]["text"]}],
                })
                pending_system = ""
            elif p["role"] == "user" and not merged:
                merged.append(p)
            elif p["role"] == "user" and merged[-1]["role"] == "model":
                merged.append(p)
            elif p["role"] == "model":
                merged.append(p)
            else:
                pending_system += p["parts"][0]["text"]

        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model_id}:generateContent?key={self.api_key}"
        )
        t0 = time.monotonic()
        with httpx.Client(timeout=self._httpx_timeout()) as client:
            resp = client.post(url, json={"contents": merged})
        resp.raise_for_status()
        data = resp.json()
        latency = time.monotonic() - t0

        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as exc:
            raise ModelError(f"Gemini unexpected response: {data}") from exc

        usage = data.get("usageMetadata", {})
        meta = {
            **self._base_metadata(),
            "latency_s": round(latency, 3),
            "prompt_tokens": usage.get("promptTokenCount"),
            "completion_tokens": usage.get("candidatesTokenCount"),
            "total_tokens": usage.get("totalTokenCount"),
            "finish_reason": data["candidates"][0].get("finishReason"),
        }
        return text, meta


# ─── HuggingFace (Gemma and others) ──────────────────────────────────────────

class HuggingFaceModel(BaseModel):
    def complete(self, messages: list[dict]) -> tuple[str, dict]:
        # Use HF Router (serverless, OpenAI-compatible)
        model_path = self.model_id  # e.g. "Qwen/Qwen2.5-Coder-32B-Instruct"
        url = "https://router.huggingface.co/v1/chat/completions"

        t0 = time.monotonic()
        with httpx.Client(timeout=self._httpx_timeout()) as client:
            resp = client.post(
                url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model_path,
                    "messages": messages,
                    "max_tokens": 4096,
                    "stream": False,
                },
            )
        resp.raise_for_status()
        data = resp.json()
        latency = time.monotonic() - t0

        text = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        meta = {
            **self._base_metadata(),
            "latency_s": round(latency, 3),
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "finish_reason": data["choices"][0].get("finish_reason"),
        }
        return text, meta


# ─── Groq (OpenAI-compatible, very fast free tier) ───────────────────────────

class GroqModel(OpenAIModel):
    BASE_URL = "https://api.groq.com/openai/v1"


# ─── OpenRouter (free models aggregator) ─────────────────────────────────────

class OpenRouterModel(OpenAIModel):
    BASE_URL = "https://openrouter.ai/api/v1"


# ─── Factory ──────────────────────────────────────────────────────────────────

def make_model(model_cfg: dict, api_keys: dict, timeout: int) -> BaseModel:
    """Instantiate the correct model class from config."""
    provider = model_cfg["provider"]
    model_id = model_cfg["id"]
    constructors: dict[str, type[BaseModel]] = {
        "openai": OpenAIModel,
        "anthropic": AnthropicModel,
        "google": GeminiModel,
        "xai": XAIModel,
        "github": GitHubModel,
        "huggingface": HuggingFaceModel,
        "groq": GroqModel,
        "openrouter": OpenRouterModel,
    }
    if provider not in constructors:
        raise ValueError(f"Unknown provider: {provider}")
    key_name = {
        "openai": "openai",
        "anthropic": "anthropic",
        "google": "google",
        "xai": "xai",
        "github": "github",
        "huggingface": "huggingface",
        "groq": "groq",
        "openrouter": "openrouter",
    }[provider]
    api_key = api_keys.get(key_name, "")
    return constructors[provider](model_id=model_id, api_key=api_key, timeout=timeout)
