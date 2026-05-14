"""RAG local: embeddings + retrieval semántico sobre user_facts.

Diseño:
- Modelo: fastembed (ONNX runtime, ~50MB) en lugar de sentence-transformers/torch (~700MB).
  Esto deja la imagen total bajo 1GB y entra en Fly.io free tier (512MB RAM).
- Default: `sentence-transformers/all-MiniLM-L6-v2` (90MB, entrenado en inglés
  pero funciona razonablemente para frases cortas en español por BPE shared vocab).
  Para mejor calidad multilingüe, setear RAG_MODEL en env (requiere VM con más RAM).
- Storage: tabla SQLite `fact_memory` con vector blob (float32 packed).
  Cosine similarity calculada en Python — suficiente para < 10k facts por sesión.
"""
from __future__ import annotations
import math
import os
import sqlite3
import struct
import time


# ── Modelo (lazy-load: la primera llamada tarda 2-4s, después es rápido) ──
_model = None

def get_model():
    global _model
    if _model is not None:
        return _model
    from fastembed import TextEmbedding
    model_name = os.getenv("RAG_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
    _model = TextEmbedding(model_name=model_name)
    print(f"[rag] modelo cargado: {model_name}")
    return _model


def embed(text: str) -> list[float]:
    """Devuelve embedding como lista de floats. [] si text vacío."""
    text = (text or "").strip()
    if not text:
        return []
    try:
        model = get_model()
        return list(model.embed([text]))[0].tolist()
    except Exception as e:
        print(f"[rag] embed falló: {e}")
        return []


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na  = math.sqrt(sum(x * x for x in a))
    nb  = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# ── Storage (BLOB de float32 packed) ──────────────────────────────────────
def _pack(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)

def _unpack(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def init_rag_db(db_path: str) -> None:
    """Crear tabla y índice (idempotente). Llamar al startup."""
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS fact_memory (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                fact_text  TEXT NOT NULL,
                embedding  BLOB NOT NULL,
                created_at REAL NOT NULL,
                UNIQUE(session_id, fact_text)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_fact_session ON fact_memory(session_id)"
        )


def add_fact(db_path: str, session_id: str, fact_text: str) -> bool:
    """Embeber + persistir un fact. Idempotente (UNIQUE)."""
    fact_text = (fact_text or "").strip()
    if not fact_text:
        return False
    vec = embed(fact_text)
    if not vec:
        return False
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO fact_memory (session_id, fact_text, embedding, created_at) VALUES (?,?,?,?)",
                (session_id, fact_text, _pack(vec), time.time()),
            )
        return True
    except Exception as e:
        print(f"[rag] add_fact falló: {e}")
        return False


def sync_facts(db_path: str, session_id: str, facts: list[str]) -> int:
    """Embeber solo los facts que NO están ya almacenados para esta sesión.

    Devuelve cuántos facts nuevos se embebieron. Útil para back-fill cuando
    arranca el server con un state que tiene user_facts pero el RAG aún no
    los procesó (ej. migración de un state pre-RAG)."""
    if not facts:
        return 0
    try:
        with sqlite3.connect(db_path) as conn:
            cur = conn.execute(
                "SELECT fact_text FROM fact_memory WHERE session_id = ?",
                (session_id,),
            )
            existing = {row[0] for row in cur}
        missing = [f.strip() for f in facts if f.strip() and f.strip() not in existing]
        for f in missing:
            add_fact(db_path, session_id, f)
        return len(missing)
    except Exception as e:
        print(f"[rag] sync_facts falló: {e}")
        return 0


def retrieve(db_path: str, session_id: str, query: str, top_k: int = 8,
             min_similarity: float = 0.15) -> list[str]:
    """Top-K facts más similares al query (en orden de relevancia).

    `min_similarity` filtra resultados ruidosos cuando el query no se parece
    a nada — devuelve [] si nada supera el threshold."""
    qvec = embed(query)
    if not qvec:
        return []
    try:
        with sqlite3.connect(db_path) as conn:
            cur = conn.execute(
                "SELECT fact_text, embedding FROM fact_memory WHERE session_id = ?",
                (session_id,),
            )
            scored = []
            for fact_text, blob in cur:
                vec = _unpack(blob)
                sim = cosine(qvec, vec)
                if sim >= min_similarity:
                    scored.append((sim, fact_text))
            scored.sort(reverse=True, key=lambda t: t[0])
            return [t for _, t in scored[:top_k]]
    except Exception as e:
        print(f"[rag] retrieve falló: {e}")
        return []


def delete_session_facts(db_path: str, session_id: str) -> None:
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute("DELETE FROM fact_memory WHERE session_id = ?", (session_id,))
    except Exception as e:
        print(f"[rag] delete falló: {e}")


def stats(db_path: str, session_id: str | None = None) -> dict:
    """Para debug: cuántos facts hay por sesión."""
    try:
        with sqlite3.connect(db_path) as conn:
            if session_id:
                cur = conn.execute(
                    "SELECT COUNT(*) FROM fact_memory WHERE session_id = ?",
                    (session_id,),
                )
                return {"session_id": session_id, "facts": cur.fetchone()[0]}
            else:
                cur = conn.execute(
                    "SELECT session_id, COUNT(*) FROM fact_memory GROUP BY session_id"
                )
                return {sid: count for sid, count in cur.fetchall()}
    except Exception as e:
        return {"error": str(e)}
