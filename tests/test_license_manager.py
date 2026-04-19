import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from license_manager import LicenseInfo, LicenseManager


class LicenseManagerTests(unittest.TestCase):
    def test_get_license_info_returns_invalid_info_instead_of_none_without_key(self):
        manager = LicenseManager()
        manager.get_stored_key = lambda: None

        info = manager.get_license_info()

        self.assertIsInstance(info, LicenseInfo)
        self.assertFalse(info.valid)
        self.assertEqual(info.error, "No license key found")

    def test_get_license_info_uses_trial_expiry_check(self):
        manager = LicenseManager()
        expected = LicenseInfo.invalid("test")
        manager.validate_and_check_trial = lambda: expected

        self.assertIs(manager.get_license_info(), expected)


if __name__ == "__main__":
    unittest.main()
