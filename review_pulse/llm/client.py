"""Thin wrapper around the Groq API.

Isolates the Groq SDK so the model/provider can change without touching pipeline
logic. Supports plain chat completion and JSON-mode structured output (used by
theming and summarization in later phases).

The Groq SDK is imported lazily so that a no-op run (and importing this module)
works even when the package or API key is not present.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "llama-3.3-70b-versatile"


class LlmClient:
    """Groq-backed chat/JSON client."""

    def __init__(
        self,
        api_key: str | None,
        model: str = DEFAULT_MODEL,
        temperature: float = 0.2,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self._client: Any | None = None

    @classmethod
    def from_config(cls, config: Any) -> "LlmClient":
        """Build a client from a RunConfig.groq section."""
        groq = config.groq
        return cls(
            api_key=groq.api_key,
            model=groq.model,
            temperature=groq.temperature,
        )

    def _ensure_client(self) -> Any:
        """Lazily construct the Groq SDK client, validating the API key."""
        if self._client is not None:
            return self._client
        if not self.api_key:
            raise RuntimeError(
                "GROQ_API_KEY is not set. Add it to your .env before running "
                "stages that call the LLM."
            )
        try:
            from groq import Groq  # noqa: PLC0415  (lazy import by design)
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise RuntimeError(
                "The 'groq' package is not installed. Run: pip install -r requirements.txt"
            ) from exc

        self._client = Groq(api_key=self.api_key)
        return self._client

    def complete(self, system: str, user: str) -> str:
        """Return a plain-text completion for a system+user prompt."""
        client = self._ensure_client()
        response = client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return response.choices[0].message.content or ""

    def complete_json(self, system: str, user: str) -> str:
        """Return a JSON string using Groq JSON mode (structured output)."""
        client = self._ensure_client()
        response = client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return response.choices[0].message.content or "{}"
