import sys
import types
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _FakeEmails:
    sent = []

    @classmethod
    def send(cls, payload):
        cls.sent.append(payload)


class _FakeLimiter:
    def __init__(self, *args, **kwargs):
        pass

    def limit(self, *_args, **_kwargs):
        def decorator(fn):
            return fn

        return decorator


sys.modules.setdefault(
    "resend",
    types.SimpleNamespace(api_key=None, Emails=_FakeEmails),
)
sys.modules.setdefault(
    "stripe",
    types.SimpleNamespace(
        api_key=None,
        Webhook=types.SimpleNamespace(construct_event=lambda payload, sig, secret: {}),
        checkout=types.SimpleNamespace(Session=types.SimpleNamespace(create=lambda **_: None)),
        Coupon=types.SimpleNamespace(create=lambda **_: types.SimpleNamespace(id="coupon_test")),
        Subscription=types.SimpleNamespace(cancel=lambda *_: None),
    ),
)
sys.modules.setdefault(
    "flask_limiter",
    types.SimpleNamespace(Limiter=_FakeLimiter),
)
sys.modules.setdefault(
    "flask_limiter.util",
    types.SimpleNamespace(get_remote_address=lambda: "127.0.0.1"),
)

import app as backend_app


class _FakeResult:
    def __init__(self, value=None, values=None):
        self._value = value
        self._values = list(values or [])

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return self

    def all(self):
        return self._values


class _FakeSession:
    def __init__(self, execute_results=None):
        self.execute_results = list(execute_results or [])
        self.added = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def execute(self, _statement):
        if not self.execute_results:
            return _FakeResult()
        result = self.execute_results.pop(0)
        if isinstance(result, _FakeResult):
            return result
        return _FakeResult(result)

    def add(self, row):
        self.added.append(row)

    def flush(self):
        for row in self.added:
            if row.__class__.__name__ == "User" and getattr(row, "id", None) is None:
                row.id = uuid.uuid4()

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


class _Trial:
    def __init__(self, *, email="trial@example.com", trial_end=None, converted=False):
        self.email = email
        self.trial_end = trial_end or (datetime.now(timezone.utc) + timedelta(days=7))
        self.converted = converted
        self.license_key_hash = "trial_hash"


