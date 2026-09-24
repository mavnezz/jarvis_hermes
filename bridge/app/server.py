"""WebSocket server the Voice PE firmware connects to."""

from __future__ import annotations

import asyncio
import logging

import websockets
from websockets.exceptions import ConnectionClosed

from .config import Config, load
from .llm import HermesClient
from .session import Session
from .stt import Transcriber
from .tts import Synthesizer

_LOG = logging.getLogger(__name__)


def _peer_name(websocket) -> str:
    address = getattr(websocket, "remote_address", None)
    if isinstance(address, tuple) and address:
        return f"{address[0]}:{address[1]}" if len(address) > 1 else str(address[0])
    return "device"


async def serve(cfg: Config) -> None:
    transcriber = Transcriber(cfg)
    synthesizer = Synthesizer(cfg)
    hermes = HermesClient(cfg)

    if await hermes.health():
        _LOG.info("hermes reachable at %s", cfg.hermes_url)
    else:
        _LOG.warning("hermes NOT reachable at %s — starting anyway", cfg.hermes_url)

    async def handler(websocket) -> None:
        peer = _peer_name(websocket)
        session = Session(
            websocket,
            cfg=cfg,
            transcriber=transcriber,
            synthesizer=synthesizer,
            hermes=hermes,
            peer=peer,
        )
        try:
            await session.run()
        except ConnectionClosed:
            _LOG.info("[%s] disconnected", peer)
        except Exception:  # noqa: BLE001
            _LOG.exception("[%s] session crashed", peer)

    try:
        async with websockets.serve(
            handler,
            cfg.host,
            cfg.port,
            max_size=None,
            ping_interval=20,
            ping_timeout=20,
        ):
            _LOG.info("listening on ws://%s:%d", cfg.host, cfg.port)
            await asyncio.Future()
    finally:
        await hermes.aclose()


def run() -> None:
    import os

    logging.basicConfig(
        level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").strip().upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    # Beide reden auf INFO ueber jede einzelne Anfrage. httpx wuerde damit
    # jeden Hermes-Aufruf und jeden Modell-Download protokollieren.
    for noisy in ("websockets", "httpx", "httpcore", "huggingface_hub", "filelock"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    try:
        cfg = load()
    except ValueError as exc:
        # With `restart: unless-stopped` a bad value would otherwise produce an
        # endless loop of stack traces. Say what is wrong and stop.
        _LOG.error("Konfigurationsfehler: %s", exc)
        _LOG.error("Bitte die betroffene Zeile in .env pruefen und neu starten.")
        raise SystemExit(2) from None
    try:
        asyncio.run(serve(cfg))
    except KeyboardInterrupt:
        _LOG.info("shutting down")
