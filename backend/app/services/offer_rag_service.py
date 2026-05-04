from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid5

from llama_index.core import PromptTemplate, Settings, get_response_synthesizer
from llama_index.core.query_engine import RetrieverQueryEngine
from llama_index.core.retrievers import BaseRetriever
from llama_index.core.node_parser import SentenceSplitter
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.llms.openai import OpenAI
from llama_index.core.schema import NodeWithScore, TextNode
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

_CHUNK_SIZE = int(os.getenv("OFFER_CHUNK_SIZE") or "1000")
_CHUNK_OVERLAP = int(os.getenv("OFFER_CHUNK_OVERLAP") or "150")
_TOP_K = int(os.getenv("OFFER_TOP_K") or "5")
_TEMPERATURE = float(os.getenv("OFFER_TEMPERATURE") or "0.3")


def _require_ai_key() -> None:
    if not _AI_API_KEY:
        raise ValueError("AI_API_KEY не задан (нужен для embeddings и ответов).")


def _qdrant() -> QdrantClient:
    return QdrantClient(url=_QDRANT_URL, timeout=60.0)

def _llama_settings() -> None:
    """
    Configure LlamaIndex global settings.
    We use our existing env vars (AI_API_KEY / AI_API_BASE_URL) instead of OPENAI_API_KEY.
    """
    _require_ai_key()
    Settings.embed_model = OpenAIEmbedding(
        model=_AI_EMBED_MODEL,
        api_key=_AI_API_KEY,
        api_base=_AI_API_BASE,
        timeout=_AI_TIMEOUT_SEC,
    )
    Settings.llm = OpenAI(
        model=_AI_MODEL,
        temperature=_TEMPERATURE,
        api_key=_AI_API_KEY,
        api_base=_AI_API_BASE,
        timeout=_AI_TIMEOUT_SEC,
        max_tokens=900,
    )


def _offer_prompt_template() -> PromptTemplate:
    template = """Ты — ассистент по оферте Wildberries.

Отвечай ТОЛЬКО на основании контекста из оферты. Нельзя выдумывать.
Если в контексте нет ответа — прямо скажи, что ответа в контексте нет, и не додумывай.

Требования к ответу:
- объясняй простыми словами;
- не начинай с цитаты;
- дай пример;
- объясни практический риск для селлера;
- в конце кратко укажи основание в оферте (по смыслу, можно сослаться на фрагменты).

Формат ответа (строго):
Коротко:
Простыми словами:
Пример:
Что важно для селлера:
Основание в оферте:

Контекст из оферты:
{context_str}

Вопрос:
{query_str}
"""
    return PromptTemplate(template)


def _offer_query_engine(*, version: str) -> RetrieverQueryEngine:
    _llama_settings()
    retriever = _QdrantOfferRetriever(
        qdrant=_qdrant(),
        offer_version=version,
        similarity_top_k=_TOP_K,
    )
    synthesizer = get_response_synthesizer(
        response_mode="compact",
        text_qa_template=_offer_prompt_template(),
    )
    return RetrieverQueryEngine(retriever=retriever, response_synthesizer=synthesizer)


