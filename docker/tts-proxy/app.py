"""Idle-aware proxy in front of vLLM-Omni.

vLLM-Omni keeps the model resident and cannot release the GPU on its own, so
this proxy owns the lifecycle:

* after `TTS_IDLE_SECONDS` with no traffic it calls the Omni sleep endpoint,
  which offloads the weights to host RAM and drops the KV cache (36 GB -> 2.6 GB
  of VRAM in practice);
* the next request wakes the engine, resumes admission, then is forwarded
  untouched — callers never see the transition beyond a few seconds of extra
  latency.

Sleep is level 1 (offload, not discard) so waking is a host-to-device copy
rather than a model reload.
"""

import asyncio
import logging
import os
import time

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

UPSTREAM = os.getenv("TTS_UPSTREAM", "http://tts-vllm:8000").rstrip("/")
IDLE_SECONDS = float(os.getenv("TTS_IDLE_SECONDS", "300"))
CHECK_INTERVAL = float(os.getenv("TTS_IDLE_CHECK_INTERVAL", "15"))
STAGES = [int(s) for s in os.getenv("TTS_STAGES", "0,1").split(",") if s.strip()]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("tts-proxy")

app = FastAPI(title="vLLM-Omni idle proxy")
# TTS responses are a few MB; reading them fully keeps the forwarding path simple.
client = httpx.AsyncClient(timeout=httpx.Timeout(900.0, connect=10.0))

lock = asyncio.Lock()
state = {"sleeping": False, "last_active": time.monotonic(), "in_flight": 0}


async def _control(path: str, payload: dict | None = None) -> httpx.Response:
    response = await client.post(f"{UPSTREAM}{path}", json=payload)
    response.raise_for_status()
    return response


async def wake() -> None:
    async with lock:
        if not state["sleeping"]:
            return
        started = time.monotonic()
        await _control("/v1/omni/wakeup", {"stage_ids": STAGES})
        # wake_up restores the weights but leaves admission paused for AR
        # engines; /resume is what makes generate() actually proceed.
        await _control("/resume")
        state["sleeping"] = False
        state["last_active"] = time.monotonic()
        log.info("woke engine in %.1fs", time.monotonic() - started)


async def sleep() -> None:
    async with lock:
        if state["sleeping"]:
            return
        started = time.monotonic()
        await _control("/v1/omni/sleep", {"stage_ids": STAGES, "level": 1})
        state["sleeping"] = True
        log.info("engine asleep after %.1fs idle (took %.1fs)",
                 time.monotonic() - state["last_active"], time.monotonic() - started)


async def idle_monitor() -> None:
    while True:
        await asyncio.sleep(CHECK_INTERVAL)
        idle_for = time.monotonic() - state["last_active"]
        if state["sleeping"] or state["in_flight"] or idle_for < IDLE_SECONDS:
            continue
        try:
            await sleep()
        except Exception as exc:  # keep watching even if one sleep attempt fails
            log.warning("sleep failed: %s", exc)


@app.on_event("startup")
async def _startup() -> None:
    asyncio.create_task(idle_monitor())
    log.info("proxying %s, idle timeout %.0fs", UPSTREAM, IDLE_SECONDS)


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "sleeping": state["sleeping"], "upstream": UPSTREAM})


@app.post("/_proxy/wake")
async def force_wake() -> JSONResponse:
    await wake()
    return JSONResponse({"sleeping": state["sleeping"]})


@app.post("/_proxy/sleep")
async def force_sleep() -> JSONResponse:
    await sleep()
    return JSONResponse({"sleeping": state["sleeping"]})


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
async def forward(path: str, request: Request) -> Response:
    await wake()

    state["in_flight"] += 1
    try:
        body = await request.body()
        headers = {k: v for k, v in request.headers.items() if k.lower() != "host"}
        upstream = await client.request(
            request.method,
            f"{UPSTREAM}/{path}",
            params=request.query_params,
            content=body,
            headers=headers,
        )
    finally:
        state["in_flight"] -= 1
        state["last_active"] = time.monotonic()

    passthrough = {
        k: v for k, v in upstream.headers.items()
        if k.lower() in {"content-type", "content-length", "cache-control"}
    }
    return Response(content=upstream.content, status_code=upstream.status_code, headers=passthrough)
