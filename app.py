"""
Typestra backend API — Flask application entrypoint for Railway (gunicorn app:app).
"""

from __future__ import annotations

import logging
import os
import secrets
import resend
import stripe
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from flask import Flask, current_app, jsonify, request
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from sqlalchemy.orm import sessionmaker

from database import Affiliate, Referral, TrialRequest, init_db, create_engine_from_env
from license import validate_license

load_dotenv()

logger = logging.getLogger(__name__)

DEFAULT_DOWNLOAD_URL = os.environ.get(
    "DOWNLOAD_URL",
    "https://typestra.com/download",
)

# Map frontend tier IDs to Stripe price IDs (monthly)
STRIPE_PRICE_MAP = {
    "solo": os.environ.get("STRIPE_PRICE_SOLO"),
    "pro": os.environ.get("STRIPE_PRICE_PRO"),
    "team": os.environ.get("STRIPE_PRICE_TEAM"),
}

# Map frontend tier IDs to DB/PG enum values
TIER_MAP = {
    "solo": "basic",
    "pro": "pro",
    "team": "premium",
}

# Map frontend tier IDs to Stripe price IDs (annual)
STRIPE_PRICE_MAP_ANNUAL = {
    "solo": os.environ.get("STRIPE_PRICE_SOLO_ANNUAL"),
    "pro": os.environ.get("STRIPE_PRICE_PRO_ANNUAL"),
    "team": os.environ.get("STRIPE_PRICE_TEAM_ANNUAL"),
}