class _QdrantOfferRetriever(BaseRetriever):
    def __init__(self, *, qdrant: QdrantClient, offer_version: str, similarity_top_k: int) -> None:
        super().__init__()
        self._qdrant = qdrant
        self._offer_version = offer_version
        self._top_k = int(similarity_top_k)

    def _retrieve(self, query_bundle):  # type: ignore[override]
        query_str = str(getattr(query_bundle, "query_str", "") or "")
        if not query_str.strip():
            return []

        # LlamaIndex provides embedding model via Settings; use it to embed query.
        q_vec = Settings.embed_model.get_text_embedding(query_str)  # type: ignore[no-any-return]

        flt = qmodels.Filter(
            must=[
                qmodels.FieldCondition(
                    key="offer_version",
                    match=qmodels.MatchValue(value=self._offer_version),
                )
            ]
        )
        points = self._qdrant.query_points(
            collection_name=OFFER_COLLECTION,
            query=q_vec,
            limit=self._top_k,
            with_payload=True,
            query_filter=flt,
        ).points

        out: list[NodeWithScore] = []
        for p in points or []:
            payload = p.payload or {}
            text = str(payload.get("text") or "")
            try:
                chunk_id = int(payload.get("chunk_id") or 0)
            except Exception:
                chunk_id = 0
            node = TextNode(
                text=text,
                metadata={
                    "offer_version": self._offer_version,
                    "chunk_id": chunk_id,
                },
            )
            out.append(NodeWithScore(node=node, score=float(p.score or 0.0)))
        return out

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
    splitter = SentenceSplitter(
        chunk_size=_CHUNK_SIZE,
        chunk_overlap=_CHUNK_OVERLAP,
        paragraph_separator="\n\n",
        separator=" ",
    )
    # SentenceSplitter expects documents; avoid pulling full Document stack:
    # Use internal split_text for minimal dependency.
    chunks = splitter.split_text(text)
    return [c.strip() for c in chunks if c and c.strip()]


def _embed_texts(texts: list[str]) -> list[list[float]]:
    _llama_settings()
    # Settings.embed_model is OpenAIEmbedding
    embs = Settings.embed_model.get_text_embedding_batch(texts)  # type: ignore[no-any-return]
    return [[float(x) for x in e] for e in embs]


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
        embeddings.extend(_embed_texts(chunks[i : i + batch]))

    vector_size = len(embeddings[0])
    client = _qdrant()
    _ensure_collection(client, vector_size=vector_size)

    # Полная переиндексация: если повторно индексируем тот же файл (тот же version),
    # то набор чанков может поменяться (из-за параметров splitter), поэтому сначала
    # удаляем все точки этой версии, чтобы не оставить "хвост" старых chunk_id.
    delete_version_points(version=version)

    points: list[qmodels.PointStruct] = []
    for i, (chunk, vec) in enumerate(zip(chunks, embeddings, strict=True)):
        points.append(
            qmodels.PointStruct(
                id=_point_uuid(version=version, chunk_id=i),
                vector=vec,
                payload={
                    # Qdrant payload == LlamaIndex node metadata.
                    "offer_version": version,
                    "chunk_id": i,
                    # Keep raw text also in payload to enable direct UI inspection.
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
    metadata: dict | None = None


def ask_offer(*, question: str, active_version: str) -> tuple[str, list[OfferSource]]:
    engine = _offer_query_engine(version=active_version)
    response = engine.query(question)

    # sources from LlamaIndex
    sources: list[OfferSource] = []
    for sn in getattr(response, "source_nodes", []) or []:
        node = getattr(sn, "node", None)
        score = float(getattr(sn, "score", 0.0) or 0.0)
        text = str(getattr(node, "text", "") or "")
        meta = getattr(node, "metadata", {}) or {}
        try:
            chunk_id = int(meta.get("chunk_id") or 0)
        except Exception:
            chunk_id = 0
        sources.append(OfferSource(score=score, chunk_id=chunk_id, text=text, metadata=dict(meta) if meta else None))

    if not sources:
        # strict no-hallucination contract
        answer = (
            "Коротко:\n"
            "В контексте оферты нет ответа на этот вопрос.\n\n"
            "Простыми словами:\n"
            "Я не нашёл в загруженной оферте подходящих фрагментов, чтобы ответить уверенно.\n\n"
            "Пример:\n"
            "Если в оферте нет условия про конкретную ситуацию, я не могу его придумать.\n\n"
            "Что важно для селлера:\n"
            "Лучше уточнить формулировку вопроса или проверить актуальную редакцию оферты.\n\n"
            "Основание в оферте:\n"
            "Нет релевантных фрагментов в найденных чанках."
        )
        return answer, []

    text_out = str(getattr(response, "response", "") or "").strip()
    return text_out, sources

