from __future__ import annotations

import argparse
import math
import json
import os
from pathlib import Path
import inspect
from typing import Any, Dict, List, Optional

from llama_index.core import (
    Settings,
    SimpleDirectoryReader,
    StorageContext,
    VectorStoreIndex,
    load_index_from_storage,
)
from llama_index.core.node_parser import SentenceSplitter
from llama_index.embeddings.huggingface import HuggingFaceEmbedding

# Dependencies:
# pip install llama-index llama-index-embeddings-huggingface llama-index-vector-stores-faiss faiss-cpu pypdf

DEFAULT_DATA_DIR = os.path.normpath(os.path.join("data", "rag"))
DEFAULT_INDEX_DIR = os.path.normpath(os.path.join("data", "rag_index"))
DEFAULT_EMBEDDING_REPO = "BAAI/bge-small-en-v1.5"
DEFAULT_EMBEDDING_MODEL = os.path.normpath(
    os.path.join("models", "rag", "embeddings", "BAAI_bge-small-en-v1.5")
)

CHUNK_SIZE = 800
CHUNK_OVERLAP = 150
DEFAULT_TOP_K = 6
DEFAULT_RETRIEVAL_K = 18
MAX_EVIDENCE_CHARS = 1200
FUNGAL_TRIGGER_KEYWORDS = (
    "fungal",
    "fungus",
    "mycos",
    "aspergill",
    "candida",
)
NOISE_PATTERNS = (
    "et al",
    "doi:",
    "n engl j med",
    "clin infect dis",
    "respirology",
    "jama",
    "table of contents",
    "clinical questions",
    "question 1",
    "question 2",
    "question 3",
    "question 4",
    "question 5",
    "question 6",
    "question 7",
    "question 8",
    "question 9",
    "question 10",
    "all rights reserved",
    "downloaded from",
    "permissions",
)


