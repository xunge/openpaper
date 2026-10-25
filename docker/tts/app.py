"""OpenAI-compatible TTS service backed by Qwen3-TTS (CustomVoice).

Open Paper already talks to text-to-speech through the OpenAI `audio/speech`
shape (Azure today). So this service exposes exactly that endpoint — same path,
same JSON body, WAV bytes back — which means the app needs no provider-specific
code, only a different base URL.

The model is loaded once at startup and guarded by a lock: a single 1.7B model
on one GPU is not re-entrant, and Open Paper calls this once per text chunk.
"""

import io
import logging
import os
import threading

import soundfile as sf
import torch
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from qwen_tts import Qwen3TTSModel

logger = logging.getLogger("qwen-tts")
logging.basicConfig(level=logging.INFO)

MODEL_PATH = os.getenv("QWEN_TTS_MODEL", "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice")
DEVICE = os.getenv("QWEN_TTS_DEVICE", "cuda:0")
DEFAULT_SPEAKER = os.getenv("QWEN_TTS_SPEAKER", "Serena")
DEFAULT_LANGUAGE = os.getenv("QWEN_TTS_LANGUAGE", "Auto")

# The nine built-in timbres of the CustomVoice checkpoints.
SPEAKERS = {
    "vivian": "Vivian",
    "serena": "Serena",
    "uncle_fu": "Uncle_Fu",
    "dylan": "Dylan",
    "eric": "Eric",
    "ryan": "Ryan",
    "aiden": "Aiden",
    "ono_anna": "Ono_Anna",
    "sohee": "Sohee",
}

# OpenAI's stock voice names, mapped onto the nearest Chinese-capable timbre.
# Open Paper's UI sends these, so translating here keeps the app unchanged.
OPENAI_VOICE_ALIASES = {
    "alloy": "Serena",
    "ash": "Ryan",
    "ballad": "Vivian",
    "coral": "Serena",
    "echo": "Aiden",
    "fable": "Dylan",
    "nova": "Vivian",
    "onyx": "Uncle_Fu",
    "sage": "Serena",
    "shimmer": "Vivian",
    "verse": "Ryan",
}

app = FastAPI(title="Qwen3-TTS (OpenAI-compatible)")
_model: Qwen3TTSModel | None = None
_lock = threading.Lock()


@app.on_event("startup")
def load_model() -> None:
    global _model
    logger.info("Loading %s on %s", MODEL_PATH, DEVICE)
    _model = Qwen3TTSModel.from_pretrained(
        MODEL_PATH,
        device_map=DEVICE,
        dtype=torch.bfloat16,
        # sdpa ships with torch; flash_attention_2 would need a compiled wheel.
        attn_implementation="sdpa",
    )
    logger.info("Model ready; speakers=%s", ", ".join(SPEAKERS.values()))


class SpeechRequest(BaseModel):
    """The subset of OpenAI's audio/speech body this service honours."""

    model: str | None = None
    input: str
    voice: str | None = None
    response_format: str | None = "wav"
    speed: float | None = None
    instructions: str | None = None
    language: str | None = None


@app.get("/health")
def health(response: Response) -> dict:
    """503 until the weights are resident, so compose waits instead of routing
    an early synthesis request into a half-initialised model."""
    if _model is None:
        response.status_code = 503
        return {"status": "loading", "model": MODEL_PATH, "device": DEVICE}
    return {"status": "ok", "model": MODEL_PATH, "device": DEVICE}


@app.get("/v1/voices")
def voices() -> dict:
    return {"voices": list(SPEAKERS.values())}


def _resolve_speaker(voice: str | None) -> str:
    if not voice:
        return DEFAULT_SPEAKER
    key = voice.strip().lower()
    if key in SPEAKERS:
        return SPEAKERS[key]
    if key in OPENAI_VOICE_ALIASES:
        return OPENAI_VOICE_ALIASES[key]
    # Unknown name: fall back rather than 400, so a UI change here can't break
    # audio generation entirely.
    logger.warning("Unknown voice %r; using %s", voice, DEFAULT_SPEAKER)
    return DEFAULT_SPEAKER


@app.post("/v1/audio/speech")
def speech(request: SpeechRequest) -> Response:
    if _model is None:
        raise HTTPException(status_code=503, detail="Model is still loading")
    text = (request.input or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="input must not be empty")

    speaker = _resolve_speaker(request.voice)
    language = request.language or DEFAULT_LANGUAGE
    instruct = request.instructions or ""

    try:
        with _lock:
            wavs, sample_rate = _model.generate_custom_voice(
                text=text,
                language=language,
                speaker=speaker,
                instruct=instruct,
            )
    except Exception as exc:  # surfaced to the caller instead of a bare 500
        logger.exception("Synthesis failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    buffer = io.BytesIO()
    # PCM_16 explicitly: Open Paper stitches the per-chunk WAVs by copying raw
    # frames, which only lines up if every chunk uses the same sample format.
    sf.write(buffer, wavs[0], sample_rate, format="WAV", subtype="PCM_16")
    return Response(
        content=buffer.getvalue(),
        media_type="audio/wav",
        headers={
            "X-TTS-Model": MODEL_PATH,
            "X-TTS-Speaker": speaker,
        },
    )
