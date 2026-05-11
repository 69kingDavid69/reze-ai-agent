# Arquitectura — Anime Voice Agent (Reze)

> Asistente conversacional con personalidad tsundere (fusión Asuka + Yuno) que vive en una página web. El usuario habla por micrófono o tipea, Reze responde con voz y un modelo Live2D animado en pantalla. Stack 100% gratis.

---

## 1. Flujo funcional del MVP

1. El usuario abre la página servida por FastAPI (`/`) en Chrome.
2. La página carga el canvas de PixiJS y monta el modelo Live2D de Asuka (renombrado a "Reze"). El personaje arranca en estado `idle` con animación de respiración y parpadeo automático.
3. El usuario habla al micrófono (botón 🎤) o escribe en el input de texto.
4. Si habló: Web Speech API (`SpeechRecognition`, `es-AR`) transcribe el audio en el navegador.
5. El texto se envía a `POST /chat` con `{ message, session_id }`.
6. El backend FastAPI recupera el historial de la sesión, lo trimea, antepone el system prompt de Reze y llama a DeepSeek con `response_format: json_object`.
7. DeepSeek devuelve un JSON estructurado: `{ respuesta, emocion, motion }`.
8. El frontend recibe el JSON. Inmediatamente:
   - Pinta la burbuja con `respuesta` en el chat.
   - Dispara el `motion` correspondiente en el modelo Live2D.
   - Aplica la `expresion` asociada a la `emocion` (sonrisa, enojo, flustered, etc.).
   - Llama a `speechSynthesis.speak()` con la respuesta en español.
9. Mientras suena el TTS del browser, un `AudioContext + AnalyserNode` mide la amplitud y mapea a `ParamMouthOpenY` de Live2D para lip-sync.
10. Cuando termina de hablar, el personaje vuelve a `idle` y el loop queda listo para el siguiente turno.
11. El historial vive en memoria del servidor con TTL de 1h. Botón "Limpiar chat" llama a `DELETE /history`.

---

## 2. Diagrama de arquitectura

```
┌─────────────────────── Browser (Chrome) ────────────────────────┐
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │  PixiJS canvas + pixi-live2d-display                    │    │
│  │  ┌────────────────────────────────────────────────┐     │    │
│  │  │  Modelo Live2D "Reze" (Asuka fan-made)         │     │    │
│  │  │  - motions: idle, talk, wave, dance, surprise  │     │    │
│  │  │  - expresiones: smile, angry, flustered, …     │     │    │
│  │  │  - lip-sync: ParamMouthOpenY ← amplitud audio  │     │    │
│  │  └────────────────────────────────────────────────┘     │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                 │
│  ┌─────────────┐   ┌──────────────────┐   ┌─────────────────┐   │
│  │ Mic UI      │   │  Chat UI         │   │ Animation       │   │
│  │ Web Speech  │──▶│  (bubbles +      │◀──│ controller      │   │
│  │ STT (es-AR) │   │   status)        │   │ emocion→motion  │   │
│  └─────────────┘   └─────────┬────────┘   └────────┬────────┘   │
│                              │ texto              ▲             │
│                              ▼                    │             │
│                      ┌──────────────────────────────────────┐   │
│                      │  speechSynthesis (TTS browser, es)   │   │
│                      │  + AnalyserNode → amplitud → boca    │   │
│                      └──────────────────────────────────────┘   │
└──────────────────────────────┬──────────────────────────────────┘
                               │ POST /chat (JSON)
                               ▼
┌────────────────────── FastAPI (server.py) ──────────────────────┐
│                                                                 │
│  /chat        → arma historial + prompt → DeepSeek → JSON       │
│  /history     → limpia sesión                                   │
│  /            → sirve static/index.html                         │
│  /static/*    → modelo Live2D, JS, CSS                          │
│                                                                 │
│  sessions: dict[str, {history, last_active}]  (TTL 1h)          │
│  memory.trimmed_history()  → sliding window 20 turnos           │
└──────────────────────────────┬──────────────────────────────────┘
                               │ chat completions (OpenAI SDK)
                               ▼
                  ┌─────────────────────────────┐
                  │  DeepSeek API               │
                  │  model: deepseek-chat       │
                  │  response_format: json      │
                  └─────────────────────────────┘
```

---

## 3. Reutilización de código existente

