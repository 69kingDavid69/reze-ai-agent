from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv
from elevenlabs.client import ElevenLabs
from elevenlabs import VoiceSettings
from openai import OpenAI
from dataclasses import asdict
import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager

from persona import (
    REZE_SYSTEM_PROMPT,
    VOICE_ID,
    VOICE_SETTINGS,
    LLM_TEMPERATURE,
    REFLECT_EVERY_N_TURNS,
    REFLECT_PROMPT,
)
from memory import trimmed_history, SessionState, MAX_HISTORY_TURNS
import rag

# ── Preprocesado TTS ──────────────────────────────────────────────
_TTS_MAP = [
    (re.compile(r'\bTsk\b'),        'ts.'),
    (re.compile(r'\btsk\b'),        'ts.'),
    (re.compile(r'\bTch\b'),        'ch,'),
    (re.compile(r'\btch\b'),        'ch,'),
    (re.compile(r'\bHmph\b'),       'Mmf.'),
    (re.compile(r'\bhmph\b'),       'mmf.'),
    (re.compile(r'\bHmpf\b'),       'Mmf.'),
    (re.compile(r'\bhmpf\b'),       'mmf.'),
    (re.compile(r'\bNgh\b'),        '.'),
    (re.compile(r'\bngh\b'),        '.'),
    (re.compile(r'\bPfft\b'),       'pff.'),
    (re.compile(r'\bpfft\b'),       'pff.'),
    (re.compile(r'\bUgh\b'),        'ugh,'),
    (re.compile(r'\bugh\b'),        'ugh,'),
    (re.compile(r'\bHmm+\b'),       'Hmm,'),
    (re.compile(r'\bhmm+\b'),       'hmm,'),
    (re.compile(r'\bHah\b'),        'ja.'),
    (re.compile(r'\bhah\b'),        'ja.'),
    (re.compile(r'\bHeh\b'),        'je.'),
    (re.compile(r'\bheh\b'),        'je.'),
    (re.compile(r'\.{3,}'),         '...'),
]

def preprocess_tts(text: str) -> str:
    for pattern, replacement in _TTS_MAP:
        text = pattern.sub(replacement, text)
    text = re.sub(r'([.,!?])([.,!?])+', r'\1', text)
    text = re.sub(r' {2,}', ' ', text)
    return text.strip()

load_dotenv()

app = FastAPI()

el_client = ElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))

deepseek = OpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com",
    timeout=30,   # reasoner suele tardar más; subimos timeout
)

SESSION_TTL          = 7 * 24 * 3600   # 1 semana (antes era 1 hora)
MAX_USER_FACTS       = 40
SUMMARIZE_EVERY_N    = 20              # cada 20 turnos genera resumen long-term
DB_PATH              = os.getenv("REZE_DB", "reze_state.db")

# ── Detección de pregunta técnica → route a deepseek-reasoner ──
# Patrones que sugieren razonamiento complejo (código, mate, lógica, debugging).
_TECH_PATTERNS = re.compile(
    r"```|`[^`]+`"
    r"|\bdef\s|\bclass\s|\bfunction\s|\bimport\s|\bconst\s|\blet\s|\bvar\s"
    r"|=>|::|->|<-|&&|\|\|"
    r"|c[óo]mo\s+(funciona|implement|optimiz|configur)"
    r"|\bexplicame\b|\bexplicá|\bporqu[ée]\s|\bpor\s+qu[ée]\s"
    r"|\bc[óo]digo\b|\bdebug|\berror\b|\bexcepci[óo]n|\balgoritm"
    r"|\bcomplejidad\b|\bbig[- ]?o\b|\brecursi[óo]n\b"
    r"|\bmatem[áa]tic|\bf[íi]sic|\bqu[íi]mic|\bcalcul[aá]"
    r"|\becuaci[óo]n|\bderivad|\bintegral|\bteorem"
    r"|\bdemost(rá|ra|rar)|\bprueba\s+que",
    re.IGNORECASE
)

def needs_reasoner(message: str) -> bool:
    """Heurística rápida: ¿esta pregunta amerita el modelo de razonamiento?"""
    if len(message) > 200 and "?" in message:
        return True
    return bool(_TECH_PATTERNS.search(message))


