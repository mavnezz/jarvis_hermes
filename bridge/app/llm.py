"""Hermes agent client.

Hermes exposes an OpenAI-compatible `/v1/chat/completions` endpoint guarded by
a bearer token. The active model lives in `~/.hermes/config.yaml` on the server
— the `model` field in the request is accepted but cosmetic.
"""

from __future__ import annotations

import json
import logging
from typing import AsyncIterator

import httpx

from .config import Config

_LOG = logging.getLogger(__name__)


class HermesError(RuntimeError):
    pass


class HermesClient:
    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        headers = {"Content-Type": "application/json"}
        if cfg.hermes_key:
            headers["Authorization"] = f"Bearer {cfg.hermes_key}"
        self._client = httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(cfg.hermes_timeout_s, connect=10.0),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def health(self) -> bool:
        url = self._cfg.hermes_url.rstrip("/").removesuffix("/v1") + "/v1/health"
        try:
            response = await self._client.get(url, timeout=5.0)
            return response.status_code == 200
        except httpx.HTTPError as exc:
            _LOG.warning("hermes health check failed: %s", exc)
            return False

    async def stream(self, messages: list[dict]) -> AsyncIterator[str]:
        """Yield response text as it is generated."""
        if not self._cfg.hermes_stream:
            yield await self._complete(messages)
            return

        payload = {"model": self._cfg.hermes_model, "messages": messages, "stream": True}
        async with self._client.stream("POST", self._cfg.chat_url, json=payload) as response:
            if response.status_code != 200:
                body = (await response.aread()).decode("utf-8", "replace")[:400]
                raise HermesError(f"hermes returned {response.status_code}: {body}")

            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    if data == "[DONE]":
                        break
                    continue
                try:
                    event = json.loads(data)
                except ValueError:
                    _LOG.debug("skipping unparseable sse chunk: %r", data[:120])
                    continue
                for choice in event.get("choices") or []:
                    token = (choice.get("delta") or {}).get("content")
                    if token:
                        yield token

    async def _complete(self, messages: list[dict]) -> str:
        payload = {"model": self._cfg.hermes_model, "messages": messages, "stream": False}
        response = await self._client.post(self._cfg.chat_url, json=payload)
        if response.status_code != 200:
            raise HermesError(f"hermes returned {response.status_code}: {response.text[:400]}")
        body = response.json()
        choices = body.get("choices") or []
        if not choices:
            raise HermesError("hermes returned no choices")
        return (choices[0].get("message") or {}).get("content") or ""