| Componente actual | Destino | Acción |
|---|---|---|
| `persona.py` — `REZE_SYSTEM_PROMPT` | Núcleo de personalidad | **Actualizar**: agregar instrucción de JSON output con `respuesta`, `emocion`, `motion`. Mantener todo el carácter. |
| `persona.py` — `VOICE_ID`, `VOICE_SETTINGS` | Config de ElevenLabs | **Retirar del flujo principal**: queda como código muerto o se mueve a un módulo opcional si se quiere TTS premium en el futuro. |
| `server.py` — `/chat` endpoint | Backend principal | **Modificar**: ya no streamea audio MP3. Devuelve JSON `{ respuesta, emocion, motion }`. Eliminar dependencia de ElevenLabs del path crítico. |
| `server.py` — sesiones con TTL | Memoria conversacional | **Mantener tal cual**: ya funciona. |
| `memory.py` — `trimmed_history` | Sliding window de contexto | **Mantener tal cual**. |
| `agent.py` — CLI loop voice-only | Versión terminal vieja | **Conservar como utilidad** standalone. No participa del flujo web. |
| `tts_test.py` | Probar voces ElevenLabs | **Conservar** como herramienta de testing si en el futuro se reactiva ElevenLabs. |
| `static/index.html` — chat UI + mic + audio playback | Frontend web | **Evolucionar**: agregar canvas PixiJS, cargar modelo Live2D, reemplazar `new Audio(blob)` por `speechSynthesis`, conectar amplitud a la boca del modelo, mapear emoción → motion. |

---

## 4. Modelo de datos

Sin persistencia. El estado conversacional vive en memoria del proceso FastAPI:

```python
# server.py — sessions in-memory
sessions: dict[str, {
    "history": list[dict],   # [{role: "user"|"assistant", content: str}, ...]
    "created_at": float,
    "last_active": float,
}]
# TTL: 3600s. Limpieza lazy en cada request.
```

**Próxima iteración (si se quiere memoria persistente entre reinicios):**

```sql
CREATE TABLE sessions (
    id          TEXT PRIMARY KEY,
    created_at  TIMESTAMP DEFAULT NOW(),
    last_active TIMESTAMP DEFAULT NOW()
);

CREATE TABLE messages (
    id          SERIAL PRIMARY KEY,
    session_id  TEXT REFERENCES sessions(id),
    role        TEXT NOT NULL,   -- 'user' | 'assistant'
    content     TEXT NOT NULL,
    emocion     TEXT,            -- guardado para analytics opcional
    created_at  TIMESTAMP DEFAULT NOW()
);
```

---

## 5. Contrato de respuesta del LLM

DeepSeek se llama con `response_format: { type: "json_object" }`. El system prompt instruye devolver:

```json
{
  "respuesta": "Anta baka?! Claro que sé la respuesta, ya deja de molestar.",
  "emocion": "arrogante",
  "motion": "talk_confident"
}
```

**Campos:**

| Campo | Tipo | Valores | Uso en frontend |
|---|---|---|---|
| `respuesta` | string | máx 2 oraciones, sin markdown | Se renderiza en burbuja + se pasa a `speechSynthesis` |
| `emocion` | enum | `neutral`, `arrogante`, `enojo`, `flustered`, `posesivo`, `sorpresa`, `dulce` | Selecciona el `.exp3.json` (expresión facial) |
| `motion` | enum | `idle`, `talk`, `talk_confident`, `wave`, `dance`, `surprise`, `angry_pose` | Selecciona el `.motion3.json` (animación de cuerpo) |

**Fallback:** si DeepSeek devuelve JSON inválido o falta algún campo, el backend rellena con `{ emocion: "neutral", motion: "talk" }` antes de mandar al cliente.

---

## 6. Stack tecnológico

