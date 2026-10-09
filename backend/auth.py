"""
Firebase Authentication Middleware
--------------------------------------------------------
Verifies Firebase ID tokens on student API routes and links
authenticated users to their student records in MongoDB.

Setup:
    1. Create a Firebase project at https://console.firebase.google.com
    2. Enable Email/Password, Google, and Phone sign-in providers
    3. Go to Project Settings → Service accounts → Generate new private key
    4. Save the JSON file as backend/firebase-service-account.json
       (or set FIREBASE_SERVICE_ACCOUNT env var to the file path)
    5. Add Firebase web config to .env (see .env.example)
"""

import os
import json
import logging
import functools
from flask import request, jsonify, g

logger = logging.getLogger("campusvision.auth")

_firebase_app = None
_firebase_initialized = False


def _init_firebase():
    """Initialize Firebase Admin SDK once. Safe to call multiple times."""
    global _firebase_app, _firebase_initialized
    if _firebase_initialized:
        return _firebase_app is not None

    _firebase_initialized = True

    try:
        import firebase_admin
        from firebase_admin import credentials
    except ImportError:
        logger.error(
            "firebase-admin package not installed. "
            "Run: pip install firebase-admin"
        )
        return False

    # Look for service account credentials
    sa_path = os.environ.get(
        "FIREBASE_SERVICE_ACCOUNT",
        os.path.join(os.path.dirname(__file__), "firebase-service-account.json"),
    )
    sa_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON")

    try:
        if sa_json:
            cred = credentials.Certificate(json.loads(sa_json))
        elif os.path.exists(sa_path):
            cred = credentials.Certificate(sa_path)
        else:
            print(f"Firebase service account not found at {sa_path}. Student auth will not work until configured.")
            logger.warning(
                "Firebase service account not found at %s. "
                "Student auth will not work until configured. "
                "See backend/auth.py docstring for setup instructions.",
                sa_path,
            )
            return False

        _firebase_app = firebase_admin.initialize_app(cred)
        logger.info("Firebase Admin SDK initialized successfully.")
        return True

    except Exception as e:
        logger.error("Failed to initialize Firebase Admin SDK: %s", e)
        return False


def verify_firebase_token(id_token):
    """
    Verify a Firebase ID token and return the decoded claims.
    Returns None if verification fails.
    """
    if not _init_firebase():
        return None

    try:
        from firebase_admin import auth
        decoded = auth.verify_id_token(id_token)
        return decoded
    except Exception as e:
        print("Token verification failed:", e)
        return None


def require_student_auth(f):
    """
    Decorator for student API routes.
    Verifies Firebase ID token from Authorization header,
    looks up the linked student record, and attaches it to Flask g context.

    Usage:
        @app.route("/api/student/profile")
        @require_student_auth
        def student_profile():
            student = g.student  # linked student record
            firebase_user = g.firebase_user  # decoded token claims
            ...
    """
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        # Extract token from Authorization header
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Missing or invalid Authorization header"}), 401

        id_token = auth_header[7:]  # Strip "Bearer "
        decoded = verify_firebase_token(id_token)
        if not decoded:
            return jsonify({"error": "Invalid or expired token. Please sign in again."}), 401

        # Extract user info from token
        uid = decoded.get("uid")
        email = decoded.get("email", "")
        phone = decoded.get("phone_number", "")

        # Look up student by linked email
        from db import get_db
        db = get_db()

        student = None
        if email:
            student = db.students.find_one({"email": email.lower().strip()})
        if not student and phone:
            student = db.students.find_one({"phone": phone})
        if not student and uid:
            student = db.students.find_one({"firebase_uid": uid})

        if not student:
            return jsonify({
                "error": "account_not_linked",
                "message": (
                    "No student record is linked to this account. "
                    "Please contact your admin to assign your email to your student record."
                ),
                "email": email,
                "phone": phone,
            }), 403

        # Update firebase_uid if not set yet (first-time link)
        if not student.get("firebase_uid"):
            update_fields = {"firebase_uid": uid}
            if email and not student.get("email"):
                update_fields["email"] = email.lower().strip()
            if phone and not student.get("phone"):
                update_fields["phone"] = phone
            update_fields["linked_at"] = __import__("datetime").datetime.now().isoformat()
            db.students.update_one({"_id": student["_id"]}, {"$set": update_fields})
            student.update(update_fields)

        # Attach to Flask g context
        g.student = student
        g.firebase_user = decoded
        g.firebase_uid = uid

        return f(*args, **kwargs)

    return decorated


def get_firebase_config():
    """
    Return the Firebase web config for the frontend.
    Reads from environment variables.
    """
    config = {
        "apiKey": os.environ.get("FIREBASE_API_KEY", ""),
        "authDomain": os.environ.get("FIREBASE_AUTH_DOMAIN", ""),
        "projectId": os.environ.get("FIREBASE_PROJECT_ID", ""),
        "storageBucket": os.environ.get("FIREBASE_STORAGE_BUCKET", ""),
        "messagingSenderId": os.environ.get("FIREBASE_MESSAGING_SENDER_ID", ""),
        "appId": os.environ.get("FIREBASE_APP_ID", ""),
    }
    return config
