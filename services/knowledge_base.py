from __future__ import annotations

import os
import re

import chromadb
from chromadb.api.models.Collection import Collection
from chromadb.utils.embedding_functions import DefaultEmbeddingFunction, SentenceTransformerEmbeddingFunction

from utils.logger import get_logger

logger = get_logger("KnowledgeBase")

DEFAULT_COLLECTION_NAME = "dermatology_kb"
DEFAULT_EMBEDDING_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
DEFAULT_EMBEDDING_PROVIDER = "default"
STOPWORDS = {
    "a",
    "o",
    "os",
    "as",
    "de",
    "da",
    "do",
    "das",
    "dos",
    "e",
    "é",
    "em",
    "no",
    "na",
    "nos",
    "nas",
    "um",
    "uma",
    "para",
    "por",
    "com",
    "sem",
    "meu",
    "minha",
    "tenho",
    "estou",
    "quero",
    "preciso",
    "gostaria",
    "seria",
    "como",
}


def get_chroma_path() -> str:
    return os.getenv("CHROMA_DB_PATH", "chromadb")


def get_collection_name() -> str:
    return os.getenv("CHROMA_COLLECTION_NAME", DEFAULT_COLLECTION_NAME)


def get_embedding_model_name() -> str:
    return os.getenv("CHROMA_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)


def get_embedding_provider() -> str:
    return os.getenv("CHROMA_EMBEDDING_PROVIDER", DEFAULT_EMBEDDING_PROVIDER).strip().lower()


def get_embedding_function():
    provider = get_embedding_provider()
    if provider == "sentence_transformer":
        return SentenceTransformerEmbeddingFunction(model_name=get_embedding_model_name())
    return DefaultEmbeddingFunction()


def get_chroma_client() -> chromadb.PersistentClient:
    return chromadb.PersistentClient(path=get_chroma_path())


def get_collection() -> Collection:
    client = get_chroma_client()
    return client.get_collection(
        name=get_collection_name(),
        embedding_function=get_embedding_function(),
    )


def _normalize_query(query: str) -> str:
    return re.sub(r"\s+", " ", query.strip())


def _keyword_query(query: str, max_terms: int = 10) -> str:
    tokens = re.findall(r"[a-zA-ZÀ-ÿ0-9]+", query.lower())
    filtered = [token for token in tokens if token not in STOPWORDS and len(token) > 2]
    if not filtered:
        return _normalize_query(query)
    return " ".join(filtered[:max_terms])


def build_search_queries(query: str) -> list[str]:
    base_query = _normalize_query(query)
    keyword_query = _keyword_query(query)
    contextual_query = f"dermatologia atencao basica {keyword_query}".strip()

    queries: list[str] = []
    for candidate in [base_query, keyword_query, contextual_query]:
        normalized = _normalize_query(candidate)
        if normalized and normalized not in queries:
            queries.append(normalized)

    while len(queries) < 3:
        queries.append(base_query)

    return queries[:3]


def search_knowledge_base(query: str, limit: int = 5, limit_per_query: int = 4) -> list[dict]:
    collection = get_collection()
    aggregated: dict[str, dict] = {}
    search_queries = build_search_queries(query)

    for search_query in search_queries:
        result = collection.query(query_texts=[search_query], n_results=limit_per_query)
        documents = result.get("documents", [[]])[0]
        metadatas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0]
        ids = result.get("ids", [[]])[0]

        for doc_id, document, metadata, distance in zip(ids, documents, metadatas, distances):
            current = aggregated.get(doc_id)
            candidate = {
                "id": doc_id,
                "document": document,
                "metadata": metadata or {},
                "distance": distance,
                "matched_query": search_query,
            }
            if current is None or distance < current["distance"]:
                aggregated[doc_id] = candidate

    matches = sorted(aggregated.values(), key=lambda item: item["distance"])[:limit]
    logger.info(
        "Busca na base dermatologica retornou %s trechos apos deduplicacao usando %s queries.",
        len(matches),
        len(search_queries),
    )
    return matches


def build_rag_context(query: str, limit: int = 5, limit_per_query: int = 4) -> dict:
    queries = build_search_queries(query)
    matches = search_knowledge_base(query=query, limit=limit, limit_per_query=limit_per_query)

    context_parts: list[str] = []
    for index, match in enumerate(matches, start=1):
        metadata = match["metadata"]
        context_parts.append(
            (
                f"[Trecho {index}] "
                f"Fonte: {metadata.get('source_file', 'desconhecida')} | "
                f"Secao: {metadata.get('section_title', 'sem secao')} | "
                f"Paginas: {metadata.get('page_start', '?')}-{metadata.get('page_end', '?')}\n"
                f"{match['document']}"
            )
        )

    return {
        "queries": queries,
        "matches": matches,
        "context": "\n\n".join(context_parts),
    }
