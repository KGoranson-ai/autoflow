"""
Typestra backend API — Flask application entrypoint for Railway (gunicorn app:app).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS

from database import init_db, create_engine_from_env

load_dotenv()

logger = logging.getLogger(__name__)

DEFAULT_DOWNLOAD_URL = os.environ.get(
    "DOWNLOAD_URL",
    "https://typestra.com/download",
)


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-change-me")
    app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024  # 1 MiB

    cors_origins = _parse_cors_origins(
        os.environ.get(
            "CORS_ORIGINS",
            "https://typestra.com,https://www.typestra.com,http://localhost:3000,http://127.0.0.1:3000",
        )
    )
    CORS(
        app,
        resources={r"/api/*": {"origins": cors_origins}},
        supports_credentials=True,
    )

    # Initialize database
    try:
        engine = create_engine_from_env()
        init_db(engine)
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")

    @app.get("/health")
    def health() -> tuple[dict[str, str], int]:
        return {"status": "ok"}, 200

    @app.get("/api/version")
    def api_version() -> tuple[dict[str, str], int]:
        """Return Typestra client version and download URL."""
        return {
            "version": "3.0.0",
            "download_url": DEFAULT_DOWNLOAD_URL,
        }, 200

    @app.post("/api/validate-license")
    def validate_license() -> tuple[dict[str, Any], int]:
        if not request.is_json:
            return {"error": "Expected application/json"}, 400
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return {"error": "Invalid JSON body"}, 400
        key = data.get("license_key")
        if key is None or not isinstance(key, str) or not key.strip():
            return {"error": "license_key is required"}, 400
        # Stub: real validation will check DB / Stripe / signed tokens
        _ = os.environ.get("LICENSE_SALT", "")
        return {
            "valid": False,
            "message": "License validation not configured (stub)",
        }, 200

    @app.errorhandler(404)
    def not_found(_e: Exception) -> tuple[dict[str, str], int]:
        return {"error": "Not found"}, 404

    @app.errorhandler(413)
    def payload_too_large(_e: Exception) -> tuple[dict[str, str], int]:
        return {"error": "Payload too large"}, 413

    @app.errorhandler(500)
    def server_error(_e: Exception) -> tuple[dict[str, str], int]:
        logger.exception("Unhandled server error")
        return {"error": "Internal server error"}, 500

    return app


def _parse_cors_origins(raw: str) -> list[str]:
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return parts or ["https://typestra.com"]


app = create_app()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    port = int(os.environ.get("PORT", "5001"))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