# ── SQLite persistence ────────────────────────────────────────────
@contextmanager
def _db():
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def init_db() -> None:
    with _db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id   TEXT PRIMARY KEY,
                state_json   TEXT NOT NULL,
                history_json TEXT NOT NULL,
                created_at   REAL NOT NULL,
                last_active  REAL NOT NULL
            )
        """)

def save_session(session_id: str, sess: dict) -> None:
    try:
        with _db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO sessions VALUES (?, ?, ?, ?, ?)",
                (
                    session_id,
                    json.dumps(asdict(sess["state"]), ensure_ascii=False),
                    json.dumps(sess["history"], ensure_ascii=False),
                    sess["created_at"],
                    sess["last_active"],
                ),
            )
    except Exception as e:
        print(f"[db] save failed for {session_id}: {e}")

def load_all_sessions() -> dict:
    out = {}
    try:
        with _db() as conn:
            cur = conn.execute("SELECT session_id, state_json, history_json, created_at, last_active FROM sessions")
            for sid, sj, hj, c, la in cur.fetchall():
                try:
                    sd = json.loads(sj)
                    # Filtrar keys que ya no existen en SessionState (migración silenciosa)
                    valid_keys = SessionState().__dict__.keys()
                    sd = {k: v for k, v in sd.items() if k in valid_keys}
                    state = SessionState(**sd)
                    out[sid] = {
                        "history":     json.loads(hj),
                        "state":       state,
                        "created_at":  c,
                        "last_active": la,
                    }
                except Exception as e:
                    print(f"[db] skip session {sid}: {e}")
    except Exception as e:
        print(f"[db] load failed: {e}")
    return out


init_db()
rag.init_rag_db(DB_PATH)
sessions: dict[str, dict] = load_all_sessions()
print(f"[db] cargadas {len(sessions)} sesiones desde {DB_PATH}")
# Back-fill: por cada sesión cargada, asegurar que sus user_facts ya tengan embedding.
# (Idempotente: sync_facts skipea los que ya están.)
for sid, sess in sessions.items():
    n = rag.sync_facts(DB_PATH, sid, sess["state"].user_facts)
    if n:
        print(f"[rag] back-fill: {n} facts nuevos embeddeados para {sid[:8]}…")


def get_session(session_id: str) -> dict:
    now = time.time()
    expired = [sid for sid, s in sessions.items() if now - s["last_active"] > SESSION_TTL]
    for sid in expired:
        del sessions[sid]
        try:
            with _db() as conn:
                conn.execute("DELETE FROM sessions WHERE session_id = ?", (sid,))
        except Exception:
            pass

    if session_id not in sessions:
        sessions[session_id] = {
            "history":     [],
            "state":       SessionState(),
            "created_at":  now,
            "last_active": now,
        }
    else:
        sessions[session_id]["last_active"] = now

    return sessions[session_id]


# ── Memoria larga: resumir turnos viejos cada N turnos ────────────
SUMMARIZE_PROMPT = """Sos un cronista breve. Mirá esta conversación entre el usuario y Reze (una IA tsundere). Devolvé un resumen condensado de máx 4 oraciones que capture:
- Qué temas hablaron (no detalles, esencia)
- Momentos emocionales clave (revelaciones, vulnerabilidades, conflictos)
- Tono general de la relación en ese tramo

NADA de listas ni markdown. Prosa fluida en español rioplatense, tercera persona.

