"""
CampusVision — Bulk Import Face Photos & Embeddings into MongoDB Atlas
----------------------------------------------------------------------
Imports student photos (JPG, PNG) from any folder directly into MongoDB Atlas.
For each photo, it:
    1. Detects face using InsightFace / ArcFace
    2. Generates the 512-dimensional biometric embedding
    3. Crops and optimizes an avatar thumbnail (JPEG base64)
    4. Upserts student into the MongoDB Atlas 'students' collection

Usage:
    python import_faces_to_mongo.py <path_to_images_directory>
    python import_faces_to_mongo.py --file <path_to_image> --prn <PRN> --name <Name>

Examples:
    python import_faces_to_mongo.py C:\\Users\\Name\\Desktop\\student_photos
    python import_faces_to_mongo.py ./photos
"""

import os
import sys
import argparse
import base64
import cv2
import numpy as np
from datetime import datetime
from PIL import Image

# Ensure backend directory is in path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db import get_db, check_connection
from face_engine import FaceEngine


def process_and_enroll_image(db, img_bgr, prn, name="", roll_no="", division="", branch=""):
    """Detects face, computes embedding, crops thumbnail, and upserts to MongoDB Atlas."""
    eng = FaceEngine.get()
    detect_fn = getattr(eng, "detect_and_embed_closeup", eng.detect_and_embed)
    faces = detect_fn(img_bgr)

    if not faces:
        return False, "No face detected in image"
    if len(faces) > 1:
        # Sort by bounding box area to pick dominant face if multiple detected
        faces.sort(key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]), reverse=True)

    face = faces[0]
    embedding = face.embedding.tolist()

    # Crop and optimize face photo thumbnail
    h, w = img_bgr.shape[:2]
    x1, y1, x2, y2 = [int(v) for v in face.bbox]
    pad_x = int((x2 - x1) * 0.25)
    pad_y = int((y2 - y1) * 0.25)
    cx1 = max(0, x1 - pad_x)
    cy1 = max(0, y1 - pad_y)
    cx2 = min(w, x2 + pad_x)
    cy2 = min(h, y2 + pad_y)

    crop = img_bgr[cy1:cy2, cx1:cx2]
    if crop.size > 0:
        thumb = cv2.resize(crop, (160, 160), interpolation=cv2.INTER_AREA)
        _, buf = cv2.imencode(".jpg", thumb, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        thumb_b64 = "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode("utf-8")
    else:
        thumb_b64 = ""

    # Check if student exists in MongoDB
    existing = db.students.find_one({"_id": str(prn)}) or db.students.find_one({"prn": str(prn)})
    if existing:
        encodings = existing.get("encodings", {})
        encodings["center"] = embedding
        photos = existing.get("photos", {})
        if thumb_b64:
            photos["center"] = thumb_b64

        update_doc = {
            "encodings": encodings,
            "embedding_model": eng.model_name,
            "updated_at": datetime.now().isoformat(),
        }
        if thumb_b64:
            update_doc["photo"] = thumb_b64
            update_doc["photos"] = photos
        if name and not existing.get("name"):
            update_doc["name"] = name
        if roll_no and not existing.get("roll_no"):
            update_doc["roll_no"] = roll_no

        db.students.update_one({"_id": existing["_id"]}, {"$set": update_doc})
        return True, f"Updated existing student {existing.get('name', prn)} with face biometrics"
    else:
        doc = {
            "_id": str(prn),
            "prn": str(prn),
            "roll_no": str(roll_no or prn),
            "name": name or str(prn),
            "division": division or "A",
            "branch": branch or "CSE",
            "photo": thumb_b64,
            "photos": {"center": thumb_b64} if thumb_b64 else {},
            "encodings": {"center": embedding},
            "embedding_model": eng.model_name,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
        }
        db.students.replace_one({"_id": str(prn)}, doc, upsert=True)
        return True, f"Enrolled new student {doc['name']} (PRN {prn}) with face biometrics"


def import_directory(dir_path):
    """Walk directory and import all student images found."""
    if not os.path.exists(dir_path):
        print(f"[ERROR] Directory does not exist: {dir_path}")
        return

    print("=" * 60)
    print("  CampusVision — Importing Face Images to MongoDB Atlas")
    print(f"  Target Directory: {dir_path}")
    print("=" * 60)

    try:
        check_connection()
        db = get_db()
        print("  MongoDB Atlas Connection: OK\n")
    except Exception as e:
        print(f"  MongoDB Atlas Connection FAILED: {e}")
        return

    valid_exts = {".jpg", ".jpeg", ".png", ".webp"}
    count_success = 0
    count_failed = 0

    for root, _, files in os.walk(dir_path):
        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            if ext not in valid_exts:
                continue

            filepath = os.path.join(root, fname)
            stem = os.path.splitext(fname)[0]

            # Parse filename patterns:
            # 1. PRN_Name.jpg -> prn, name
            # 2. PRN.jpg -> prn
            # 3. RollNo_Name_PRN.jpg
            parts = stem.split("_")
            if len(parts) >= 2:
                prn = parts[0].strip()
                name = " ".join(parts[1:]).strip()
            else:
                prn = stem.strip()
                name = stem.strip()

            try:
                # Read image safely handling unicode paths
                img_data = np.fromfile(filepath, dtype=np.uint8)
                img_bgr = cv2.imdecode(img_data, cv2.IMREAD_COLOR)
                if img_bgr is None:
                    print(f"  [SKIP] Could not decode image: {fname}")
                    count_failed += 1
                    continue

                ok, msg = process_and_enroll_image(db, img_bgr, prn, name=name)
                if ok:
                    print(f"  [OK] {fname} -> {msg}")
                    count_success += 1
                else:
                    print(f"  [FAIL] {fname}: {msg}")
                    count_failed += 1
            except Exception as e:
                print(f"  [ERROR] {fname}: {e}")
                count_failed += 1

    print("\n" + "=" * 60)
    print(f"  Import Completed: {count_success} succeeded, {count_failed} failed.")
    total_in_db = db.students.count_documents({})
    print(f"  Total students in MongoDB Atlas: {total_in_db}")
    print("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import student face photos into MongoDB Atlas.")
    parser.add_argument("path", nargs="?", default="", help="Directory containing student images")
    parser.add_argument("--file", help="Single image file path")
    parser.add_argument("--prn", help="Student PRN (when using --file)")
    parser.add_argument("--name", help="Student full name (when using --file)")

    args = parser.parse_args()

    if args.file:
        if not args.prn:
            print("Error: --prn is required when importing a single file.")
            sys.exit(1)
        check_connection()
        db = get_db()
        img_data = np.fromfile(args.file, dtype=np.uint8)
        img_bgr = cv2.imdecode(img_data, cv2.IMREAD_COLOR)
        ok, msg = process_and_enroll_image(db, img_bgr, args.prn, name=args.name or "")
        print(f"Result: {msg}")
    elif args.path:
        import_directory(args.path)
    else:
        print("Usage:")
        print("  python import_faces_to_mongo.py <directory_of_images>")
        print("  python import_faces_to_mongo.py --file <image.jpg> --prn <PRN> --name <Name>")
