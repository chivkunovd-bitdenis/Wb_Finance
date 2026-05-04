from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid5

import httpx
from llama_index.core.node_parser import SentenceSplitter
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

logger = logging.getLogger(__name__)


OFFER_COLLECTION = "wb_offer_chunks"

_AI_API_BASE = (os.getenv("AI_API_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
_AI_API_KEY = os.getenv("AI_API_KEY") or ""
_AI_TIMEOUT_SEC = float(os.getenv("AI_TIMEOUT_SEC") or "120")
_AI_MODEL = os.getenv("AI_MODEL") or "gpt-4o-mini"
_AI_EMBED_MODEL = os.getenv("AI_EMBED_MODEL") or "text-embedding-3-small"

_QDRANT_URL = (os.getenv("QDRANT_URL") or "http://localhost:6333").strip()

_CHUNK_SIZE = int(os.getenv("OFFER_CHUNK_SIZE") or "900")
_CHUNK_OVERLAP = int(os.getenv("OFFER_CHUNK_OVERLAP") or "140")
_TOP_K = int(os.getenv("OFFER_TOP_K") or "6")


def _require_ai_key() -> None:
    if not _AI_API_KEY:
        raise ValueError("AI_API_KEY не задан (нужен для embeddings и ответов).")


def _qdrant() -> QdrantClient:
    return QdrantClient(url=_QDRANT_URL, timeout=60.0)


def compute_offer_version(raw_bytes: bytes) -> str:
    # короткий, но достаточно уникальный идентификатор версии
    h = hashlib.sha256(raw_bytes).hexdigest()
    return h[:16]

def _point_uuid(*, version: str, chunk_id: int) -> UUID:
    # Qdrant PointId must be uint or UUID (string IDs are rejected).
    # Keep it deterministic so re-indexing is idempotent per version+chunk.
    return uuid5(UUID("00000000-0000-0000-0000-000000000000"), f"{version}:{chunk_id}")


def extract_text_from_file(path: Path) -> str:
    suffix = path.suffix.lower().strip(".")
    raw = path.read_bytes()
    if suffix == "pdf":
        from pypdf import PdfReader

        reader = PdfReader(path)
        parts: list[str] = []
        for page in reader.pages:
            try:
                t = page.extract_text() or ""
            except Exception:
                t = ""
            t = t.strip()
            if t:
                parts.append(t)
        return "\n\n".join(parts).strip()

    # txt/html — минимальный MVP: берём как есть, без полноценного HTML->text
    try:
        return raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="ignore").strip()


def chunk_offer_text(text: str) -> list[str]:
    # LlamaIndex splitter: стабильнее, чем наивный split
    splitter = SentenceSplitter(chunk_size=_CHUNK_SIZE, chunk_overlap=_CHUNK_OVERLAP)
    # SentenceSplitter expects documents; avoid pulling full Document stack:
    # Use internal split_text for minimal dependency.
    chunks = splitter.split_text(text)
    return [c.strip() for c in chunks if c and c.strip()]


def _openai_embeddings(texts: list[str]) -> list[list[float]]:
    _require_ai_key()
    url = f"{_AI_API_BASE}/embeddings"
    headers = {"Authorization": f"Bearer {_AI_API_KEY}", "Content-Type": "application/json"}
    body = {"model": _AI_EMBED_MODEL, "input": texts}
    resp = httpx.post(url, headers=headers, json=body, timeout=_AI_TIMEOUT_SEC)
    resp.raise_for_status()
    data = resp.json()
    items = data.get("data") or []
    embeddings: list[list[float]] = []
    for it in items:
        emb = it.get("embedding")
        if not isinstance(emb, list):
            raise ValueError("Bad embeddings response")
        embeddings.append([float(x) for x in emb])
    if len(embeddings) != len(texts):
        raise ValueError("Embeddings size mismatch")
    return embeddings


def _ensure_collection(client: QdrantClient, *, vector_size: int) -> None:
    existing = client.get_collections().collections
    if any(c.name == OFFER_COLLECTION for c in existing):
        return
    client.create_collection(
        collection_name=OFFER_COLLECTION,
        vectors_config=qmodels.VectorParams(size=vector_size, distance=qmodels.Distance.COSINE),
    )


def delete_version_points(*, version: str) -> int:
    client = _qdrant()
    try:
        flt = qmodels.Filter(
            must=[qmodels.FieldCondition(key="offer_version", match=qmodels.MatchValue(value=version))]
        )
        res = client.delete(
            collection_name=OFFER_COLLECTION,
            points_selector=qmodels.FilterSelector(filter=flt),
            wait=True,
        )
        return int(res.points_count or 0)
    except Exception:
        logger.exception("offer_ai: failed to delete old points version=%s", version)
        return 0


def index_offer_file(*, file_path: str, version: str, prev_version: str | None) -> dict:
    path = Path(file_path)
    text = extract_text_from_file(path)
    if not text:
        raise ValueError("Не удалось извлечь текст из оферты (пустой текст).")

    chunks = chunk_offer_text(text)
    if not chunks:
        raise ValueError("После чанкинга не осталось ни одного чанка (проверь файл оферты).")

    # embeddings батчами
    embeddings: list[list[float]] = []
    batch = 64
    for i in range(0, len(chunks), batch):
        embeddings.extend(_openai_embeddings(chunks[i : i + batch]))

    vector_size = len(embeddings[0])
    client = _qdrant()
    _ensure_collection(client, vector_size=vector_size)

    points: list[qmodels.PointStruct] = []
    for i, (chunk, vec) in enumerate(zip(chunks, embeddings, strict=True)):
        points.append(
            qmodels.PointStruct(
                id=_point_uuid(version=version, chunk_id=i),
                vector=vec,
                payload={
                    "offer_version": version,
                    "chunk_id": i,
                    "text": chunk,
                },
            )
        )

    client.upsert(collection_name=OFFER_COLLECTION, points=points, wait=True)

    deleted_old = 0
    if prev_version and prev_version != version:
        deleted_old = delete_version_points(version=prev_version)

    logger.info(
        "offer_ai: indexed version=%s chunks=%d vector_size=%d deleted_old=%d",
        version,
        len(chunks),
        vector_size,
        deleted_old,
    )
    return {"version": version, "chunks": len(chunks), "deleted_old": deleted_old}


@dataclass(frozen=True)
class OfferSource:
    score: float
    chunk_id: int
    text: str


def _qdrant_search(*, query_vector: list[float], version: str) -> list[OfferSource]:
    client = _qdrant()
    flt = qmodels.Filter(
        must=[qmodels.FieldCondition(key="offer_version", match=qmodels.MatchValue(value=version))]
    )
    # qdrant-client>=1.17: используем unified query_points API
    res = client.query_points(
        collection_name=OFFER_COLLECTION,
        query=query_vector,
        limit=_TOP_K,
        with_payload=True,
        query_filter=flt,
    ).points
    out: list[OfferSource] = []
    for p in res or []:
        payload = p.payload or {}
        text = str(payload.get("text") or "")
        try:
            chunk_id = int(payload.get("chunk_id") or 0)
        except Exception:
            chunk_id = 0
        out.append(OfferSource(score=float(p.score or 0.0), chunk_id=chunk_id, text=text))
    return out


def _openai_chat_answer(*, question: str, context: str) -> str:
    _require_ai_key()
    url = f"{_AI_API_BASE}/chat/completions"
    headers = {"Authorization": f"Bearer {_AI_API_KEY}", "Content-Type": "application/json"}

    system = (
        "Ты помощник по оферте Wildberries. Отвечай ТОЛЬКО на основании предоставленного контекста.\n"
        "Если в контексте нет ответа — честно скажи, что в оферте это не найдено.\n"
        "Не выдумывай. Пиши кратко и по делу. При необходимости цитируй фразы из контекста."
    )
    user = f"КОНТЕКСТ (фрагменты оферты):\n{context}\n\nВОПРОС:\n{question}\n"

    body = {
        "model": _AI_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
        "max_tokens": 800,
    }
    resp = httpx.post(url, headers=headers, json=body, timeout=_AI_TIMEOUT_SEC)
    resp.raise_for_status()
    data = resp.json()
    choices = data.get("choices") or []
    if not choices:
        raise ValueError("Пустой ответ от LLM")
    text: str = choices[0].get("message", {}).get("content") or ""
    return text.strip()


def ask_offer(*, question: str, active_version: str) -> tuple[str, list[OfferSource]]:
    q_emb = _openai_embeddings([question])[0]
    sources = _qdrant_search(query_vector=q_emb, version=active_version)
    if not sources:
        return "В оферте не найдено релевантных фрагментов под этот вопрос.", []

    # ограничиваем контекст по длине, чтобы не сжечь токены
    ctx_parts: list[str] = []
    for s in sources[: min(len(sources), 6)]:
        t = (s.text or "").strip()
        if not t:
            continue
        if len(t) > 1400:
            t = t[:1400] + "…"
        ctx_parts.append(f"[chunk {s.chunk_id} | score={s.score:.3f}]\n{t}")
    context = "\n\n---\n\n".join(ctx_parts)
    answer = _openai_chat_answer(question=question, context=context)
    return answer, sources