| Capa | Tecnología | Razón |
|---|---|---|
| Backend HTTP | FastAPI (Python 3.11+) | (decidido) — ya implementado, maneja sesiones y proxy al LLM |
| LLM | DeepSeek `deepseek-chat` vía OpenAI SDK | (decidido) — el usuario ya tiene API key; bajo costo |
| Output del LLM | JSON estructurado (`response_format: json_object`) | (decidido) — separa texto, emoción y motion sin parseo frágil |
| STT | Web Speech API (`SpeechRecognition`, `es-AR`) | (decidido) — nativo del browser, gratis, ya integrado en `static/index.html` |
| TTS | Web Speech API (`speechSynthesis`) | (decidido en este turno) — gratis, voces en español del SO. Reemplaza a ElevenLabs. |
| ~~TTS premium~~ | ~~ElevenLabs `eleven_flash_v2_5`~~ | ~~(decidido)~~ → **retirado por costo; queda como opción futura opt-in** |
| Renderer 3D/2.5D | PixiJS + `pixi-live2d-display` + Cubism SDK Web | (decidido) — wrapper open source; SDK libre para uso personal |
| Modelo de personaje | Live2D fan-made de Asuka (Booth / itch.io / GitHub) | (decidido) — renombrado a "Reze" en la UI; licencia "free personal use" |
| Lip-sync | `AudioContext` + `AnalyserNode` → amplitud → `ParamMouthOpenY` | (decidido) — aproximado pero funcional sin fonemas |
| Animaciones | Motions `.motion3.json` + expresiones `.exp3.json` mapeadas a `emocion`/`motion` del LLM | (decidido) |
| Frontend serving | FastAPI sirviendo `static/` directamente | (decidido) — no se migra a Next.js; el setup actual ya alcanza |
| Persistencia | Ninguna (in-memory con TTL 1h) | (decidido) — suficiente para MVP |
| Hosting | Por definir | (sugerido) — Render free tier o Railway para Python; Vercel no sirve bien para FastAPI con sesiones en memoria |
| Config | `python-dotenv` (`.env` con `DEEPSEEK_API_KEY`) | (decidido) |
| Browser target | Chrome (Web Speech API estable ahí) | (decidido) — limitación conocida |

---

## 7. Plan de construcción por fases

### Fase 0 — Estado actual (✓ completado)
- FastAPI con `/chat`, sesiones con TTL, persona Reze, DeepSeek integrado.
- Frontend web con chat UI, mic (Web Speech STT), playback de audio ElevenLabs.
- Conversación funcional voz↔voz sin avatar visual.

### Fase 1 — Pivote del backend a JSON estructurado (~medio día)
- Modificar `REZE_SYSTEM_PROMPT` para que devuelva `{ respuesta, emocion, motion }`.
- Cambiar `server.py`: `/chat` deja de llamar a ElevenLabs y devuelve `JSONResponse` con los 3 campos.
- Agregar validación + fallback de JSON inválido en el backend.
- Quitar `X-Reze-Reply` header del flujo (ya no hace falta, va en el body).
- Mantener `agent.py` CLI funcionando aparte (no se toca).
- **Entregable:** `/chat` devuelve JSON; el frontend viejo se rompe temporalmente.

### Fase 2 — Reemplazo de TTS por Web Speech API (~½ día)
- En `static/index.html`: reemplazar el bloque `new Audio(blob).play()` por `speechSynthesis.speak(new SpeechSynthesisUtterance(reply))`.
- Configurar `utterance.lang = 'es-AR'` y elegir una voz femenina disponible (`speechSynthesis.getVoices()`).
- Manejar `onstart` / `onend` para el estado `isBusy` y el indicador de "Reze está hablando".
- **Entregable:** chat funcional, ahora gratis sin ElevenLabs.

### Fase 3 — Integración de Live2D (~1-2 días)
- Conseguir modelo Live2D de Asuka (búsqueda en Booth/itch.io/GitHub con licencia free personal). Validar que trae motions y expresiones, o al menos los parámetros estándar de Cubism.
- Servirlo como assets estáticos en `/static/live2d/reze/`.
- Cargar Cubism Core (`live2dcubismcore.min.js`) y `pixi-live2d-display` en el HTML.
- Montar canvas PixiJS sobre el fondo, detrás del chat (o al costado en desktop).
- Mostrar el modelo en idle con breathing/parpadeo activos.
- **Entregable:** Reze visible en pantalla, respirando, sin interactividad aún.

### Fase 4 — Lip-sync y motions reactivos (~1-2 días)
- Conectar `speechSynthesis` con un `AudioContext` (truco: capturar el output via `MediaStream` o, más simple, animar boca con timer sincrónico al `onboundary` event que dispara por palabra).
- Mapear amplitud → `ParamMouthOpenY` con suavizado.
- Crear `animationController.js` que reciba `{ emocion, motion }` y dispare la motion y expresión correspondientes.
- Definir el mapping concreto emoción↔expresión y motion↔motion file según lo que traiga el modelo conseguido.
- **Entregable:** Reze mueve la boca y cambia de pose según lo que dice.

### Fase 5 — Pulido y estados visuales (~½ día)
- Estados visuales claros: `idle` / `listening` (leve inclinación) / `thinking` (mira al costado) / `speaking` / `reaction`.
- Transiciones suaves entre motions (crossfade del SDK).
- Botón opcional "que baile" → dispara motion `dance` independiente del LLM.
- Manejo de errores con motion de "tch" / sorpresa cuando falla `/chat`.

