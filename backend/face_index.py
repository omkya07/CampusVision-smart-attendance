"""
FaceIndex — fast gallery matching for ArcFace (and compatible) embeddings
--------------------------------------------------------
Uses FAISS IndexFlatIP when available (cosine via L2-normalized vectors).
Falls back to NumPy matrix multiply if faiss is not installed.

Typical use:
    index = FaceIndex(dim=512, threshold=0.28)
    index.build(embeddings, meta)          # meta[i] aligned with embeddings[i]
    hit = index.search(query_embedding)    # (meta_item | None, score)
    hits = index.search_batch(queries)     # list of (meta|None, score)
"""

from __future__ import annotations

from typing import Any, List, Optional, Sequence, Tuple

import numpy as np

try:
    import faiss
    _HAS_FAISS = True
except Exception:
    faiss = None
    _HAS_FAISS = False


def _l2_normalize_rows(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float32)
    if x.ndim == 1:
        x = x.reshape(1, -1)
    norms = np.linalg.norm(x, axis=1, keepdims=True)
    norms[norms < 1e-6] = 1.0
    return x / norms


class FaceIndex:
    """
    Exact nearest-neighbor search over face embeddings.

    - FAISS IndexFlatIP: inner product == cosine for L2-normalized vectors
    - NumPy fallback: same math, fine for hundreds of encodings
    """

    def __init__(self, dim: int = 512, threshold: float = 0.28):
        self.dim = int(dim)
        self.threshold = float(threshold)
        self._meta: List[Any] = []
        self._matrix: Optional[np.ndarray] = None  # (N, D) normalized, NumPy path
        self._index = None  # faiss index
        self.backend = "empty"

    @property
    def size(self) -> int:
        return len(self._meta)

    def clear(self) -> None:
        self._meta = []
        self._matrix = None
        self._index = None
        self.backend = "empty"

    def build(
        self,
        embeddings: Sequence[np.ndarray],
        meta: Sequence[Any],
    ) -> int:
        """
        Rebuild the index from parallel lists of embeddings and metadata.
        Returns number of vectors stored.
        """
        self.clear()
        if not embeddings or not meta:
            return 0
        if len(embeddings) != len(meta):
            raise ValueError("embeddings and meta must have the same length")

        rows = []
        kept_meta = []
        for emb, m in zip(embeddings, meta):
            v = np.asarray(emb, dtype=np.float32).ravel()
            if v.size != self.dim:
                continue
            rows.append(v)
            kept_meta.append(m)

        if not rows:
            return 0

        mat = _l2_normalize_rows(np.stack(rows))
        self._meta = list(kept_meta)

        if _HAS_FAISS:
            index = faiss.IndexFlatIP(self.dim)
            index.add(np.ascontiguousarray(mat))
            self._index = index
            self._matrix = mat  # keep for debugging / export
            self.backend = "faiss"
        else:
            self._matrix = mat
            self._index = None
            self.backend = "numpy"

        return len(self._meta)

    def build_from_students_db(self, db: dict, encoding_dim: Optional[int] = None) -> int:
        """
        Build from students_database.pkl structure:
          { prn: { name, roll_no, encodings: { angle: list/array }, ... }, ... }
        meta entries are (prn, student_dict).
        """
        if encoding_dim is not None:
            self.dim = int(encoding_dim)
        embs = []
        meta = []
        for prn, student in (db or {}).items():
            for _angle, enc in (student.get("encodings") or {}).items():
                embs.append(np.asarray(enc, dtype=np.float32))
                meta.append((prn, student))
        return self.build(embs, meta)

    def search(
        self,
        query_emb: np.ndarray,
        threshold: Optional[float] = None,
        k: int = 1,
    ) -> Tuple[Optional[Any], float]:
        """
        Returns (meta | None, best_score).
        meta is None if below threshold or index empty.
        """
        thresh = self.threshold if threshold is None else float(threshold)
        results = self.search_batch(
            np.asarray(query_emb, dtype=np.float32).reshape(1, -1),
            threshold=thresh,
            k=k,
        )
        return results[0]

    def search_batch(
        self,
        queries: np.ndarray,
        threshold: Optional[float] = None,
        k: int = 1,
    ) -> List[Tuple[Optional[Any], float]]:
        """
        queries: (Q, D) or (D,)
        Returns list of (meta|None, score) length Q.
        """
        thresh = self.threshold if threshold is None else float(threshold)
        if self.size == 0:
            q = np.asarray(queries, dtype=np.float32)
            n = 1 if q.ndim == 1 else q.shape[0]
            return [(None, 0.0)] * n

        Q = _l2_normalize_rows(np.asarray(queries, dtype=np.float32))
        if Q.shape[1] != self.dim:
            return [(None, 0.0)] * Q.shape[0]

        if self.backend == "faiss" and self._index is not None:
            scores, ids = self._index.search(np.ascontiguousarray(Q), max(1, k))
            out = []
            for i in range(Q.shape[0]):
                best_id = int(ids[i, 0])
                best_score = float(scores[i, 0])
                if best_id < 0 or best_score < thresh:
                    out.append((None, best_score if best_id >= 0 else 0.0))
                else:
                    out.append((self._meta[best_id], best_score))
            return out

        # NumPy exact cosine (same as FAISS FlatIP on normalized vectors)
        sims = Q @ self._matrix.T  # (Q, N)
        out = []
        for i in range(Q.shape[0]):
            best_local = int(np.argmax(sims[i]))
            best_score = float(sims[i, best_local])
            if best_score < thresh:
                out.append((None, best_score))
            else:
                out.append((self._meta[best_local], best_score))
        return out

    def info(self) -> dict:
        return {
            "backend": self.backend,
            "size": self.size,
            "dim": self.dim,
            "threshold": self.threshold,
            "faiss_available": _HAS_FAISS,
        }
