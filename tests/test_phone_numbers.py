import unittest

from consultant_dashboard.core.phone_numbers import normalize_phone


class PhoneNumberNormalizationTest(unittest.TestCase):
    def test_uk_number_allows_optional_trunk_zero_after_country_code(self):
        self.assertEqual(normalize_phone("+44 (0) 7712886300", "UK"), "+447712886300")

    def test_us_number_uses_plus_one_country_code(self):
        self.assertEqual(normalize_phone("4155551212", "US"), "+14155551212")