### Fase 6 — (opcional) Deploy y portabilidad
- Elegir hosting (Render free / Railway).
- Variables de entorno en el panel del hosting.
- Documentar el setup en un README mínimo.

---

## 8. MCPs y Skills útiles

### MCPs disponibles en el entorno

| MCP | Para qué sirve en este proyecto |
|---|---|
| `Claude_Preview` / `Claude_in_Chrome` | Probar el frontend en un browser controlado, leer console logs y network requests sin salir del agente. Útil para debuggear el rendering de Live2D y el lip-sync. |

### Skills útiles

| Skill | Aporte |
|---|---|
| `generate-arch` | Mantener este `ARCHITECTURE.md` sincronizado cuando se cierren decisiones abiertas (sección 9). |
| `claude-api` | Si en el futuro se migra el LLM de DeepSeek a Claude (caching + structured output más maduros). |
| `text-to-speech` (ElevenLabs) | Solo si se reactiva el TTS premium opcional; no se usa en el flujo gratuito. |

---

## 9. Riesgos y decisiones abiertas

1. **Conseguir un modelo Live2D decente de Asuka — cuello de botella principal.** Hay modelos fan-made gratis en Booth/itch.io/GitHub, pero la calidad varía mucho y no todos traen motions de cuerpo (algunos son solo head-rig). Mitigación: validar antes de empezar Fase 3 que el modelo elegido incluya al menos `idle`, `talk` y una motion expresiva. Si no, considerar (a) usar uno más simple y agregar motions custom con Cubism Editor FREE, (b) reemplazar por otra waifu fan-made con más assets.

2. **Licencia del modelo fan-made: respetar "personal use only".** No monetizar, no redistribuir el modelo, no usarlo en builds públicas comerciales. Guardar la licencia original junto a los assets en `/static/live2d/reze/LICENSE.txt`.

3. **IP de Asuka (Evangelion).** Uso personal no comercial está fuera de zona de riesgo práctico. Si en algún momento el proyecto se publica con audiencia real, hay que reemplazar por un personaje original o licenciado.

4. **Web Speech API funciona bien solo en Chrome.** Firefox y Safari tienen soporte parcial o requieren configuración. Mitigación: detectar disponibilidad y mostrar mensaje claro; opcionalmente proveer fallback a input solo de texto.

5. **Lip-sync por amplitud es aproximado.** `speechSynthesis` no expone fácilmente el waveform en todos los browsers. Plan A: animar boca con eventos `onboundary` (granularidad por palabra). Plan B: enrutarlo por `MediaStreamAudioDestinationNode` (más complejo, no siempre funciona). Plan C: animar la boca con un ritmo aleatorio mientras `speaking=true` (cheat aceptable). Decidir en Fase 4 según se vea.

6. **Decisión abierta — hosting.** Vercel free tier no juega bien con FastAPI ni con sesiones en memoria (cold starts + serverless lambda statelessness). Opciones: Render free (750h/mes, sleep tras inactividad), Railway ($5 crédito/mes), Fly.io (free allowance). Hasta no deployar, correr local con `uvicorn server:app --reload`.

7. **Decisión abierta — voz del navegador en español.** `speechSynthesis.getVoices()` devuelve distintas voces según SO/browser. En macOS suele haber "Mónica" o "Paulina" decentes; en Windows "Sabina". No hay garantía de calidad uniforme. Mitigación: filtrar voces por `lang.startsWith('es')` y dejar la primera que aparezca, con override por config.

8. **Decisión abierta — persistencia de memoria.** Actualmente la conversación se pierde al reiniciar el servidor o al expirar el TTL. Si se quiere recordar entre sesiones del usuario, agregar SQLite local o Supabase free. No urgente para MVP.

9. **Riesgo — context window de DeepSeek.** Ya mitigado parcialmente con `trimmed_history` (20 turnos). Monitorear si en charlas largas el modelo empieza a perder personalidad o repetirse.

10. **Decisión abierta — idioma del STT/TTS.** Hoy hardcoded a `es-AR`. Si el usuario quiere mezclar inglés/japonés, hay que evaluar `lang` dinámico o detección automática. No bloquea el MVP.

11. **~~Decisión abierta — STT offline vs cloud~~ → resuelto: Web Speech API del browser (cloud-side de Google, pero gratis y sin API key).** Si la privacidad importa más adelante, evaluar Whisper local.

12. **~~Decisión abierta — TTS en backend con ElevenLabs vs algo gratis~~ → resuelto: `speechSynthesis` del browser. ElevenLabs queda como opción premium opt-in si en el futuro se quiere mejor voz.**