class RAGEngine:
    def __init__(
        self,
        data_dir: str = DEFAULT_DATA_DIR,
        index_dir: str = DEFAULT_INDEX_DIR,
        embedding_model_name: Optional[str] = None,
    ) -> None:
        self.data_dir = os.path.normpath(data_dir)
        self.index_dir = os.path.normpath(index_dir)
        self.embedding_model_name = (
            embedding_model_name
            or os.getenv("RAG_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)
        )
        self._index = None
        self._embed_model = None
        self._normalize_embeddings = False
        self._normalization_checked = False
        self._data_dir_abs = os.path.abspath(self.data_dir)
        self._repo_root = os.path.abspath(os.getcwd())

    def build_or_load(self) -> VectorStoreIndex:
        if self._index is not None:
            return self._index
        if self._index_exists():
            print(f"[RAG] Loading index from {self.index_dir}")
            self._index = self.load_index()
        else:
            print(f"[RAG] Building index into {self.index_dir}")
            self._index = self.build_index()
        return self._index

    def build_index(self) -> VectorStoreIndex:
        self._configure_settings()
        if not os.path.isdir(self.data_dir):
            raise FileNotFoundError(f"RAG data directory not found: {self.data_dir}")

        reader = SimpleDirectoryReader(
            input_dir=self.data_dir,
            recursive=True,
            required_exts=[".pdf"],
            file_metadata=self._file_metadata,
            filename_as_id=True,
        )
        documents = reader.load_data()
        if not documents:
            raise ValueError(f"No PDF documents found under {self.data_dir}")

        documents = self._normalize_document_ids(documents)
        vector_store, store_name = self._create_vector_store()
        storage_context = StorageContext.from_defaults(vector_store=vector_store)
        index = VectorStoreIndex.from_documents(
            documents, storage_context=storage_context
        )
        os.makedirs(self.index_dir, exist_ok=True)
        index.storage_context.persist(persist_dir=self.index_dir)
        print(
            f"[RAG] Indexed {len(documents)} documents with {store_name} store -> {self.index_dir}"
        )
        return index

    def load_index(self) -> VectorStoreIndex:
        if not self._index_exists():
            raise FileNotFoundError(
                f"Index directory missing or empty: {self.index_dir}"
            )
        self._configure_settings()
        vector_store = self._load_vector_store()
        if vector_store is None:
            storage_context = StorageContext.from_defaults(persist_dir=self.index_dir)
        else:
            storage_context = StorageContext.from_defaults(
                persist_dir=self.index_dir, vector_store=vector_store
            )
        return load_index_from_storage(storage_context)

    def query(self, query_text: str, top_k: int = DEFAULT_TOP_K) -> List[Dict[str, Any]]:
        if not query_text:
            return []
        query_lower = query_text.lower()
        fungal_trigger = any(
            keyword in query_lower for keyword in FUNGAL_TRIGGER_KEYWORDS
        )
        index = self.build_or_load()
        retrieval_k = max(top_k, DEFAULT_RETRIEVAL_K)
        retriever = index.as_retriever(similarity_top_k=retrieval_k)
        results = retriever.retrieve(query_text)

        evidence: List[Dict[str, Any]] = []
        noisy_evidence: List[Dict[str, Any]] = []
        for item in results:
            node = item.node
            metadata = node.metadata or {}
            text = ""
            if hasattr(node, "get_content"):
                text = node.get_content() or ""
            elif hasattr(node, "text"):
                text = node.text or ""
            text = text[:MAX_EVIDENCE_CHARS]
            text_lower = text.lower()

            tier = metadata.get("tier")
            if tier is None:
                tier = self._compute_tier(
                    metadata.get("category"), metadata.get("source_path")
                )
            combined = self._normalize_for_tier(
                metadata.get("category"), metadata.get("source_path")
            )
            if self._is_fungal_testing_algorithm(combined):
                if fungal_trigger:
                    tier = min(tier, 2)
                else:
                    tier = max(tier, 3)

            record = (
                {
                    "text": text,
                    "source_file": metadata.get("source_file") or "",
                    "source_path": metadata.get("source_path") or "",
                    "category": metadata.get("category") or "",
                    "score": float(item.score) if item.score is not None else None,
                    "_tier": tier,
                }
            )
            if self._is_noise_chunk(text_lower):
                noisy_evidence.append(record)
            else:
                evidence.append(record)

        evidence.sort(key=self._sort_key)
        noisy_evidence.sort(key=self._sort_key)
        trimmed = []
        for item in evidence[:top_k]:
            item.pop("_tier", None)
            trimmed.append(item)
        if not trimmed:
            for item in noisy_evidence[:top_k]:
                item.pop("_tier", None)
                trimmed.append(item)
        return trimmed

    def _index_exists(self) -> bool:
        return os.path.isdir(self.index_dir) and any(os.listdir(self.index_dir))

    def _configure_settings(self) -> None:
        embed_model = self._get_embedding()
        Settings.embed_model = embed_model
        Settings.node_parser = SentenceSplitter(
            chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP
        )
        Settings.llm = None

    def _get_embedding(self):
        if self._embed_model is not None:
            return self._embed_model
        try:
            model_name = self._resolve_embedding_model_name()
            kwargs = self._embedding_kwargs()
            kwargs["model_name"] = model_name
            self._embed_model = HuggingFaceEmbedding(**kwargs)
            return self._embed_model
        except Exception as exc:
            message = (
                "Failed to load embedding model "
                f"'{self.embedding_model_name}'. If you are offline, "
                "download the model in advance or set RAG_EMBEDDING_MODEL "
                "to a local path (e.g., 'models/rag/embeddings/BAAI_bge-small-en-v1.5')."
            )
            raise RuntimeError(message) from exc

    def _embedding_dim(self) -> int:
        embed_model = self._get_embedding()
        sample = embed_model.get_text_embedding("dimension probe")
        if not self._normalization_checked:
            self._normalize_embeddings = self._is_unit_norm(sample)
            self._normalization_checked = True
        return len(sample)

    def _embedding_kwargs(self) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {}
        try:
            params = inspect.signature(HuggingFaceEmbedding.__init__).parameters
        except (TypeError, ValueError):
            params = {}

        if "normalize" in params:
            kwargs["normalize"] = True
        elif "normalize_embeddings" in params:
            kwargs["normalize_embeddings"] = True
        elif "encode_kwargs" in params:
            kwargs["encode_kwargs"] = {"normalize_embeddings": True}
        return kwargs

    def _resolve_embedding_model_name(self) -> str:
        model_name = self.embedding_model_name
        if self._looks_like_local_path(model_name):
            model_path = os.path.normpath(model_name)
            if not os.path.exists(model_path):
                self._download_embedding_model(model_path)
            return model_path
        return model_name

    def _looks_like_local_path(self, model_name: str) -> bool:
        if os.path.isabs(model_name):
            return True
        if self._looks_like_repo_id(model_name):
            return False
        if os.path.altsep and os.path.altsep in model_name:
            return True
        if os.path.sep in model_name:
            return True
        if model_name.startswith(".") or model_name.startswith("models"):
            return True
        if ":" in model_name:
            return True
        return False

    def _looks_like_repo_id(self, model_name: str) -> bool:
        parts = model_name.split("/")
        return len(parts) == 2 and all(part.strip() for part in parts)

    def _download_embedding_model(self, target_dir: str) -> None:
        repo_id = os.getenv("RAG_EMBEDDING_REPO", DEFAULT_EMBEDDING_REPO)
        print(f"[RAG] Downloading embedding model {repo_id} -> {target_dir}")
        try:
            from huggingface_hub import snapshot_download
        except Exception as exc:
            message = (
                "huggingface_hub is required to download models. "
                "Install it with: pip install huggingface_hub"
            )
            raise RuntimeError(message) from exc
        os.makedirs(target_dir, exist_ok=True)
        snapshot_download(
            repo_id=repo_id,
            local_dir=target_dir,
            local_dir_use_symlinks=False,
        )

    def _is_unit_norm(self, vector: List[float]) -> bool:
        if not vector:
            return False
        norm = math.sqrt(sum(value * value for value in vector))
        return 0.99 <= norm <= 1.01

    def _create_vector_store(self):
        try:
            import faiss
            from llama_index.vector_stores.faiss import FaissVectorStore

            dim = self._embedding_dim()
            if self._normalize_embeddings:
                faiss_index = faiss.IndexFlatIP(dim)
                store_name = "faiss_ip"
                print("[RAG] Using FAISS IndexFlatIP (cosine/inner product)")
            else:
                faiss_index = faiss.IndexFlatL2(dim)
                store_name = "faiss_l2"
                print("[RAG] Using FAISS IndexFlatL2 (no normalization detected)")
            return FaissVectorStore(faiss_index=faiss_index), store_name
        except Exception:
            from llama_index.vector_stores.simple import SimpleVectorStore

            print("[RAG] FAISS unavailable, falling back to SimpleVectorStore")
            return SimpleVectorStore(), "simple"

    def _load_vector_store(self):
        vector_store_path = os.path.join(self.index_dir, "default__vector_store.json")
        if not os.path.isfile(vector_store_path):
            return None

        if not self._looks_like_json(vector_store_path):
            try:
                from llama_index.vector_stores.faiss import FaissVectorStore

                return FaissVectorStore.from_persist_dir(self.index_dir)
            except Exception as exc:
                message = (
                    "Index appears to be a FAISS store, but faiss or the FAISS "
                    "vector store plugin is unavailable. Install "
                    "'faiss-cpu' and 'llama-index-vector-stores-faiss', or "
                    "delete the index directory and rebuild."
                )
                raise RuntimeError(message) from exc
        return None

    def _looks_like_json(self, path: str) -> bool:
        try:
            with open(path, "rb") as handle:
                chunk = handle.read(256)
        except OSError:
            return False
        for byte in chunk:
            if byte in (9, 10, 13, 32):
                continue
            return byte in (123, 91)
        return True

    def _normalize_document_ids(self, documents: List[Any]) -> List[Any]:
        normalized: List[Any] = []
        for idx, doc in enumerate(documents):
            metadata = getattr(doc, "metadata", {}) or {}
            source_path = metadata.get("source_path")
            if not source_path:
                file_path = metadata.get("file_path") or metadata.get("file_name")
                if file_path:
                    if os.path.isabs(file_path):
                        source_path = self._make_relative(os.path.abspath(file_path))
                    else:
                        source_path = file_path
            if not source_path:
                source_path = f"document_{idx}"
            source_path = os.path.normpath(source_path)
            if os.path.isabs(source_path):
                source_path = self._make_relative(source_path)

            self._sanitize_metadata_paths(metadata)
            self._set_doc_id(doc, source_path)
            normalized.append(doc)
        return normalized

    def _sanitize_metadata_paths(self, metadata: Dict[str, Any]) -> None:
        for key in ("file_path", "file_dir"):
            value = metadata.get(key)
            if isinstance(value, str) and os.path.isabs(value):
                metadata[key] = self._make_relative(value)

    def _set_doc_id(self, doc: Any, doc_id: str) -> None:
        if hasattr(doc, "doc_id"):
            try:
                doc.doc_id = doc_id
                return
            except Exception:
                pass
        if hasattr(doc, "id_"):
            try:
                doc.id_ = doc_id
                return
            except Exception:
                pass
        if hasattr(doc, "id"):
            try:
                doc.id = doc_id
            except Exception:
                pass

    def _file_metadata(self, file_path: str) -> Dict[str, Any]:
        file_path_abs = os.path.abspath(file_path)
        source_path = self._make_relative(file_path_abs)
        category = self._extract_category(file_path_abs)
        tier = self._compute_tier(category, source_path)
        return {
            "source_file": os.path.basename(file_path),
            "source_path": source_path,
            "category": category,
            "tier": tier,
        }

    def _make_relative(self, file_path_abs: str) -> str:
        try:
            return os.path.relpath(file_path_abs, self._repo_root)
        except ValueError:
            return file_path_abs

    def _extract_category(self, file_path_abs: str) -> str:
        try:
            rel_to_data = os.path.relpath(file_path_abs, self._data_dir_abs)
        except ValueError:
            return "unknown"

        parts = Path(rel_to_data).parts
        if not parts:
            return "unknown"
        if parts[0] in (".", ".."):
            return "unknown"
        return parts[0]

    def _compute_tier(self, category: Optional[str], source_path: Optional[str]) -> int:
        combined = self._normalize_for_tier(category, source_path)
        if self._is_guideline_or_pathway(combined):
            return 1
        return 2

    def _normalize_for_tier(
        self, category: Optional[str], source_path: Optional[str]
    ) -> str:
        category = category or ""
        source_path = source_path or ""
        combined = f"{category} {source_path}".lower()
        return combined.replace("-", "_")

    def _is_guideline_or_pathway(self, combined: str) -> bool:
        if "pneumoniaclinical_guidelines" in combined:
            return True
        if "clinical_guideline" in combined or "clinicalguideline" in combined:
            return True
        if "clinicalpracticeguideline" in combined or "clinical_practice_guideline" in combined:
            return True
        if "clinical_pathway" in combined or "clinicalpathway" in combined:
            return True
        if "decision_pathway" in combined or "decisionpathway" in combined:
            return True
        return False

    def _is_fungal_testing_algorithm(self, combined: str) -> bool:
        if "testingalgorithm" not in combined and "testing_algorithm" not in combined:
            return False
        return any(keyword in combined for keyword in FUNGAL_TRIGGER_KEYWORDS)

    def _sort_key(self, item: Dict[str, Any]) -> Any:
        tier = item.get("_tier") or 2
        score = item.get("score")
        score_key = score if score is not None else -1.0
        return (tier, -score_key)

    def _is_noise_chunk(self, text_lower: str) -> bool:
        return any(pattern in text_lower for pattern in NOISE_PATTERNS)


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG engine for pneumonia ward docs")
    parser.add_argument("--build", action="store_true", help="Build index")
    parser.add_argument("--query", type=str, help="Query text")
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--data-dir", type=str, default=DEFAULT_DATA_DIR)
    parser.add_argument("--index-dir", type=str, default=DEFAULT_INDEX_DIR)
    parser.add_argument("--embedding-model", type=str, default=None)
    args = parser.parse_args()

    engine = RAGEngine(
        data_dir=args.data_dir,
        index_dir=args.index_dir,
        embedding_model_name=args.embedding_model,
    )

    index = None
    if args.build:
        index = engine.build_index()

    if args.query:
        if index is None:
            engine.build_or_load()
        results = engine.query(args.query, top_k=args.top_k)
        print(json.dumps(results, ensure_ascii=False, indent=2))

    if not args.build and not args.query:
        parser.print_help()


if __name__ == "__main__":
    main()
