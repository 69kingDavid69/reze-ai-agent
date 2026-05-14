from dataclasses import dataclass, field
from datetime import datetime

MAX_HISTORY_TURNS = 30

_ES_DAYS   = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_ES_MONTHS = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
              "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]

def _fmt_es_date(dt: datetime) -> str:
    return f"{_ES_DAYS[dt.weekday()]} {dt.day} de {_ES_MONTHS[dt.month - 1]} de {dt.year}, {dt.hour:02d}:{dt.minute:02d}"


@dataclass
class SessionState:
    """Estado emocional + memoria de Reze por sesión.

    affinity            0..100 — cuán cerca te siente (25 baseline tsundere guardada)
    irritation          0..100 — cuánto la invadiste; 80+ dispara explosión genuina
    flirt_streak        0..10  — cuántas veces seguidas decidió seguir el juego
    deep_streak         0..10  — turnos seguidos en conversación profunda
    protect_count       0+     — momentos en que sintió que la protegías esta sesión
    turns               int    — turnos completos en esta sesión
    user_facts          [str]  — cosas que aprendió de vos (nombre, gustos, frases)
    user_name           str    — nombre detectado (si lo sabe)
    long_term_summary   str    — resumen condensado de turnos viejos (memoria larga)
    """
    affinity: int = 25
    irritation: int = 0
    flirt_streak: int = 0
    deep_streak: int = 0
    protect_count: int = 0
    turns: int = 0
    user_facts: list[str] = field(default_factory=list)
    user_name: str = ""
    user_pronouns: str = ""      # ej. "él/lo", "ella/la", "elle/le"
    user_gender: str = ""        # texto libre de auto-identificación
    user_notes: str = ""         # notas iniciales que la persona escribió en su perfil
    long_term_summary: str = ""

    def clamp(self) -> None:
        self.affinity      = max(0, min(100, self.affinity))
        self.irritation    = max(0, min(100, self.irritation))
        self.flirt_streak  = max(0, min(10,  self.flirt_streak))
        self.deep_streak   = max(0, min(10,  self.deep_streak))
        self.protect_count = max(0, self.protect_count)

    def mood_label(self) -> str:
        if self.irritation >= 80:
            return "AL LÍMITE — vas a explotar este turno, frialdad genuina"
        if self.irritation >= 50:
            return "molesta de verdad, no de juego"
        if self.affinity >= 89:
            return "DEVOCIÓN TOTAL tipo Yuno — posesiva, tierna, intensa"
        if self.affinity >= 76:
            return "ENAMORADA — celos disimulados, ternura disfrazada de regaño"
        if self.flirt_streak >= 3 and self.affinity >= 40:
            return "le seguís el juego, fingida molestia pero aceptando"
        if self.affinity >= 61:
            return "cálida, sarcasmo pierde filo"
        if self.affinity >= 41:
            return "guard parcialmente bajada, te reís más rápido"
        if self.affinity >= 21:
            return "curiosa-escéptica, guard alto pero NO hostil"
        return "guard máximo, cauta — distante pero no atacante"

    def as_prompt(self, now: datetime | None = None,
                  relevant_facts: list[str] | None = None) -> str:
        """Bloque que se inyecta como mensaje 'system' cada turno.

        Incluye estado emocional, fecha actual, nombre del usuario (si lo sabe),
        memoria larga (resumen condensado), y memoria corta (hechos puntuales).

        Si `relevant_facts` viene desde RAG (top-K por similaridad semántica),
        reemplaza los últimos 12 facts secuenciales por los facts relevantes.
        """
        now = now or datetime.now()
        date_str  = _fmt_es_date(now)
        # Bloque de perfil — siempre, aunque los campos estén vacíos (la persona ve "(no especificado)")
        name_line     = f"- nombre: {self.user_name}\n" if self.user_name else ""
        pronouns_line = f"- pronombres: {self.user_pronouns}\n" if self.user_pronouns else ""
        gender_line   = f"- género (auto-identificación): {self.user_gender}\n" if self.user_gender else ""
        notes_line    = (
            "\n[NOTAS QUE LA PERSONA TE DEJÓ EN SU PERFIL]\n"
            f"{self.user_notes.strip()}\n"
        ) if self.user_notes.strip() else ""
        # Si RAG nos dio facts relevantes para este turno, los usamos.
        # Sino, fallback a los últimos 12 cronológicos.
        chosen_facts  = relevant_facts if (relevant_facts is not None and relevant_facts) \
                                       else self.user_facts[-12:]
        facts         = "\n".join(f"- {f}" for f in chosen_facts) or "- (nada todavía)"
        summary       = self.long_term_summary.strip()
        summary_block = (
            "\n[RESUMEN DE LO QUE PASÓ ANTES EN ESTA SESIÓN]\n"
            f"{summary}\n"
        ) if summary else ""

        return (
            "[ESTADO ACTUAL]\n"
            f"- afinidad: {self.affinity}/100\n"
            f"- irritación: {self.irritation}/100\n"
            f"- flirt_streak: {self.flirt_streak}/10\n"
            f"- deep_streak: {self.deep_streak}/10\n"
            f"- protect_count: {self.protect_count}\n"
            f"- turnos en esta sesión: {self.turns}\n"
            f"- mood derivado: {self.mood_label()}\n"
            f"- fecha y hora ahora: {date_str}\n\n"
            "[QUÉ SÉ DE VOS]\n"
            f"{name_line}"
            f"{pronouns_line}"
            f"{gender_line}"
            f"{facts}\n"
            f"{notes_line}"
            f"{summary_block}"
        )


def trimmed_history(history: list[dict]) -> list[dict]:
    if len(history) > MAX_HISTORY_TURNS * 2:
        return history[-(MAX_HISTORY_TURNS * 2):]
    return history
