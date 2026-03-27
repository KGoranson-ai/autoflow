"""
Typestra backend API — Flask application entrypoint for Railway (gunicorn app:app).
"""

from __future__ import annotations

import logging
import os
from dotenv import load_dotenv
from flask import Flask, current_app, jsonify, request
from flask_cors import CORS
from sqlalchemy.orm import sessionmaker

from database import init_db, create_engine_from_env
from license import validate_license

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
        Session = sessionmaker(bind=engine)
        app.config["db_session"] = Session
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

    @app.route("/api/validate-license", methods=["POST"])
    def api_validate_license():
        try:
            data = request.get_json()
            if not data or "license_key" not in data:
                return jsonify({"valid": False, "error": "Missing license_key"}), 400

            license_key = data["license_key"]
            ip_address = request.remote_addr

            Session = current_app.config["db_session"]
            session = Session()

            try:
                salt = os.environ.get("LICENSE_SALT", "")

                result = validate_license(
                    session=session,
                    license_key=license_key,
                    salt=salt,
                    ip_address=ip_address,
                )

                session.commit()
                return jsonify(result), 200

            except Exception as e:
                session.rollback()
                logger.error(f"License validation error: {e}")
                return jsonify({"valid": False, "error": "Validation failed"}), 500
            finally:
                session.close()

        except Exception as e:
            logger.error(f"Request error: {e}")
            return jsonify({"valid": False, "error": "Invalid request"}), 400

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
