"""
Face Engine — unified detection + embedding
--------------------------------------------------------
Primary : InsightFace buffalo_l (SCRFD detector + ArcFace 512-d embeddings)
Fallback: face_recognition / dlib (128-d) if InsightFace is unavailable

Tuned defaults for wide classroom CCTV (~20 ft to last row, up to ~80 students).
"""

from __future__ import annotations

import os
os.environ.setdefault("ORT_LOGGING_LEVEL", "3")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
import threading
from dataclasses import dataclass
from typing import List, Optional, Tuple

import cv2
import numpy as np

ARCFACE_SIM_THRESHOLD = float(os.environ.get("ARCFACE_SIM_THRESHOLD", "0.30"))
ARCFACE_SIM_THRESHOLD_STRICT = 0.36
DLIB_SIM_THRESHOLD = 0.42

MODEL_NAME_ARCFACE = "arcface_buffalo_l"
MODEL_NAME_DLIB = "dlib_128"


def _cuda_available() -> bool:
    try:
        import onnxruntime as ort
        return "CUDAExecutionProvider" in ort.get_available_providers()
    except Exception:
        return False


# Auto-tune for CPU vs GPU (override anytime with env vars).
_HAS_CUDA = _cuda_available()
if "DET_SIZE" in os.environ:
    DET_SIZE = int(os.environ["DET_SIZE"])
else:
    DET_SIZE = 1024 if _HAS_CUDA else 640

if "DET_THRESH" in os.environ:
    DET_THRESH = float(os.environ["DET_THRESH"])
else:
    DET_THRESH = 0.20 if _HAS_CUDA else 0.30

# Tiled multi-scale detection is expensive — off by default on CPU.
if "USE_FACE_TILES" in os.environ:
    USE_FACE_TILES = os.environ["USE_FACE_TILES"].strip().lower() in ("1", "true", "yes", "on")
else:
    USE_FACE_TILES = bool(_HAS_CUDA)

# Two-stage pipeline: person/head boxes → enlarge crop → ArcFace (helps ~20ft when only heads are visible)
if "USE_PERSON_STAGE" in os.environ:
    USE_PERSON_STAGE = os.environ["USE_PERSON_STAGE"].strip().lower() in ("1", "true", "yes", "on")
else:
    USE_PERSON_STAGE = True  # try if ultralytics is installed; no-op otherwise

# Min face box after upscale attempt (pixels)
MIN_FACE_PX = int(os.environ.get("MIN_FACE_PX", "12"))
# Upscale small head crops so SCRFD sees a larger face
HEAD_CROP_UPSCALE = float(os.environ.get("HEAD_CROP_UPSCALE", "2.5"))

TRT_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".trt_cache")


@dataclass
class DetectedFace:
    bbox: Tuple[int, int, int, int]
    embedding: np.ndarray
    score: float = 1.0

    @property
    def trbl(self) -> Tuple[int, int, int, int]:
        x1, y1, x2, y2 = self.bbox
        return y1, x2, y2, x1