Devolvé JSON estricto:
{"summary": "..."}"""

def maybe_summarize(history: list[dict], state: SessionState) -> None:
    """Cada SUMMARIZE_EVERY_N turnos, condensa los turnos viejos en un párrafo
    que queda guardado en state.long_term_summary. NO toca history."""
    if state.turns == 0 or state.turns % SUMMARIZE_EVERY_N != 0:
        return
    # Tomar los turnos viejos (los que no entran ya en la ventana corta)
    if len(history) < MAX_HISTORY_TURNS * 2:
        return
    old = history[:-MAX_HISTORY_TURNS]
    if not old:
        return
    try:
        prev_summary = state.long_term_summary.strip()
        user_content = json.dumps({
            "resumen_previo": prev_summary or "(sin resumen previo)",
            "turnos_nuevos":  old,
        }, ensure_ascii=False)
        resp = deepseek.chat.completions.create(
            model="deepseek-chat",
            max_tokens=300,
            temperature=0.3,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SUMMARIZE_PROMPT},
                {"role": "user",   "content": user_content},
            ],
        )
        data = json.loads(resp.choices[0].message.content)
        new_summary = str(data.get("summary", "")).strip()
        if new_summary:
            state.long_term_summary = new_summary[:800]
            print(f"[summary] actualizado ({len(new_summary)} chars)")
    except Exception as e:
        print(f"[summary] falló: {e}")


def reflect_user_facts(history: list[dict], state: SessionState,
                       session_id: str | None = None) -> None:
    """Cada N turnos, pedir al LLM que destile hechos nuevos sobre el usuario.
    Si `session_id` viene, los hechos nuevos también se embeddean al RAG."""
    recent = trimmed_history(history)[-16:]
    if not recent:
        return
    try:
        response = deepseek.chat.completions.create(
            model="deepseek-chat",
            max_tokens=200,
            temperature=0.2,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": REFLECT_PROMPT},
                {"role": "user", "content": json.dumps(recent, ensure_ascii=False)},
            ],
        )
        data = json.loads(response.choices[0].message.content)
        new_facts = [str(f).strip()[:80] for f in data.get("facts", []) if str(f).strip()]
        added = []
        for fact in new_facts:
            if fact not in state.user_facts:
                state.user_facts.append(fact)
                added.append(fact)
        if len(state.user_facts) > MAX_USER_FACTS:
            state.user_facts = state.user_facts[-MAX_USER_FACTS:]
        # Embeddear los facts nuevos al RAG
        if session_id and added:
            for f in added:
                rag.add_fact(DB_PATH, session_id, f)
        # Intentar detectar el nombre del usuario en los hechos nuevos
        if not state.user_name:
            name = _extract_name_hint(state.user_facts)
            if name:
                state.user_name = name
    except Exception:
        pass


_NAME_PATTERNS = [
    re.compile(r"se\s+llama\s+([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)"),
    re.compile(r"nombre\s+es\s+([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)"),
    re.compile(r"^([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)\s+es\s+su\s+nombre"),
    re.compile(r"se\s+llama\s+([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)"),
]

def _extract_name_hint(facts: list[str]) -> str:
    for fact in facts:
        for pat in _NAME_PATTERNS:
            m = pat.search(fact)
            if m:
                return m.group(1).strip()
    return ""


class ChatRequest(BaseModel):
    message: str
    session_id: str


FALLBACK = {"emocion": "neutral", "motion": "talk"}


def generate_reply(history: list[dict], state: SessionState, user_message: str,
                   session_id: str | None = None) -> dict:
    """Mete `user_message` en el historial, llama al LLM con estado dinámico,
    aplica deltas y devuelve el dict de respuesta. Lanza HTTPException si falla.

    Si `session_id` viene, hace RAG retrieval: busca los facts más relevantes
    al mensaje del usuario y los inyecta en lugar de los últimos 12 cronológicos."""
    history.append({"role": "user", "content": user_message})

    # RAG: top-K facts semánticamente relevantes al mensaje actual
    relevant = None
    if session_id and state.user_facts:
        relevant = rag.retrieve(DB_PATH, session_id, user_message, top_k=8)

    system_state = {"role": "system", "content": state.as_prompt(relevant_facts=relevant)}

    # Auto-routing de modelo: reasoner para preguntas complejas, chat para conversación
    use_reasoner = needs_reasoner(user_message)
    model_name   = "deepseek-reasoner" if use_reasoner else "deepseek-chat"
    max_tokens   = 600 if use_reasoner else 280

    try:
        response = deepseek.chat.completions.create(
            model=model_name,
            max_tokens=max_tokens,
            temperature=LLM_TEMPERATURE,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": REZE_SYSTEM_PROMPT},
                system_state,
                *trimmed_history(history),
            ],
        )
    except Exception as e:
        history.pop()
        raise HTTPException(status_code=502, detail={"error": "llm_unavailable", "message": str(e)})

    raw = response.choices[0].message.content
    try:
        data         = json.loads(raw)
        respuesta    = str(data.get("respuesta", "")).strip()
        emocion      = str(data.get("emocion",   FALLBACK["emocion"]))
        motion       = str(data.get("motion",    FALLBACK["motion"]))
        flirt        = str(data.get("flirt",     "none"))
        aff_delta    = int(data.get("affinity_delta",   0) or 0)
        irr_delta    = int(data.get("irritation_delta", 0) or 0)
        is_deep      = bool(data.get("is_deep",    False))
        is_protect   = bool(data.get("is_protect", False))
        note         = str(data.get("note", "") or "").strip()[:80]
        if not respuesta:
            raise ValueError("empty respuesta")
    except (json.JSONDecodeError, AttributeError, ValueError, TypeError):
        respuesta = raw.strip() or "..."
        emocion    = FALLBACK["emocion"]
        motion     = FALLBACK["motion"]
        flirt      = "none"
        aff_delta  = irr_delta = 0
        is_deep    = is_protect = False
        note       = ""
        raw        = json.dumps({"respuesta": respuesta, "emocion": emocion, "motion": motion})

    # Clamps al delta (la persona prompt ya dice -10..+10, defendemos el server igual)
    aff_delta = max(-10, min(10, aff_delta))
    irr_delta = max(-15, min(40, irr_delta))

    state.affinity   += aff_delta
    state.irritation += irr_delta

    if flirt == "accept":
        state.flirt_streak += 1
    elif flirt == "explode":
        state.flirt_streak = 0
    elif flirt == "deflect":
        state.flirt_streak = max(0, state.flirt_streak - 1)

    # Deep streak: se acumula mientras siga marcando is_deep
    if is_deep:
        state.deep_streak += 1
    else:
        state.deep_streak = 0

    # Protect count: cap suave para escalar con returns decrecientes
    # (la persona ya modula el delta según protect_count, esto solo cuenta)
    if is_protect:
        state.protect_count += 1

    if flirt != "explode" and irr_delta <= 0:
        state.irritation = max(0, state.irritation - 2)

    if note and note not in state.user_facts:
        state.user_facts.append(note)
        if len(state.user_facts) > MAX_USER_FACTS:
            state.user_facts = state.user_facts[-MAX_USER_FACTS:]
        # Embeddear el fact recién aprendido al RAG
        if session_id:
            rag.add_fact(DB_PATH, session_id, note)

    state.turns += 1
    state.clamp()

    history.append({"role": "assistant", "content": raw})

    if state.turns > 0 and state.turns % REFLECT_EVERY_N_TURNS == 0:
        reflect_user_facts(history, state, session_id=session_id)

    maybe_summarize(history, state)

    return {
        "respuesta": respuesta,
        "emocion":   emocion,
        "motion":    motion,
        "flirt":     flirt,
        "state": {
            "affinity":      state.affinity,
            "irritation":    state.irritation,
            "flirt_streak":  state.flirt_streak,
            "deep_streak":   state.deep_streak,
            "protect_count": state.protect_count,
            "turns":         state.turns,
        },
        "model_used": model_name,
    }


@app.post("/chat")
def chat(req: ChatRequest):
    session = get_session(req.session_id)
    result  = generate_reply(session["history"], session["state"], req.message,
                             session_id=req.session_id)
    save_session(req.session_id, session)
    return JSONResponse(result)


class ProfileRequest(BaseModel):
    session_id: str
    name: str | None = None
    pronouns: str | None = None
    gender: str | None = None
    notes: str | None = None


@app.get("/profile")
def get_profile(session_id: str):
    """Lee el perfil asociado a una sesión. Si no existe, devuelve campos vacíos."""
    session = get_session(session_id)
    state: SessionState = session["state"]
    return JSONResponse({
        "session_id":   session_id,
        "name":         state.user_name,
        "pronouns":     state.user_pronouns,
        "gender":       state.user_gender,
        "notes":        state.user_notes,
        "turns":        state.turns,
        "affinity":     state.affinity,
        "irritation":   state.irritation,
        "flirt_streak": state.flirt_streak,
    })


@app.post("/profile")
def save_profile(req: ProfileRequest):
    """Actualiza campos del perfil. Solo escribe los que vengan no-None — los demás quedan."""
    session = get_session(req.session_id)
    state: SessionState = session["state"]
    if req.name     is not None: state.user_name     = req.name.strip()[:60]
    if req.pronouns is not None: state.user_pronouns = req.pronouns.strip()[:40]
    if req.gender   is not None: state.user_gender   = req.gender.strip()[:60]
    if req.notes    is not None: state.user_notes    = req.notes.strip()[:600]
    save_session(req.session_id, session)
    return JSONResponse({
        "status":   "ok",
        "name":     state.user_name,
        "pronouns": state.user_pronouns,
        "gender":   state.user_gender,
        "notes":    state.user_notes,
    })


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
        sessions[session_id]["state"] = SessionState()
        save_session(session_id, sessions[session_id])
    try:
        with _db() as conn:
            conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
    except Exception:
        pass
    # También limpiar facts embebidos del RAG para esta sesión
    rag.delete_session_facts(DB_PATH, session_id)
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index():
    with open("static/index.html", "r") as f:
        return f.read()


app.mount("/static", StaticFiles(directory="static"), name="static")
