from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv
from elevenlabs.client import ElevenLabs
from elevenlabs import VoiceSettings
from openai import OpenAI
import json
import os
import re
import time

from persona import REZE_SYSTEM_PROMPT, VOICE_ID, VOICE_SETTINGS
from memory import trimmed_history

# ── Preprocesado TTS ──────────────────────────────────────────────
# Onomatopeyas que ElevenLabs deletrea → equivalente fonético que suena natural
_TTS_MAP = [
    (re.compile(r'\bTsk\b'),        'ts.'),       # click dental
    (re.compile(r'\btsk\b'),        'ts.'),
    (re.compile(r'\bTch\b'),        'ch,'),       # fricativo
    (re.compile(r'\btch\b'),        'ch,'),
    (re.compile(r'\bHmph\b'),       'Mmf.'),      # exhalación nasal
    (re.compile(r'\bhmph\b'),       'mmf.'),
    (re.compile(r'\bHmpf\b'),       'Mmf.'),
    (re.compile(r'\bhmpf\b'),       'mmf.'),
    (re.compile(r'\bNgh\b'),        '.'),         # gruñido → pausa
    (re.compile(r'\bngh\b'),        '.'),
    (re.compile(r'\bPfft\b'),       'pff.'),      # bufido
    (re.compile(r'\bpfft\b'),       'pff.'),
    (re.compile(r'\bUgh\b'),        'ugh,'),      # queja
    (re.compile(r'\bugh\b'),        'ugh,'),
    (re.compile(r'\bHmm+\b'),       'Hmm,'),      # duda — normaliza "Hmmm" → "Hmm"
    (re.compile(r'\bhmm+\b'),       'hmm,'),
    (re.compile(r'\bHah\b'),        'ja.'),       # risa seca
    (re.compile(r'\bhah\b'),        'ja.'),
    (re.compile(r'\bHeh\b'),        'je.'),       # risa irónica
    (re.compile(r'\bheh\b'),        'je.'),
    (re.compile(r'\.{3,}'),         '...'),       # normalizar "......" → "..."
]

def preprocess_tts(text: str) -> str:
    for pattern, replacement in _TTS_MAP:
        text = pattern.sub(replacement, text)
    # Limpiar puntuación duplicada y espacios extras
    text = re.sub(r'([.,!?])([.,!?])+', r'\1', text)
    text = re.sub(r' {2,}', ' ', text)
    return text.strip()

load_dotenv()

app = FastAPI()

el_client = ElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))

deepseek = OpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com",
    timeout=15,
)

SESSION_TTL = 3600  # 1 hora en segundos

sessions: dict[str, dict] = {}


def get_session(session_id: str) -> list[dict]:
    now = time.time()
    # limpieza lazy de sesiones expiradas
    expired = [sid for sid, s in sessions.items() if now - s["last_active"] > SESSION_TTL]
    for sid in expired:
        del sessions[sid]

    if session_id not in sessions:
        sessions[session_id] = {"history": [], "created_at": now, "last_active": now}
    else:
        sessions[session_id]["last_active"] = now

    return sessions[session_id]["history"]


class ChatRequest(BaseModel):
    message: str
    session_id: str


FALLBACK = {"emocion": "neutral", "motion": "talk"}


@app.post("/chat")
def chat(req: ChatRequest):
    history = get_session(req.session_id)
    history.append({"role": "user", "content": req.message})

    try:
        response = deepseek.chat.completions.create(
            model="deepseek-chat",
            max_tokens=200,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": REZE_SYSTEM_PROMPT}, *trimmed_history(history)],
        )
    except Exception as e:
        history.pop()
        raise HTTPException(status_code=502, detail={"error": "llm_unavailable", "message": str(e)})

    raw = response.choices[0].message.content
    try:
        data = json.loads(raw)
        respuesta = str(data.get("respuesta", "")).strip()
        emocion   = str(data.get("emocion",   FALLBACK["emocion"]))
        motion    = str(data.get("motion",    FALLBACK["motion"]))
        if not respuesta:
            raise ValueError("empty respuesta")
    except (json.JSONDecodeError, AttributeError, ValueError):
        respuesta = raw.strip() or "..."
        emocion   = FALLBACK["emocion"]
        motion    = FALLBACK["motion"]
        raw       = json.dumps({"respuesta": respuesta, "emocion": emocion, "motion": motion})

    # Guardar JSON completo en historial para que el modelo mantenga el formato
    history.append({"role": "assistant", "content": raw})

    return JSONResponse({"respuesta": respuesta, "emocion": emocion, "motion": motion})


class TTSRequest(BaseModel):
    text: str


@app.post("/tts")
def tts(req: TTSRequest):
    try:
        audio_stream = el_client.text_to_speech.convert(
            text=preprocess_tts(req.text),
            voice_id=VOICE_ID,
            model_id="eleven_flash_v2_5",
            output_format="mp3_44100_128",
            voice_settings=VoiceSettings(**VOICE_SETTINGS),
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail={"error": "tts_unavailable", "message": str(e)})

    return StreamingResponse(audio_stream, media_type="audio/mpeg")


@app.delete("/history")
def clear_history(session_id: str):
    if session_id in sessions:
        sessions[session_id]["history"].clear()
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index():
    with open("static/index.html", "r") as f:
        return f.read()


app.mount("/static", StaticFiles(directory="static"), name="static")