class FaceEngine:
    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        self.backend = None
        self.model_name = None
        self._app = None
        self._init()

    @classmethod
    def get(cls) -> "FaceEngine":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = FaceEngine()
        return cls._instance

    def _init(self):
        try:
            from insightface.app import FaceAnalysis
            import onnxruntime as ort
            try:
                ort.set_default_logger_severity(3)  # 0=VERBOSE .. 3=ERROR, 4=FATAL
            except Exception:
                pass

            avail = ort.get_available_providers()
            # Do NOT put TensorRT first: if TRT DLLs are missing, InsightFace
            # often falls straight to CPU and never tries CUDA.
            # CUDA alone is enough on a laptop (RTX 3050).
            providers = []
            if "CUDAExecutionProvider" in avail:
                providers.append("CUDAExecutionProvider")
            providers.append("CPUExecutionProvider")

            app = FaceAnalysis(
                name="buffalo_l",
                providers=providers,
                allowed_modules=["detection", "recognition"],  # skip gender/age for speed
            )
            using_gpu = "CUDAExecutionProvider" in providers
            ctx = 0 if using_gpu else -1
            app.prepare(ctx_id=ctx, det_size=(DET_SIZE, DET_SIZE), det_thresh=DET_THRESH)
            self._app = app
            self.using_gpu = using_gpu
            self.backend = "arcface"
            self.model_name = MODEL_NAME_ARCFACE
            print(
                f"[FaceEngine] InsightFace buffalo_l providers={providers} "
                f"det_size={DET_SIZE} det_thresh={DET_THRESH} "
                f"(tuned for ~20ft classroom / multi-student)"
            )
            if using_gpu:
                print("[FaceEngine] GPU path requested: CUDAExecutionProvider")
            else:
                print("[FaceEngine] GPU not available — using CPUExecutionProvider")
            try:
                dummy = np.zeros((min(DET_SIZE, 640), min(DET_SIZE, 640), 3), dtype=np.uint8)
                app.get(dummy)
                print("[FaceEngine] Warmup inference complete.")
            except Exception as e:
                print(f"[FaceEngine] Warmup skipped: {e}")

            self._person_model = None
            if USE_PERSON_STAGE:
                try:
                    from ultralytics import YOLO
                    # nano model — person class 0; downloads once
                    self._person_model = YOLO("yolov8n.pt")
                    print("[FaceEngine] Two-stage ON: YOLOv8n person → head crop → ArcFace")
                except Exception as e:
                    self._person_model = None
                    print(f"[FaceEngine] Two-stage person model unavailable ({e}); using tile/upscale only")
            return
        except Exception as e:
            print(f"[FaceEngine] InsightFace unavailable ({e});")
            raise RuntimeError(
                f"InsightFace is permanently required. Please ensure insightface+onnxruntime are installed. "
                f"Error: {e}"
            )

    def detect_and_embed(self, bgr_image: np.ndarray) -> List[DetectedFace]:
        """Detect faces + embeddings. Uses tiled pass on large frames for small/distant faces."""
        if bgr_image is None or bgr_image.size == 0:
            return []

        if len(bgr_image.shape) == 2:
            bgr_image = cv2.cvtColor(bgr_image, cv2.COLOR_GRAY2BGR)
        elif bgr_image.shape[2] == 1:
            bgr_image = cv2.cvtColor(bgr_image, cv2.COLOR_GRAY2BGR)

        # Contrast boost (lighter on CPU to save time)
        try:
            h0, w0 = bgr_image.shape[:2]
            do_clahe = True
            if not getattr(self, "using_gpu", False) and (h0 * w0) > (1280 * 720):
                # skip full-frame CLAHE on large CPU frames
                do_clahe = False
            if do_clahe:
                lab = cv2.cvtColor(bgr_image, cv2.COLOR_BGR2LAB)
                l, a, b = cv2.split(lab)
                clip = 2.0 if not getattr(self, "using_gpu", False) else 3.0
                clahe = cv2.createCLAHE(clipLimit=clip, tileGridSize=(8, 8))
                l = clahe.apply(l)
                bgr_image = cv2.cvtColor(cv2.merge([l, a, b]), cv2.COLOR_LAB2BGR)
        except Exception:
            pass

        if self.backend == "arcface":
            return self._detect_arcface_pipeline(bgr_image)
        return self._detect_dlib(bgr_image)

    def detect_and_embed_closeup(self, bgr_image: np.ndarray) -> List[DetectedFace]:
        """Fast webcam path (enrollment / manual scan). No YOLO, tiles, or grid."""
        if bgr_image is None or bgr_image.size == 0:
            return []
        if len(bgr_image.shape) == 2:
            bgr_image = cv2.cvtColor(bgr_image, cv2.COLOR_GRAY2BGR)
        elif bgr_image.shape[2] == 1:
            bgr_image = cv2.cvtColor(bgr_image, cv2.COLOR_GRAY2BGR)
        h, w = bgr_image.shape[:2]
        if max(h, w) > 720:
            scale = 720 / float(max(h, w))
            bgr_image = cv2.resize(bgr_image, (int(w * scale), int(h * scale)))
        if self.backend == "arcface":
            return self._detect_arcface_once(bgr_image)
        return self._detect_dlib(bgr_image)

    def _head_region_from_person(self, x1, y1, x2, y2, frame_h, frame_w):
        """Upper portion of a person box ≈ head/shoulders for distant CCTV."""
        pw, ph = x2 - x1, y2 - y1
        # Prefer top ~45% of body box (heads in classroom seating)
        hx1 = max(0, int(x1 - 0.05 * pw))
        hx2 = min(frame_w, int(x2 + 0.05 * pw))
        hy1 = max(0, int(y1 - 0.05 * ph))
        hy2 = min(frame_h, int(y1 + max(ph * 0.45, pw * 0.6)))
        # Expand if box is tiny
        min_side = 64
        if (hx2 - hx1) < min_side:
            cx = (hx1 + hx2) // 2
            hx1 = max(0, cx - min_side // 2)
            hx2 = min(frame_w, cx + min_side // 2)
        if (hy2 - hy1) < min_side:
            cy = (hy1 + hy2) // 2
            hy1 = max(0, cy - min_side // 2)
            hy2 = min(frame_h, cy + min_side // 2)
        return hx1, hy1, hx2, hy2

    def _detect_on_crop_upscaled(self, bgr: np.ndarray, x0: int, y0: int, x1: int, y1: int) -> List[DetectedFace]:
        """Run ArcFace on a crop; upscale small crops so distant heads become detectable."""
        h, w = bgr.shape[:2]
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(w, x1), min(h, y1)
        if x1 - x0 < 8 or y1 - y0 < 8:
            return []
        crop = bgr[y0:y1, x0:x1]
        ch, cw = crop.shape[:2]
        scale = 1.0
        # If crop is small (distant head), enlarge before SCRFD
        target = 160
        if max(ch, cw) < target:
            scale = min(HEAD_CROP_UPSCALE, target / float(max(ch, cw)))
            crop = cv2.resize(crop, (int(cw * scale), int(ch * scale)), interpolation=cv2.INTER_CUBIC)
        faces = self._detect_arcface_once(crop)
        out = []
        for f in faces:
            bx1, by1, bx2, by2 = f.bbox
            # map back to full-frame coords
            out.append(DetectedFace(
                bbox=(
                    int(bx1 / scale) + x0,
                    int(by1 / scale) + y0,
                    int(bx2 / scale) + x0,
                    int(by2 / scale) + y0,
                ),
                embedding=f.embedding,
                score=f.score,
            ))
        return out

    def _detect_person_stage(self, bgr: np.ndarray) -> List[DetectedFace]:
        """Stage-1 person detect → head crop → upscale → ArcFace."""
        if self._person_model is None:
            return []
        h, w = bgr.shape[:2]
        try:
            results = self._person_model.predict(
                bgr, classes=[0], conf=0.25, verbose=False, imgsz=640,
            )
        except Exception:
            return []
        faces: List[DetectedFace] = []
        for r in results:
            if r.boxes is None:
                continue
            for box in r.boxes:
                xyxy = box.xyxy[0].tolist()
                px1, py1, px2, py2 = [int(v) for v in xyxy]
                hx1, hy1, hx2, hy2 = self._head_region_from_person(px1, py1, px2, py2, h, w)
                faces.extend(self._detect_on_crop_upscaled(bgr, hx1, hy1, hx2, hy2))
        return faces

    def _detect_upper_grid(self, bgr: np.ndarray) -> List[DetectedFace]:
        """
        Classroom CCTV: heads sit in the upper/mid band. Dense grid + upscale
        recovers faces when full-frame SCRFD only sees 'head-sized' blobs.
        """
        h, w = bgr.shape[:2]
        faces: List[DetectedFace] = []
        # Focus on upper 70% of the frame (seated students toward camera)
        y_end = int(h * 0.85)
        rows, cols = 3, 4
        cell_h = max(1, y_end // rows)
        cell_w = max(1, w // cols)
        pad_y, pad_x = int(cell_h * 0.15), int(cell_w * 0.15)
        for r in range(rows):
            for c in range(cols):
                x0 = max(0, c * cell_w - pad_x)
                y0 = max(0, r * cell_h - pad_y)
                x1 = min(w, (c + 1) * cell_w + pad_x)
                y1 = min(y_end, (r + 1) * cell_h + pad_y)
                faces.extend(self._detect_on_crop_upscaled(bgr, x0, y0, x1, y1))
        return faces

    def _detect_arcface_pipeline(self, bgr: np.ndarray) -> List[DetectedFace]:
        """
        Full pipeline for distant classroom CCTV:
          1) full-frame + optional tiles
          2) person→head→upscale stage (if YOLO available)
          3) upper-grid upscale stage when few faces found (heads-only view)
        """
        all_faces = self._detect_arcface_multiscale(bgr)

        if USE_PERSON_STAGE and self._person_model is not None:
            all_faces.extend(self._detect_person_stage(bgr))

        # If we still see few faces on a large frame, run head-grid upscale pass
        h, w = bgr.shape[:2]
        if len(all_faces) < 6 and (w >= 800 or h >= 500):
            all_faces.extend(self._detect_upper_grid(bgr))

        return self._nms_faces(all_faces)

    def _detect_arcface_once(self, bgr: np.ndarray) -> List[DetectedFace]:
        faces = self._app.get(bgr)
        out = []
        h, w = bgr.shape[:2]
        for f in faces:
            x1, y1, x2, y2 = [int(v) for v in f.bbox]
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w - 1, x2), min(h - 1, y2)
            # Keep very small boxes (back row ~20 ft)
            if x2 - x1 < MIN_FACE_PX or y2 - y1 < MIN_FACE_PX:
                continue
            emb = np.asarray(f.embedding, dtype=np.float32)
            n = np.linalg.norm(emb)
            if n > 1e-6:
                emb = emb / n
            score = float(getattr(f, "det_score", 1.0))
            out.append(DetectedFace(bbox=(x1, y1, x2, y2), embedding=emb, score=score))
        return out

    def _nms_faces(self, faces: List[DetectedFace], iou_thresh: float = 0.45) -> List[DetectedFace]:
        if len(faces) <= 1:
            return faces
        boxes = np.array([f.bbox for f in faces], dtype=np.float32)
        scores = np.array([f.score for f in faces], dtype=np.float32)
        x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
        areas = (x2 - x1 + 1) * (y2 - y1 + 1)
        order = scores.argsort()[::-1]
        keep = []
        while order.size > 0:
            i = int(order[0])
            keep.append(i)
            if order.size == 1:
                break
            xx1 = np.maximum(x1[i], x1[order[1:]])
            yy1 = np.maximum(y1[i], y1[order[1:]])
            xx2 = np.minimum(x2[i], x2[order[1:]])
            yy2 = np.minimum(y2[i], y2[order[1:]])
            w = np.maximum(0.0, xx2 - xx1 + 1)
            h = np.maximum(0.0, yy2 - yy1 + 1)
            inter = w * h
            iou = inter / (areas[i] + areas[order[1:]] - inter + 1e-6)
            order = order[1:][iou <= iou_thresh]
        return [faces[i] for i in keep]

    def _detect_arcface_multiscale(self, bgr: np.ndarray) -> List[DetectedFace]:
        """
        Full-frame detection + optional overlapping tiles on large images so
        small/distant faces (back of classroom) are not lost when SCRFD
        internal scale is limited.
        """
        h, w = bgr.shape[:2]
        all_faces = self._detect_arcface_once(bgr)

        # Tile pass when frame is large (typical wide CCTV after resize ~1600-1920)
        use_tiles = USE_FACE_TILES and (w >= 1000 or h >= 700)
        if use_tiles:
            # 2x2 grid with ~20% overlap
            overlap = 0.20
            nw, nh = 2, 2
            tw = int(w / nw * (1 + overlap))
            th = int(h / nh * (1 + overlap))
            step_x = max(1, (w - tw) // (nw - 1)) if nw > 1 else 0
            step_y = max(1, (h - th) // (nh - 1)) if nh > 1 else 0
            for yi in range(nh):
                for xi in range(nw):
                    x0 = min(xi * step_x, max(0, w - tw))
                    y0 = min(yi * step_y, max(0, h - th))
                    x1 = min(w, x0 + tw)
                    y1 = min(h, y0 + th)
                    crop = bgr[y0:y1, x0:x1]
                    if crop.size == 0:
                        continue
                    for f in self._detect_arcface_once(crop):
                        bx1, by1, bx2, by2 = f.bbox
                        all_faces.append(DetectedFace(
                            bbox=(bx1 + x0, by1 + y0, bx2 + x0, by2 + y0),
                            embedding=f.embedding,
                            score=f.score,
                        ))

        return self._nms_faces(all_faces)

    def _detect_arcface(self, bgr: np.ndarray) -> List[DetectedFace]:
        return self._detect_arcface_multiscale(bgr)

    def _detect_dlib(self, bgr: np.ndarray) -> List[DetectedFace]:
        import face_recognition
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        locations = face_recognition.face_locations(rgb, model="hog")
        encodings = face_recognition.face_encodings(rgb, locations)
        out = []
        for (top, right, bottom, left), enc in zip(locations, encodings):
            emb = np.asarray(enc, dtype=np.float32)
            n = np.linalg.norm(emb)
            if n > 1e-6:
                emb = emb / n
            out.append(DetectedFace(bbox=(left, top, right, bottom), embedding=emb, score=1.0))
        return out

    def best_match(
        self,
        query_emb: np.ndarray,
        known_embs: List[np.ndarray],
        threshold: Optional[float] = None,
    ) -> Tuple[Optional[int], float]:
        if not known_embs:
            return None, 0.0

        q = np.asarray(query_emb, dtype=np.float32).ravel()
        qn = np.linalg.norm(q)
        if qn > 1e-6:
            q = q / qn

        if self.backend == "arcface":
            thresh = ARCFACE_SIM_THRESHOLD if threshold is None else threshold
        else:
            thresh = DLIB_SIM_THRESHOLD if threshold is None else threshold

        valid = []
        valid_indices = []
        for i, k in enumerate(known_embs):
            kv = np.asarray(k, dtype=np.float32).ravel()
            if kv.shape == q.shape:
                valid.append(kv)
                valid_indices.append(i)
        if not valid:
            return None, 0.0

        K = np.stack(valid)
        norms = np.linalg.norm(K, axis=1, keepdims=True)
        norms[norms < 1e-6] = 1.0
        sims = (K / norms) @ q
        best_local = int(np.argmax(sims))
        best_sim = float(sims[best_local])
        best_i = valid_indices[best_local]

        if best_sim >= thresh:
            return best_i, best_sim
        return None, best_sim

    def embedding_dim(self) -> int:
        return 512 if self.backend == "arcface" else 128
