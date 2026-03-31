"""
Typestra backend API — Flask application entrypoint for Railway (gunicorn app:app).
"""

from __future__ import annotations

import logging
import os
import resend
import stripe
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
        methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
    )

    # Lazy DB init — runs after worker is up, not at import time
    @app.before_request
    def ensure_db():
        if "db_session" not in app.config:
            try:
                engine = create_engine_from_env()
                init_db(engine)
                app.config["db_session"] = sessionmaker(bind=engine)
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

    @app.route("/api/create-checkout-session", methods=["POST"])
    def create_checkout_session():
        try:
            data = request.get_json()
            tier = data.get("tier")
            price_map = {
                "basic": os.environ.get("STRIPE_PRICE_BASIC"),
                "pro": os.environ.get("STRIPE_PRICE_PRO"),
                "premium": os.environ.get("STRIPE_PRICE_PREMIUM"),
            }
            price_id = price_map.get(tier)
            if not price_id:
                return jsonify({"error": "Invalid tier"}), 400

            stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
            session = stripe.checkout.Session.create(
                payment_method_types=["card"],
                line_items=[{"price": price_id, "quantity": 1}],
                mode="subscription",
                metadata={"tier": tier},
                success_url="https://typestra.com/download?session_id={CHECKOUT_SESSION_ID}",
                cancel_url="https://typestra.com/pricing",
            )
            return jsonify({"url": session.url}), 200
        except Exception as e:
            logger.error(f"Checkout session error: {e}")
            return jsonify({"error": "Failed to create checkout session"}), 500

    @app.route("/api/webhook/stripe", methods=["POST"])
    def stripe_webhook():
        payload = request.get_data()
        sig_header = request.headers.get("Stripe-Signature")
        webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET")

        try:
            stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
            event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
        except Exception as e:
            logger.error(f"Webhook signature error: {e}")
            return jsonify({"error": "Invalid signature"}), 400

        if event["type"] == "checkout.session.completed":
            session_data = event["data"]["object"]
            email = session_data.get("customer_details", {}).get("email")
            stripe_customer_id = session_data.get("customer")
            stripe_subscription_id = session_data.get("subscription")
            tier = session_data.get("metadata", {}).get("tier", "basic")

            try:
                from license import create_subscription

                db_session = current_app.config.get("db_session")
                if db_session:
                    s = db_session()
                    try:
                        result = create_subscription(
                            session=s,
                            email=email,
                            tier=tier,
                            stripe_customer_id=stripe_customer_id,
                            stripe_subscription_id=stripe_subscription_id,
                        )
                        s.commit()
                        license_key = result["license_key"]

                        resend.api_key = os.environ.get("RESEND_API_KEY")
                        resend.Emails.send(
                            {
                                "from": "Typestra <noreply@typestra.com>",
                                "to": [email],
                                "subject": "Your Typestra License Key",
                                "html": f"""
                        <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto;">
                            <h2>Welcome to Typestra!</h2>
                            <p>Thank you for your purchase. Here is your license key:</p>
                            <div style="background: #1a1a2e; color: #00f5d4; font-family: monospace; font-size: 24px; padding: 20px; text-align: center; border-radius: 8px; letter-spacing: 4px;">
                                {license_key}
                            </div>
                            <p>To activate:</p>
                            <ol>
                                <li>Download Typestra at <a href="https://typestra.com/download">typestra.com/download</a></li>
                                <li>Open the app and click Activate</li>
                                <li>Enter your license key</li>
                            </ol>
                            <p>Keep this email -- you'll need the key if you reinstall.</p>
                            <p>Questions? Reply to this email or contact support@typestra.com</p>
                        </div>
                        """,
                            }
                        )
                        logger.info(f"License created and emailed to {email}")
                    except Exception as e:
                        s.rollback()
                        logger.error(f"Failed to create subscription or send email: {e}")
                    finally:
                        s.close()
            except Exception as e:
                logger.error(f"Webhook processing error: {e}")

        return jsonify({"status": "ok"}), 200

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