# Trial duration in days
TRIAL_DAYS = 14


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-change-me")
    app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024  # 1 MiB

    # Initialize rate limiter (in-memory, no Redis)
    limiter = Limiter(
        app=app,
        key_fn=get_remote_address,
        default_limits=["200 per day"],
        storage_uri="memory://",
    )

    raw_cors_origins = os.environ.get(
        "CORS_ORIGINS",
        "https://typestra.com,https://www.typestra.com,http://localhost:3000,http://127.0.0.1:3000",
    )
    cors_origins = _parse_cors_origins(raw_cors_origins)

    # Security: reject wildcard CORS_ORIGINS when credentials are enabled
    if "*" in cors_origins:
        if cors_origins == ["*"]:
            logger.warning(
                "CORS_ORIGINS=* is set. This allows any origin to make credentialed requests, "
                "which is unsafe. Set a specific list of allowed origins instead."
            )
        else:
            logger.warning(
                "CORS_ORIGINS contains '*' alongside other origins. "
                "Wildcard with credentials is unsafe — consider removing '*'."
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
            billing = data.get("billing", "monthly")  # "monthly" or "annual"
            ref_code = data.get("ref_code")

            price_map = STRIPE_PRICE_MAP_ANNUAL if billing == "annual" else STRIPE_PRICE_MAP
            price_id = price_map.get(tier)
            if not price_id:
                return jsonify({"error": "Invalid tier or billing cycle"}), 400

            discount_percent = 0
            if ref_code:
                try:
                    from sqlalchemy import select
                    db_session = current_app.config.get("db_session")
                    if db_session:
                        s = db_session()
                        try:
                            affiliate = s.execute(
                                select(Affiliate).where(
                                    Affiliate.ref_code == ref_code,
                                    Affiliate.status == "active"
                                )
                            ).scalar_one_or_none()
                            if affiliate:
                                discount_percent = affiliate.discount_percent
                        finally:
                            s.close()
                except Exception as e:
                    logger.error(f"Ref code lookup error: {e}")

            discounts = []
            if discount_percent > 0:
                stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
                coupon = stripe.Coupon.create(
                    percent_off=discount_percent,
                    duration="once",
                )
                discounts = [{"coupon": coupon.id}]

            stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
            session = stripe.checkout.Session.create(
                payment_method_types=["card"],
                line_items=[{"price": price_id, "quantity": 1}],
                mode="subscription",
                metadata={"tier": tier, "billing": billing, "ref_code": ref_code or ""},
                discounts=discounts if discounts else None,
                success_url="https://typestra.com/download?session_id={CHECKOUT_SESSION_ID}",
                cancel_url="https://typestra.com/pricing",
            )
            return jsonify({"url": session.url}), 200
        except Exception as e:
            logger.error(f"Checkout session error: {e}")
            return jsonify({"error": "Failed to create checkout session"}), 500

    @app.route("/api/start-trial", methods=["POST"])
    @limiter.limit("5 per minute")
    def start_trial():
        """Start a 7-day free trial. Creates a license key valid for 7 days and emails it."""
        try:
            data = request.get_json()
            email = (data.get("email") or "").strip().lower()
            tier = data.get("tier", "solo")

            if not email or "@" not in email:
                return jsonify({"error": "A valid email address is required"}), 400

            if tier not in STRIPE_PRICE_MAP:
                return jsonify({"error": "Invalid tier. Must be solo, pro, or team."}), 400

            Session = current_app.config.get("db_session")
            if not Session:
                return jsonify({"error": "Service unavailable"}), 503

            s = Session()
            try:
                from sqlalchemy import select
                from license import generate_license_key, hash_license_key

                # Check for existing trial by this email
                existing_trial = s.execute(
                    select(TrialRequest)
                    .where(TrialRequest.email == email, TrialRequest.converted == False)
                ).scalar_one_or_none()

                if existing_trial:
                    # Check if trial is still active
                    if existing_trial.trial_end > datetime.now(timezone.utc):
                        return jsonify({
                            "error": "You already have an active trial. Check your email for your license key."
                        }), 409
                    # Trial expired and not converted — allow re-trial or prompt convert
                    return jsonify({
                        "error": "Your trial has ended. Subscribe to continue using Typestra.",
                        "trial_expired": True,
                    }), 410

                # Generate trial license
                salt = os.environ.get("LICENSE_SALT", "")
                trial_key = generate_license_key()
                key_hash = hash_license_key(trial_key, salt)
                trial_end = datetime.now(timezone.utc) + timedelta(days=TRIAL_DAYS)

                trial_request = TrialRequest(
                    email=email,
                    tier=TIER_MAP[tier],
                    license_key_hash=key_hash,
                    trial_end=trial_end,
                    converted=False,
                )
                s.add(trial_request)
                s.commit()

                # Also create a subscriptions row so validate-license can find it
                from database import Subscription
                from database import User
                user = s.execute(select(User).where(User.email == email)).scalar_one_or_none()
                if user is None:
                    user = User(email=email)
                    s.add(user)
                    s.flush()
                trial_sub = Subscription(
                    user_id=user.id,
                    license_key_hash=key_hash,
                    tier=TIER_MAP[tier],
                    stripe_customer_id=None,
                    stripe_subscription_id=None,
                    status="active",
                    is_trial=True,
                    trial_end=trial_end,
                    converted_from_trial=False,
                    current_period_end=trial_end,
                )
                s.add(trial_sub)
                s.commit()

                # Email the trial license key
                resend.api_key = os.environ.get("RESEND_API_KEY")
                resend.Emails.send({
                    "from": "Typestra <noreply@typestra.com>",
                    "to": [email],
                    "subject": "Your Typestra Free Trial — 14 Days Free",
                    "html": f"""
                    <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto;">
                        <h2>Start your Typestra trial 🎉</h2>
                        <p>Your 14-day free trial is active. Here's your license key:</p>
                        <div style="background: #1a1a2e; color: #00f5d4; font-family: monospace; font-size: 22px; padding: 20px; text-align: center; border-radius: 8px; letter-spacing: 4px; margin: 20px 0;">
                            {trial_key}
                        </div>
                        <p><strong>Your trial ends:</strong> {trial_end.strftime('%B %d, %Y')}</p>
                        <p>To activate:</p>
                        <ol>
                            <li>Download Typestra at <a href="https://typestra.com/download">typestra.com/download</a></li>
                            <li>Open the app and click Activate</li>
                            <li>Enter your license key</li>
                        </ol>
                        <p>After your trial, you can subscribe starting at $19/month. We'll remind you before your trial ends.</p>
                        <p>Questions? Reply to this email or contact support@typestra.com</p>
                    </div>
                    """,
                })

                logger.info(f"Trial started for {email}, tier={tier}, expires={trial_end.date()}")
                return jsonify({
                    "success": True,
                    "message": "Trial started",
                    "trial_end": trial_end.isoformat(),
                    "license_key": trial_key,
                }), 200

            except Exception as e:
                s.rollback()
                logger.error(f"Start trial error: {e}")
                return jsonify({"error": "Failed to start trial. Please try again."}), 500
            finally:
                s.close()

        except Exception as e:
            logger.error(f"Start trial request error: {e}")
            return jsonify({"error": "Invalid request"}), 400

    @app.route("/api/convert-trial", methods=["POST"])
    def convert_trial():
        """Convert an existing trial to a paid subscription. Runs Stripe checkout with the trial's license key pre-filled."""
        try:
            data = request.get_json()
            email = (data.get("email") or "").strip().lower()
            tier = data.get("tier", "solo")
            billing = data.get("billing", "monthly")

            if not email or "@" not in email:
                return jsonify({"error": "A valid email address is required"}), 400

            if tier not in STRIPE_PRICE_MAP:
                return jsonify({"error": "Invalid tier"}), 400

            Session = current_app.config.get("db_session")
            if not Session:
                return jsonify({"error": "Service unavailable"}), 503

            s = Session()
            try:
                from sqlalchemy import select

                # Verify trial exists and isn't already converted
                trial = s.execute(
                    select(TrialRequest).where(
                        TrialRequest.email == email,
                        TrialRequest.converted == False,
                    )
                ).scalar_one_or_none()

                if not trial:
                    # No trial found — redirect to normal checkout
                    return jsonify({"error": "No active trial found for this email"}), 404

                if trial.trial_end <= datetime.now(timezone.utc):
                    return jsonify({
                        "error": "Your trial has expired. Please subscribe to continue.",
                        "trial_expired": True,
                    }), 410

                # Create Stripe checkout session for the paid conversion
                price_map = STRIPE_PRICE_MAP_ANNUAL if billing == "annual" else STRIPE_PRICE_MAP
                price_id = price_map.get(tier)
                if not price_id:
                    return jsonify({"error": "Invalid billing cycle"}), 400

                stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
                checkout_session = stripe.checkout.Session.create(
                    payment_method_types=["card"],
                    line_items=[{"price": price_id, "quantity": 1}],
                    mode="subscription",
                    metadata={
                        "tier": tier,
                        "billing": billing,
                        "trial_email": email,
                        "ref_code": "",
                    },
                    success_url="https://typestra.com/download?session_id={CHECKOUT_SESSION_ID}&converted_trial=1",
                    cancel_url="https://typestra.com/pricing",
                )

                logger.info(f"Trial conversion checkout created for {email}")
                return jsonify({"url": checkout_session.url}), 200

            except Exception as e:
                logger.error(f"Convert trial error: {e}")
                return jsonify({"error": "Failed to start conversion. Please try again."}), 500
            finally:
                s.close()

        except Exception as e:
            logger.error(f"Convert trial request error: {e}")
            return jsonify({"error": "Invalid request"}), 400

    @app.route("/api/webhook/stripe", methods=["POST"])
    def stripe_webhook():
        payload = request.get_data()
        sig_header = request.headers.get("Stripe-Signature")
        webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET")

        if not webhook_secret:
            logger.error("STRIPE_WEBHOOK_SECRET is not set — cannot verify webhook")
            return jsonify({"error": "Webhook not configured"}), 500

        try:
            stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
            event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
        except ValueError as e:
            logger.warning(f"Stripe webhook payload error: {e}")
            return jsonify({"error": "Invalid payload"}), 200
        except Exception as e:
            logger.warning(f"Stripe webhook signature error: {e}")
            return jsonify({"error": "Invalid signature"}), 200

        if event["type"] == "checkout.session.completed":
            session_data = event["data"]["object"]
            email = session_data.get("customer_details", {}).get("email")
            stripe_customer_id = session_data.get("customer")
            stripe_subscription_id = session_data.get("subscription")
            tier = session_data.get("metadata", {}).get("tier", "basic")
            ref_code = session_data.get("metadata", {}).get("ref_code")
            amount_total = session_data.get("amount_total", 0)

            try:
                from license import create_subscription
                from sqlalchemy import select
                db_session = current_app.config.get("db_session")
                if db_session:
                    s = db_session()
                    try:
                        result = create_subscription(
                            session=s,
                            email=email,
                            tier=TIER_MAP.get(tier, tier),
                            stripe_customer_id=stripe_customer_id,
                            stripe_subscription_id=stripe_subscription_id,
                        )
                        s.commit()
                        license_key = result["license_key"]

                        # Handle referral tracking
                        if ref_code:
                            affiliate = s.execute(
                                select(Affiliate).where(
                                    Affiliate.ref_code == ref_code,
                                    Affiliate.status == "active"
                                )
                            ).scalar_one_or_none()

                            if affiliate:
                                is_self_referral = affiliate.email.lower() == (email or "").lower()
                                referral = Referral(
                                    affiliate_id=affiliate.id,
                                    customer_email=email,
                                    stripe_subscription_id=stripe_subscription_id,
                                    stripe_customer_id=stripe_customer_id,
                                    commission_percent=affiliate.commission_percent,
                                    discount_percent=affiliate.discount_percent,
                                    monthly_amount=amount_total,
                                    commission_ends_at=datetime.now(timezone.utc) + timedelta(days=180),
                                    self_referral_attempt=is_self_referral,
                                )
                                s.add(referral)
                                s.commit()
                                if is_self_referral:
                                    logger.warning(f"Self-referral attempt blocked: {email} used own ref code {ref_code}")
                                else:
                                    logger.info(f"Referral tracked: {email} via {ref_code}")

                        resend.api_key = os.environ.get("RESEND_API_KEY")
                        resend.Emails.send({
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
                                <p>Keep this email — you'll need the key if you reinstall.</p>
                                <p>Questions? Reply to this email or contact support@typestra.com</p>
                            </div>
                            """
                        })
                        logger.info(f"License created and emailed to {email}")
                    except Exception as e:
                        s.rollback()
                        logger.error(f"Failed to create subscription or send email: {e}")
                    finally:
                        s.close()
            except Exception as e:
                logger.error(f"Webhook processing error: {e}")

        return jsonify({"status": "ok"}), 200

    @app.route("/api/admin/cancel-license", methods=["POST"])
    def admin_cancel_license():
        admin_secret = request.headers.get("X-Admin-Secret")
        if admin_secret != os.environ.get("ADMIN_SECRET"):
            return jsonify({"error": "Unauthorized"}), 401

        data = request.get_json()
        license_key = data.get("license_key")
        if not license_key:
            return jsonify({"error": "Missing license_key"}), 400

        try:
            from license import hash_license_key

            salt = os.environ.get("LICENSE_SALT", "")
            key_hash = hash_license_key(license_key, salt)

            db_session = current_app.config.get("db_session")
            s = db_session()
            try:
                from sqlalchemy import select
                from database import Subscription

                sub = s.execute(
                    select(Subscription).where(Subscription.license_key_hash == key_hash)
                ).scalar_one_or_none()

                if not sub:
                    return jsonify({"error": "License not found"}), 404

                sub.status = "cancelled"

                if sub.stripe_subscription_id:
                    stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
                    stripe.Subscription.cancel(sub.stripe_subscription_id)

                s.commit()
                logger.info(f"License cancelled: {key_hash[:8]}...")
                return jsonify({"status": "cancelled"}), 200
            except Exception as e:
                s.rollback()
                logger.error(f"Cancel error: {e}")
                return jsonify({"error": "Failed to cancel"}), 500
            finally:
                s.close()
        except Exception as e:
            logger.error(f"Cancel request error: {e}")
            return jsonify({"error": "Invalid request"}), 400

    @app.route("/api/affiliate/apply", methods=["POST"])
    def affiliate_apply():
        try:
            data = request.get_json()
            name = data.get("name", "").strip()
            email = data.get("email", "").strip().lower()
            notes = data.get("notes", "").strip()

            if not name or not email or "@" not in email:
                return jsonify({"error": "Name and valid email are required"}), 400

            db_session = current_app.config.get("db_session")
            if not db_session:
                return jsonify({"error": "Service unavailable"}), 503
            s = db_session()
            try:
                from sqlalchemy import select

                existing = s.execute(
                    select(Affiliate).where(Affiliate.email == email)
                ).scalar_one_or_none()
                if existing:
                    return (
                        jsonify({"error": "An application with this email already exists"}),
                        409,
                    )

                ref_code = secrets.token_urlsafe(6)[:8].upper()
                affiliate = Affiliate(
                    name=name,
                    email=email,
                    ref_code=ref_code,
                    status="pending",
                    notes=notes,
                )
                s.add(affiliate)
                s.commit()

                resend.api_key = os.environ.get("RESEND_API_KEY")
                resend.Emails.send(
                    {
                        "from": "Typestra <noreply@typestra.com>",
                        "to": [email],
                        "subject": "Typestra Affiliate Application Received",
                        "html": f"""
                <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto;">
                    <h2>Thanks for applying, {name}!</h2>
                    <p>We've received your affiliate application for Typestra. We review applications manually and will get back to you within 2-3 business days.</p>
                    <p>Questions? Reply to this email or contact support@typestra.com.</p>
                </div>
                """,
                    }
                )

                return jsonify({"status": "applied"}), 200
            except Exception as e:
                s.rollback()
                logger.error(f"Affiliate apply error: {e}")
                return jsonify({"error": "Application failed"}), 500
            finally:
                s.close()
        except Exception as e:
            logger.error(f"Affiliate apply request error: {e}")
            return jsonify({"error": "Invalid request"}), 400

    @app.route("/api/admin/affiliate/approve", methods=["POST"])
    def admin_affiliate_approve():
        admin_secret = request.headers.get("X-Admin-Secret")
        if admin_secret != os.environ.get("ADMIN_SECRET"):
            return jsonify({"error": "Unauthorized"}), 401

        data = request.get_json()
        email = data.get("email", "").strip().lower()
        discount_percent = data.get("discount_percent", 10)
        commission_percent = data.get("commission_percent", 50)
        payout_email = data.get("payout_email", email)

        if not email:
            return jsonify({"error": "Email required"}), 400

        db_session = current_app.config.get("db_session")
        s = db_session()
        try:
            from sqlalchemy import select

            affiliate = s.execute(
                select(Affiliate).where(Affiliate.email == email)
            ).scalar_one_or_none()
            if not affiliate:
                return jsonify({"error": "Affiliate not found"}), 404

            affiliate.status = "active"
            affiliate.discount_percent = discount_percent
            affiliate.commission_percent = commission_percent
            affiliate.payout_email = payout_email
            s.commit()

            resend.api_key = os.environ.get("RESEND_API_KEY")
            resend.Emails.send(
                {
                    "from": "Typestra <noreply@typestra.com>",
                    "to": [affiliate.email],
                    "subject": "You're approved — Typestra Affiliate Program",
                    "html": f"""
            <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto;">
                <h2>Welcome to the Typestra Affiliate Program, {affiliate.name}!</h2>
                <p>Your application has been approved. Here are your details:</p>
                <ul>
                    <li><strong>Your referral link:</strong> https://typestra.com/?ref={affiliate.ref_code}</li>
                    <li><strong>Customer discount:</strong> {affiliate.discount_percent}% off</li>
                    <li><strong>Your commission:</strong> {affiliate.commission_percent}% recurring for 6 months per sale</li>
                </ul>
                <p>Share your link with your audience. When someone clicks it and subscribes, they get {affiliate.discount_percent}% off and you earn {affiliate.commission_percent}% of their monthly payment for 6 months.</p>
                <p><strong>Important:</strong> Using your own referral link to purchase a subscription is not permitted and will result in termination from the program.</p>
                <p>Questions? Email support@typestra.com.</p>
            </div>
            """,
                }
            )

            return (
                jsonify(
                    {
                        "status": "approved",
                        "ref_code": affiliate.ref_code,
                        "referral_link": f"https://typestra.com/?ref={affiliate.ref_code}",
                        "discount_percent": affiliate.discount_percent,
                        "commission_percent": affiliate.commission_percent,
                    }
                ),
                200,
            )
        except Exception as e:
            s.rollback()
            logger.error(f"Affiliate approve error: {e}")
            return jsonify({"error": "Approval failed"}), 500
        finally:
            s.close()

    @app.route("/api/admin/affiliates", methods=["GET"])
    def admin_affiliates():
        admin_secret = request.headers.get("X-Admin-Secret")
        if admin_secret != os.environ.get("ADMIN_SECRET"):
            return jsonify({"error": "Unauthorized"}), 401

        db_session = current_app.config.get("db_session")
        s = db_session()
        try:
            from sqlalchemy import select

            affiliates = s.execute(select(Affiliate)).scalars().all()
            result = []
            for a in affiliates:
                referrals = s.execute(
                    select(Referral).where(Referral.affiliate_id == a.id)
                ).scalars().all()
                active_referrals = [
                    r
                    for r in referrals
                    if r.commission_ends_at > datetime.now(timezone.utc)
                ]
                total_commission = sum(
                    (r.monthly_amount * r.commission_percent / 100) for r in active_referrals
                )
                result.append(
                    {
                        "id": str(a.id),
                        "name": a.name,
                        "email": a.email,
                        "ref_code": a.ref_code,
                        "referral_link": f"https://typestra.com/?ref={a.ref_code}",
                        "status": a.status,
                        "discount_percent": a.discount_percent,
                        "commission_percent": a.commission_percent,
                        "payout_email": a.payout_email,
                        "total_referrals": len(referrals),
                        "active_referrals": len(active_referrals),
                        "monthly_commission_owed": round(total_commission / 100, 2),
                        "created_at": a.created_at.isoformat(),
                    }
                )
            return jsonify(result), 200
        except Exception as e:
            logger.error(f"Affiliates list error: {e}")
            return jsonify({"error": "Failed to fetch affiliates"}), 500
        finally:
            s.close()

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
