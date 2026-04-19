import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _FakeEmails:
    sent = []

    @classmethod
    def send(cls, payload):
        cls.sent.append(payload)


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


class _FakeLimiter:
    def __init__(self, *args, **kwargs):
        pass

    def limit(self, *_args, **_kwargs):
        def decorator(fn):
            return fn

        return decorator


sys.modules.setdefault(
    "flask_limiter",
    types.SimpleNamespace(Limiter=_FakeLimiter),
)
sys.modules.setdefault(
    "flask_limiter.util",
    types.SimpleNamespace(get_remote_address=lambda: "127.0.0.1"),
)

import app as webhook_app


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    def __init__(self, execute_results=None):
        self.execute_results = list(execute_results or [])
        self.commits = 0
        self.rollbacks = 0
        self.closed = False
        self.added = []

    def execute(self, _statement):
        if not self.execute_results:
            return _FakeResult(None)
        return _FakeResult(self.execute_results.pop(0))

    def add(self, row):
        self.added.append(row)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


class _Trial:
    def __init__(self):
        self.email = "trial@example.com"
        self.license_key_hash = "trial_hash"
        self.converted = False


class _Subscription:
    def __init__(self):
        self.license_key_hash = "trial_hash"
        self.tier = "solo"
        self.status = "active"
        self.is_trial = True
        self.converted_from_trial = False
        self.stripe_customer_id = None
        self.stripe_subscription_id = None
        self.current_period_end = "trial_end"


class StripeWebhookTests(unittest.TestCase):
    def setUp(self):
        _FakeEmails.sent.clear()
        self.flask_app = webhook_app.create_app()
        webhook_app.resend.Emails = _FakeEmails

    def _session_data(self, *, tier, billing="monthly", email="buyer@example.com", trial_email=""):
        return {
            "customer_details": {"email": email},
            "customer": "cus_test",
            "subscription": "sub_test",
            "amount_total": 1900,
            "metadata": {
                "tier": tier,
                "billing": billing,
                "trial_email": trial_email,
                "ref_code": "",
            },
        }

    def test_checkout_completed_creates_license_for_all_tiers_and_billing_cycles(self):
        cases = [
            ("solo", "monthly"),
            ("solo", "annual"),
            ("pro", "monthly"),
            ("pro", "annual"),
            ("team", "monthly"),
            ("team", "annual"),
        ]

        for tier, billing in cases:
            with self.subTest(tier=tier, billing=billing):
                fake_session = _FakeSession()
                self.flask_app.config["db_session"] = lambda: fake_session
                with self.flask_app.app_context():
                    with patch("license.create_subscription") as create_subscription:
                        create_subscription.return_value = {"license_key": "TEST-KEY"}

                        webhook_app._process_checkout_completed(
                            self._session_data(tier=tier, billing=billing)
                        )

                        create_subscription.assert_called_once()
                        self.assertEqual(create_subscription.call_args.kwargs["tier"], tier)
                        self.assertEqual(create_subscription.call_args.kwargs["email"], "buyer@example.com")
                        self.assertEqual(fake_session.commits, 1)
                        self.assertEqual(fake_session.rollbacks, 0)
                        self.assertTrue(fake_session.closed)
                        self.assertEqual(_FakeEmails.sent[-1]["subject"], "Your Typestra License Key")

    def test_checkout_completed_converts_existing_trial(self):
        trial = _Trial()
        subscription = _Subscription()
        fake_session = _FakeSession(execute_results=[trial, subscription])
        self.flask_app.config["db_session"] = lambda: fake_session

        with self.flask_app.app_context():
            with patch("license.create_subscription") as create_subscription:
                webhook_app._process_checkout_completed(
                    self._session_data(
                        tier="team",
                        billing="annual",
                        email="trial@example.com",
                        trial_email="trial@example.com",
                    )
                )

                create_subscription.assert_not_called()

        self.assertTrue(trial.converted)
        self.assertEqual(subscription.tier, "team")
        self.assertFalse(subscription.is_trial)
        self.assertTrue(subscription.converted_from_trial)
        self.assertEqual(subscription.stripe_customer_id, "cus_test")
        self.assertEqual(subscription.stripe_subscription_id, "sub_test")
        self.assertIsNone(subscription.current_period_end)
        self.assertEqual(fake_session.commits, 1)
        self.assertEqual(fake_session.rollbacks, 0)
        self.assertTrue(fake_session.closed)
        self.assertEqual(_FakeEmails.sent[-1]["subject"], "Your Typestra Trial Is Now Active")


if __name__ == "__main__":
    unittest.main()
