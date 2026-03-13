from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

import chromadb
from chromadb.utils.embedding_functions import DefaultEmbeddingFunction, SentenceTransformerEmbeddingFunction
from pypdf import PdfReader

DEFAULT_CHROMA_PATH = "chromadb"
DEFAULT_COLLECTION_NAME = "dermatology_kb"
DEFAULT_EMBEDDING_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
DEFAULT_EMBEDDING_PROVIDER = "default"
DEFAULT_INPUT_DIR = "pdfs"
DEFAULT_CHUNK_SIZE = 1400
DEFAULT_CHUNK_OVERLAP = 220
DEFAULT_MIN_CHUNK_SIZE = 300

# python scripts/build_derm_kb.py --reset


@dataclass
class SectionChunk:
    chunk_id: str
    text: str
    metadata: dict


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Processa PDFs de dermatologia e gera uma collection persistente no ChromaDB."
    )
    parser.add_argument("--input-dir", default=DEFAULT_INPUT_DIR, help="Diretorio com PDFs.")
    parser.add_argument("--db-path", default=os.getenv("CHROMA_DB_PATH", DEFAULT_CHROMA_PATH), help="Pasta do ChromaDB.")
    parser.add_argument(
        "--collection",
        default=os.getenv("CHROMA_COLLECTION_NAME", DEFAULT_COLLECTION_NAME),
        help="Nome da collection no ChromaDB.",
    )
    parser.add_argument(
        "--embedding-model",
        default=os.getenv("CHROMA_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
        help="Modelo usado quando o provider for sentence_transformer.",
    )
    parser.add_argument(
        "--embedding-provider",
        default=os.getenv("CHROMA_EMBEDDING_PROVIDER", DEFAULT_EMBEDDING_PROVIDER),
        choices=["default", "sentence_transformer"],
        help="Provider de embedding. 'default' usa o embedding interno do Chroma e evita torch.",
    )
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE, help="Tamanho alvo do chunk em caracteres.")
    parser.add_argument(
        "--chunk-overlap",
        type=int,
        default=DEFAULT_CHUNK_OVERLAP,
        help="Overlap em caracteres entre chunks consecutivos.",
    )
    parser.add_argument(
        "--min-chunk-size",
        type=int,
        default=DEFAULT_MIN_CHUNK_SIZE,
        help="Tamanho minimo de chunk; abaixo disso sera fundido quando possivel.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Apaga e recria a collection antes da ingestao.",
    )
    return parser.parse_args()


