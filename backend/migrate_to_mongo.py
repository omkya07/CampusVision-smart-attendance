"""
One-Time Migration: Local Files -> MongoDB
--------------------------------------------------------
Imports your existing students_database.pkl, attendance_log.csv, and
classrooms.json into MongoDB, so you don't lose already-enrolled students
or attendance history when switching over to the database-backed version.

Run this ONCE, after setting up .env with your MONGODB_URI, BEFORE starting
app.py for the first time on the new database-backed code. Safe to re-run —
it upserts by ID and skips attendance rows it's already imported, so running
it twice won't create duplicates.

Usage:
    python migrate_to_mongo.py
"""

import os
import pickle
import json
import csv

from db import get_db, check_connection

STUDENTS_DB_PATH = "students_database.pkl"
ATTENDANCE_LOG = "attendance_log.csv"
CLASSROOMS_DB_PATH = "classrooms.json"
CAMERAS_DB_PATH = "cameras.json"  # older name, migrated by the app itself previously


def migrate_students():
    if not os.path.exists(STUDENTS_DB_PATH):
        print(f"  No {STUDENTS_DB_PATH} found - skipping students.")
        return
    with open(STUDENTS_DB_PATH, "rb") as f:
        data = pickle.load(f)

    db = get_db()
    count = 0
    for prn, s in data.items():
        doc = dict(s)
        doc["_id"] = prn
        doc["prn"] = prn
        db.students.replace_one({"_id": prn}, doc, upsert=True)
        count += 1
    print(f"  Migrated {count} student(s).")


def migrate_attendance():
    if not os.path.exists(ATTENDANCE_LOG):
        print(f"  No {ATTENDANCE_LOG} found - skipping attendance.")
        return

    db = get_db()
    count = 0
    skipped = 0
    with open(ATTENDANCE_LOG, "r", newline="") as f:
        reader = csv.reader(f)
        header = next(reader, None)  # skip header row

        for row in reader:
            if len(row) < 8:
                continue
            doc = {
                "session": row[0],
                "prn": row[1],
                "roll_no": row[2],
                "name": row[3],
                "division": row[4],
                "branch": row[5],
                "date": row[6],
                "time": row[7],
            }
            # Avoid duplicate inserts if this script is run more than once
            exists = db.attendance.find_one({
                "prn": doc["prn"], "session": doc["session"],
                "date": doc["date"], "time": doc["time"],
            })
            if exists:
                skipped += 1
                continue
            db.attendance.insert_one(doc)
            count += 1
    print(f"  Migrated {count} attendance record(s), skipped {skipped} already present.")


def migrate_classrooms():
    path = CLASSROOMS_DB_PATH
    if not os.path.exists(path) and os.path.exists(CAMERAS_DB_PATH):
        path = CAMERAS_DB_PATH  # fall back to the older filename

    if not os.path.exists(path):
        print(f"  No {CLASSROOMS_DB_PATH} found - skipping classrooms.")
        return

    with open(path, "r", encoding="utf-8") as f:
        rows = json.load(f)

    db = get_db()
    count = 0
    for r in rows:
        doc = dict(r)
        row_id = doc.get("id") or doc.get("name") or doc.get("classroom")
        doc["_id"] = row_id
        doc.setdefault("id", row_id)
        # normalize to the current field name if this came from the old cameras.json shape
        if "classroom" not in doc and "name" in doc:
            doc["classroom"] = doc["name"]
        db.classrooms.replace_one({"_id": row_id}, doc, upsert=True)
        count += 1
    print(f"  Migrated {count} classroom(s).")


if __name__ == "__main__":
    print("=" * 60)
    print("  CampusVision — Migrating local data into MongoDB")
    print("=" * 60)

    try:
        check_connection()
        print("  MongoDB connection: OK\n")
    except Exception as e:
        print(f"  MongoDB connection FAILED: {e}")
        print("  Fix .env (see .env.example) before running this script.")
        raise SystemExit(1)

    migrate_students()
    migrate_attendance()
    migrate_classrooms()

    print("\n" + "=" * 60)
    print("  Migration complete. You can now run: python app.py")
    print("=" * 60)