class BackendApiTests(unittest.TestCase):
    def setUp(self):
        _FakeEmails.sent.clear()
        self.app = backend_app.create_app()
        self.fake_session = _FakeSession()
        self.app.config["db_session"] = lambda: self.fake_session
        self.client = self.app.test_client()

        self._old_monthly = dict(backend_app.STRIPE_PRICE_MAP)
        self._old_annual = dict(backend_app.STRIPE_PRICE_MAP_ANNUAL)
        backend_app.STRIPE_PRICE_MAP.update(
            {"solo": "price_solo_m", "pro": "price_pro_m", "team": "price_team_m"}
        )
        backend_app.STRIPE_PRICE_MAP_ANNUAL.update(
            {"solo": "price_solo_y", "pro": "price_pro_y", "team": "price_team_y"}
        )

    def tearDown(self):
        backend_app.STRIPE_PRICE_MAP.clear()
        backend_app.STRIPE_PRICE_MAP.update(self._old_monthly)
        backend_app.STRIPE_PRICE_MAP_ANNUAL.clear()
        backend_app.STRIPE_PRICE_MAP_ANNUAL.update(self._old_annual)

    def test_admin_auth_rejects_when_secret_is_missing(self):
        with patch.dict(backend_app.os.environ, {}, clear=True):
            response = self.client.get("/api/admin/affiliates")

        self.assertEqual(response.status_code, 401)

    def test_rate_limit_storage_prefers_explicit_uri_then_redis_then_memory(self):
        with patch.dict(backend_app.os.environ, {"RATE_LIMIT_STORAGE_URI": "redis://explicit"}, clear=True):
            self.assertEqual(backend_app._rate_limit_storage_uri(), "redis://explicit")

        with patch.dict(backend_app.os.environ, {"REDIS_URL": "redis://shared"}, clear=True):
            self.assertEqual(backend_app._rate_limit_storage_uri(), "redis://shared")

        with patch.dict(backend_app.os.environ, {}, clear=True):
            self.assertEqual(backend_app._rate_limit_storage_uri(), "memory://")

    def test_auto_init_db_is_opt_in(self):
        with patch.dict(backend_app.os.environ, {}, clear=True):
            self.assertFalse(backend_app._auto_init_db_enabled())

        with patch.dict(backend_app.os.environ, {"AUTO_INIT_DB": "1"}, clear=True):
            self.assertTrue(backend_app._auto_init_db_enabled())

    def test_admin_auth_rejects_missing_header(self):
        with patch.dict(backend_app.os.environ, {"ADMIN_SECRET": "secret"}, clear=True):
            response = self.client.get("/api/admin/affiliates")

        self.assertEqual(response.status_code, 401)

    def test_admin_auth_accepts_matching_header(self):
        self.fake_session.execute_results = [_FakeResult(values=[])]
        with patch.dict(backend_app.os.environ, {"ADMIN_SECRET": "secret"}, clear=True):
            response = self.client.get(
                "/api/admin/affiliates",
                headers={"X-Admin-Secret": "secret"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), [])

    def test_validate_license_calls_license_validator_and_commits(self):
        with patch.dict(backend_app.os.environ, {"LICENSE_SALT": "salt"}, clear=True):
            with patch.object(backend_app, "validate_license") as validate_license:
                validate_license.return_value = {
                    "valid": True,
                    "tier": "pro",
                    "is_trial": False,
                    "expires": None,
                    "error": None,
                }
                response = self.client.post(
                    "/api/validate-license",
                    json={"license_key": "AAAA-BBBB-CCCC-DDDD"},
                )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["valid"])
        self.assertEqual(self.fake_session.commits, 1)
        self.assertTrue(self.fake_session.closed)

    def test_validate_license_requires_license_key(self):
        response = self.client.post("/api/validate-license", json={})

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()["valid"])

    def test_start_trial_creates_trial_license_and_emails_key(self):
        self.fake_session.execute_results = [None, None]

        with patch.dict(backend_app.os.environ, {"LICENSE_SALT": "salt"}, clear=True):
            with patch("license.generate_license_key", return_value="TEST-TRIAL-KEY"):
                with patch("license.hash_license_key", return_value="hashed_trial_key"):
                    response = self.client.post(
                        "/api/start-trial",
                        json={"email": "Trial@Example.com", "tier": "solo"},
                    )

        body = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(body["success"])
        self.assertEqual(body["license_key"], "TEST-TRIAL-KEY")
        self.assertGreaterEqual(self.fake_session.commits, 2)
        self.assertTrue(self.fake_session.closed)
        self.assertEqual(_FakeEmails.sent[-1]["to"], ["trial@example.com"])

    def test_start_trial_rejects_existing_expired_trial(self):
        self.fake_session.execute_results = [
            _Trial(trial_end=datetime.now(timezone.utc) - timedelta(days=1))
        ]

        response = self.client.post(
            "/api/start-trial",
            json={"email": "trial@example.com", "tier": "solo"},
        )

        self.assertEqual(response.status_code, 410)
        self.assertTrue(response.get_json()["trial_expired"])

    def test_convert_trial_rejects_expired_trial(self):
        self.fake_session.execute_results = [
            _Trial(trial_end=datetime.now(timezone.utc) - timedelta(days=1))
        ]

        response = self.client.post(
            "/api/convert-trial",
            json={"email": "trial@example.com", "tier": "pro", "billing": "monthly"},
        )

        self.assertEqual(response.status_code, 410)
        self.assertTrue(response.get_json()["trial_expired"])

    def test_create_checkout_session_uses_expected_price_and_metadata(self):
        created = {}

        def fake_create(**kwargs):
            created.update(kwargs)
            return types.SimpleNamespace(url="https://checkout.example/session")

        with patch.object(backend_app.stripe.checkout.Session, "create", side_effect=fake_create):
            response = self.client.post(
                "/api/create-checkout-session",
                json={"tier": "team", "billing": "annual"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["url"], "https://checkout.example/session")
        self.assertEqual(created["line_items"][0]["price"], "price_team_y")
        self.assertEqual(created["metadata"]["tier"], "team")
        self.assertEqual(created["metadata"]["billing"], "annual")

    def test_create_checkout_session_rejects_invalid_tier(self):
        response = self.client.post(
            "/api/create-checkout-session",
            json={"tier": "enterprise", "billing": "monthly"},
        )

        self.assertEqual(response.status_code, 400)

    def test_stripe_webhook_processes_checkout_completed_event(self):
        event = {
            "type": "checkout.session.completed",
            "data": {"object": {"metadata": {}, "customer_details": {"email": "a@b.com"}}},
        }

        with patch.dict(backend_app.os.environ, {"STRIPE_WEBHOOK_SECRET": "whsec"}, clear=True):
            with patch.object(backend_app.stripe.Webhook, "construct_event", return_value=event):
                with patch.object(backend_app, "_process_checkout_completed") as process:
                    response = self.client.post(
                        "/api/webhook/stripe",
                        data=b"{}",
                        headers={"Stripe-Signature": "sig"},
                    )

        self.assertEqual(response.status_code, 200)
        process.assert_called_once_with(event["data"]["object"])

    def test_stripe_webhook_returns_error_for_missing_secret(self):
        with patch.dict(backend_app.os.environ, {}, clear=True):
            response = self.client.post("/api/webhook/stripe", data=b"{}")

        self.assertEqual(response.status_code, 500)

    def test_stripe_webhook_rejects_invalid_signature(self):
        with patch.dict(backend_app.os.environ, {"STRIPE_WEBHOOK_SECRET": "whsec"}, clear=True):
            with patch.object(
                backend_app.stripe.Webhook,
                "construct_event",
                side_effect=Exception("bad signature"),
            ):
                response = self.client.post(
                    "/api/webhook/stripe",
                    data=b"{}",
                    headers={"Stripe-Signature": "bad"},
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["error"], "Invalid signature")


if __name__ == "__main__":
    unittest.main()