def normalize_text(text: str) -> str:
    text = text.replace("\u00a0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def is_heading(line: str) -> bool:
    candidate = line.strip()
    if len(candidate) < 4 or len(candidate) > 100:
        return False
    if candidate.endswith("."):
        return False
    if re.match(r"^\d+$", candidate):
        return False
    if re.match(r"^pagina \d+$", candidate.lower()):
        return False
    if candidate.isupper():
        return True
    if re.match(r"^\d+(\.\d+)*\s+[A-ZÀ-Ü]", candidate):
        return True
    title_words = candidate.split()
    capitalized = sum(word[:1].isupper() for word in title_words if word)
    return len(title_words) <= 10 and capitalized >= max(1, len(title_words) - 1)


def extract_pdf_pages(pdf_path: Path) -> list[dict]:
    reader = PdfReader(str(pdf_path))
    pages: list[dict] = []
    for index, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        pages.append(
            {
                "page_number": index,
                "text": normalize_text(text),
            }
        )
    return pages


def build_sections(pages: list[dict]) -> list[dict]:
    sections: list[dict] = []
    current_heading = "Introducao"
    current_lines: list[str] = []
    current_start_page = pages[0]["page_number"] if pages else 1
    current_end_page = current_start_page

    def flush_section() -> None:
        nonlocal current_lines, current_heading, current_start_page, current_end_page
        content = normalize_text("\n".join(current_lines))
        if content:
            sections.append(
                {
                    "heading": current_heading,
                    "page_start": current_start_page,
                    "page_end": current_end_page,
                    "text": content,
                }
            )
        current_lines = []

    for page in pages:
        lines = [line.strip() for line in page["text"].splitlines() if line.strip()]
        for line in lines:
            if is_heading(line) and current_lines:
                flush_section()
                current_heading = line
                current_start_page = page["page_number"]
                current_end_page = page["page_number"]
                continue
            current_end_page = page["page_number"]
            current_lines.append(line)

    flush_section()
    return sections


def split_text(text: str, chunk_size: int, chunk_overlap: int, min_chunk_size: int) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    chunks: list[str] = []
    current = ""

    for paragraph in paragraphs:
        candidate = paragraph if not current else f"{current}\n\n{paragraph}"
        if len(candidate) <= chunk_size:
            current = candidate
            continue

        if current:
            chunks.append(current)
        if len(paragraph) <= chunk_size:
            current = paragraph
            continue

        sentences = re.split(r"(?<=[.!?])\s+", paragraph)
        sentence_chunk = ""
        for sentence in sentences:
            sentence_candidate = sentence if not sentence_chunk else f"{sentence_chunk} {sentence}"
            if len(sentence_candidate) <= chunk_size:
                sentence_chunk = sentence_candidate
                continue
            if sentence_chunk:
                chunks.append(sentence_chunk)
            sentence_chunk = sentence
        current = sentence_chunk

    if current:
        chunks.append(current)

    merged: list[str] = []
    for chunk in chunks:
        if merged and len(chunk) < min_chunk_size:
            merged[-1] = f"{merged[-1]}\n\n{chunk}"
        else:
            merged.append(chunk)

    if chunk_overlap <= 0:
        return merged

    overlapped: list[str] = []
    for index, chunk in enumerate(merged):
        if index == 0:
            overlapped.append(chunk)
            continue
        previous_tail = merged[index - 1][-chunk_overlap:]
        overlapped.append(f"{previous_tail}\n{chunk}")
    return overlapped


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def build_chunks(
    pdf_path: Path,
    sections: list[dict],
    chunk_size: int,
    chunk_overlap: int,
    min_chunk_size: int,
) -> list[SectionChunk]:
    file_hash = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    chunks: list[SectionChunk] = []

    for section_index, section in enumerate(sections):
        split_chunks = split_text(
            section["text"],
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            min_chunk_size=min_chunk_size,
        )
        for chunk_index, chunk_text in enumerate(split_chunks):
            normalized_chunk = normalize_text(chunk_text)
            chunk_hash = hashlib.sha256(
                f"{pdf_path.name}:{section_index}:{chunk_index}:{normalized_chunk}".encode("utf-8")
            ).hexdigest()[:16]
            metadata = {
                "source_file": pdf_path.name,
                "source_path": str(pdf_path),
                "document_hash": file_hash,
                "section_title": section["heading"],
                "section_index": section_index,
                "chunk_index": chunk_index,
                "page_start": section["page_start"],
                "page_end": section["page_end"],
                "char_count": len(normalized_chunk),
                "token_estimate": estimate_tokens(normalized_chunk),
            }
            chunks.append(
                SectionChunk(
                    chunk_id=f"{pdf_path.stem}-{chunk_hash}",
                    text=normalized_chunk,
                    metadata=metadata,
                )
            )

    return chunks


def upsert_collection(
    chunks: list[SectionChunk],
    db_path: str,
    collection_name: str,
    embedding_provider: str,
    embedding_model: str,
    reset: bool,
) -> dict:
    client = chromadb.PersistentClient(path=db_path)
    if embedding_provider == "sentence_transformer":
        embedding_function = SentenceTransformerEmbeddingFunction(model_name=embedding_model)
    else:
        embedding_function = DefaultEmbeddingFunction()

    if reset:
        try:
            client.delete_collection(collection_name)
        except Exception:
            pass

    collection = client.get_or_create_collection(
        name=collection_name,
        embedding_function=embedding_function,
        metadata={"embedding_model": embedding_model},
    )

    collection.upsert(
        ids=[chunk.chunk_id for chunk in chunks],
        documents=[chunk.text for chunk in chunks],
        metadatas=[chunk.metadata for chunk in chunks],
    )

    return {
        "collection_name": collection_name,
        "db_path": db_path,
        "embedding_provider": embedding_provider,
        "embedding_model": embedding_model,
        "documents_in_collection": collection.count(),
    }


def write_manifest(db_summary: dict, chunks: list[SectionChunk], output_path: Path) -> None:
    per_file: dict[str, dict] = {}
    for chunk in chunks:
        source_file = chunk.metadata["source_file"]
        file_entry = per_file.setdefault(
            source_file,
            {
                "chunks": 0,
                "pages": set(),
                "sections": set(),
            },
        )
        file_entry["chunks"] += 1
        file_entry["pages"].update(range(chunk.metadata["page_start"], chunk.metadata["page_end"] + 1))
        file_entry["sections"].add(chunk.metadata["section_title"])

    serializable_files = {
        file_name: {
            "chunks": data["chunks"],
            "pages_indexed": len(data["pages"]),
            "sections_detected": len(data["sections"]),
        }
        for file_name, data in per_file.items()
    }

    manifest = {
        **db_summary,
        "files": serializable_files,
        "total_chunks": len(chunks),
    }
    output_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=True), encoding="utf-8")


def main() -> None:
    args = parse_args()
    input_dir = Path(args.input_dir)
    pdf_paths = sorted(input_dir.glob("*.pdf"))
    if not pdf_paths:
        raise SystemExit(f"Nenhum PDF encontrado em {input_dir}")

    all_chunks: list[SectionChunk] = []
    for pdf_path in pdf_paths:
        pages = extract_pdf_pages(pdf_path)
        sections = build_sections(pages)
        chunks = build_chunks(
            pdf_path,
            sections,
            chunk_size=args.chunk_size,
            chunk_overlap=args.chunk_overlap,
            min_chunk_size=args.min_chunk_size,
        )
        all_chunks.extend(chunks)
        print(f"[ok] {pdf_path.name}: {len(pages)} paginas, {len(sections)} secoes, {len(chunks)} chunks")

    db_summary = upsert_collection(
        chunks=all_chunks,
        db_path=args.db_path,
        collection_name=args.collection,
        embedding_provider=args.embedding_provider,
        embedding_model=args.embedding_model,
        reset=args.reset,
    )
    manifest_path = Path("kb_manifest.json")
    write_manifest(db_summary, all_chunks, manifest_path)

    print(
        f"[ok] Collection '{db_summary['collection_name']}' atualizada em {db_summary['db_path']} "
        f"com {db_summary['documents_in_collection']} chunks."
    )
    print(f"[ok] Manifesto salvo em {manifest_path}")


if __name__ == "__main__":
    main()
