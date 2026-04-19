import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from license_manager import LicenseInfo
from ocr_capture import FeatureNotAvailableError as OCRFeatureError
from ocr_capture import _require_pro_or_team
from session_manager import FeatureNotAvailableError as MultiFormFeatureError
from session_manager import _require_team


def _license(tier):
    return LicenseInfo(
        valid=True,
        tier=tier,
        expires=None,
        is_trial=False,
        trial_end=None,
        days_remaining=0,
        features=[],
    )


class TierEntitlementTests(unittest.TestCase):
    def test_ocr_allows_pro_and_team(self):
        _require_pro_or_team(_license("pro"))
        _require_pro_or_team(_license("team"))

    def test_ocr_blocks_solo(self):
        with self.assertRaises(OCRFeatureError):
            _require_pro_or_team(_license("solo"))

    def test_multi_form_allows_team_only(self):
        _require_team(_license("team"))

        with self.assertRaises(MultiFormFeatureError):
            _require_team(_license("pro"))

        with self.assertRaises(MultiFormFeatureError):
            _require_team(_license("solo"))


if __name__ == "__main__":
    unittest.main()
