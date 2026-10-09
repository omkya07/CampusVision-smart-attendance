"""
MongoDB Connection
--------------------------------------------------------
Single shared connection to MongoDB Atlas (or any MongoDB instance), used by
every storage function in app.py. Reads the connection string from a .env
file so the real credentials never get committed to git.

Setup (one-time):
    1. Create a free MongoDB Atlas cluster: https://www.mongodb.com/cloud/atlas/register
    2. Database Access -> create a user (username + password)
    3. Network Access -> Add IP Address -> Allow Access from Anywhere (0.0.0.0/0)
       (fine for a college project demo; tighten this for real production use)
    4. Connect -> Drivers -> copy the connection string
    5. Copy .env.example to .env and paste your connection string into it

Usage elsewhere in the backend:
    from db import get_db
    db = get_db()
    db.students.find_one({"_id": prn})
"""

import os
import sys
import threading
import logging
from pymongo import MongoClient
from pymongo.server_api import ServerApi
from pymongo.errors import (
    PyMongoError,
    ServerSelectionTimeoutError,
    OperationFailure,
    ConfigurationError,
)

logger = logging.getLogger("campusvision.db")


def _load_env():
    """Load environment variables defensively from cwd, project root, and backend/."""
    try:
        from dotenv import load_dotenv
        # 1. Standard search from current working directory
        load_dotenv()
        # 2. Project root (parent of backend/)
        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        root_env = os.path.join(root_dir, ".env")
        if os.path.exists(root_env):
            load_dotenv(root_env, override=False)
        # 3. backend/ directory
        backend_env = os.path.join(os.path.dirname(__file__), ".env")
        if os.path.exists(backend_env):
            load_dotenv(backend_env, override=False)
    except ImportError:
        pass  # python-dotenv not installed - fine if MONGODB_URI is set another way


_load_env()

MONGODB_URI = os.environ.get("MONGODB_URI")
MONGODB_DB_NAME = os.environ.get("MONGODB_DB_NAME", "campusvision")

_lock = threading.Lock()
_client = None
_db = None
_indexes_initialized = False


def _get_certifi_ca_file():
    """Retrieve certifi CA bundle if available to prevent Windows SSL verification failures."""
    try:
        import certifi
        return certifi.where()
    except ImportError:
        return None


def _ensure_indexes(db):
    """Safely create or verify collection indexes without crashing the app on startup."""
    global _indexes_initialized
    if _indexes_initialized:
        return

    indexes = [
        ("students", "prn", {"unique": True}),
        ("students", "email", {"unique": True, "sparse": True}),
        ("students", "firebase_uid", {"unique": True, "sparse": True}),
        ("attendance", [("prn", 1), ("session", 1), ("date", 1)], {}),
        ("attendance", "date", {}),
        ("classrooms", "id", {"unique": True}),
        ("attendance_requests", [("prn", 1), ("date", 1), ("session", 1)], {}),
        ("attendance_requests", "status", {}),
    ]

    for coll_name, keys, kwargs in indexes:
        try:
            db[coll_name].create_index(keys, **kwargs)
        except OperationFailure as e:
            # Code 86 = IndexKeySpecsConflict (already exists with slightly different specs/name)
            if getattr(e, "code", None) == 86:
                pass
            else:
                logger.warning("Could not create index %s on %s: %s", keys, coll_name, e)
        except Exception as e:
            logger.warning("Could not create index %s on %s: %s", keys, coll_name, e)

    _indexes_initialized = True


def get_db():
    """Returns the shared database handle, thread-safe, connecting on first call."""
    global _client, _db, MONGODB_URI, MONGODB_DB_NAME

    if _db is not None:
        return _db

    with _lock:
        if _db is not None:
            return _db

        # Refresh URI from env if not set at import time
        uri = os.environ.get("MONGODB_URI") or MONGODB_URI
        if not uri:
            _load_env()
            uri = os.environ.get("MONGODB_URI")

        if not uri:
            raise RuntimeError(
                "MONGODB_URI is not set. Copy .env.example to .env and fill in your "
                "MongoDB Atlas connection string before running the app."
            )

        MONGODB_URI = uri

        client_kwargs = {
            "server_api": ServerApi("1"),
            "serverSelectionTimeoutMS": int(os.environ.get("MONGODB_TIMEOUT_MS", 10000)),
            "connectTimeoutMS": 20000,
            "socketTimeoutMS": 20000,
            "maxPoolSize": 50,
            "minPoolSize": 1,
        }

        # Prevent Windows/macOS SSL certificate verify errors with Atlas
        ca_file = _get_certifi_ca_file()
        if ca_file:
            client_kwargs["tlsCAFile"] = ca_file

        try:
            client = MongoClient(uri, **client_kwargs)
        except ConfigurationError as e:
            err_msg = str(e).lower()
            if "dnspython" in err_msg:
                raise RuntimeError(
                    "Missing 'dnspython' package required for mongodb+srv:// connections. "
                    "Run: pip install dnspython (or pip install 'pymongo[srv]')"
                ) from e
            elif "resolution lifetime expired" in err_msg or "dns" in err_msg:
                raise RuntimeError(
                    "DNS resolution timed out for MongoDB Atlas SRV URI. "
                    "This usually happens on restricted WiFi, mobile hotspots, or slow DNS. "
                    "Please check your internet connection or try changing your DNS to 8.8.8.8 / 1.1.1.1."
                ) from e
            raise

        # Determine database name
        db_name = os.environ.get("MONGODB_DB_NAME") or MONGODB_DB_NAME
        try:
            default_db = client.get_default_database()
            if default_db is not None and not os.environ.get("MONGODB_DB_NAME"):
                database = default_db
                db_name = default_db.name
            else:
                database = client[db_name or "campusvision"]
        except Exception:
            database = client[db_name or "campusvision"]

        MONGODB_DB_NAME = db_name

        # Ensure indexes safely
        _ensure_indexes(database)

        _client = client
        _db = database
        return _db


def check_connection():
    """
    Quick connectivity check - call at startup to fail fast with a clear, helpful error.
    Returns True if successful, or raises a descriptive RuntimeError on failure.
    """
    try:
        db = get_db()
        _client.admin.command("ping")
        return True
    except ServerSelectionTimeoutError as e:
        raise RuntimeError(
            "MongoDB Atlas connection timed out (server unreachable). "
            "Please check:\n"
            "  1. Network Access in MongoDB Atlas has 0.0.0.0/0 (allow from anywhere) enabled.\n"
            "  2. Your internet connection and firewall settings.\n"
            f"Details: {e}"
        ) from e
    except OperationFailure as e:
        if "auth" in str(e).lower() or getattr(e, "code", None) in (18, 8000):
            raise RuntimeError(
                "MongoDB Atlas authentication failed. "
                "Please verify your database username and password in .env (MONGODB_URI)."
            ) from e
        raise RuntimeError(f"MongoDB operation failed: {e}") from e
    except PyMongoError as e:
        raise RuntimeError(f"MongoDB connection error: {e}") from e


def close_db():
    """Closes the active MongoDB client connection and resets handles."""
    global _client, _db, _indexes_initialized
    with _lock:
        if _client is not None:
            try:
                _client.close()
            except Exception:
                pass
            _client = None
            _db = None
            _indexes_initialized = False

