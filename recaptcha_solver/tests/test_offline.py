"""オフラインテスト (ブラウザ不要)。"""
import unittest

from recaptcha_solver import (
    RecaptchaSolver,
    normalize_answer,
    validate_audio_url,
)
from recaptcha_solver.audio import AudioError


class TestNormalize(unittest.TestCase):
    def test_words_to_digits(self):
        self.assertEqual(normalize_answer("one two three four"), "1234")

    def test_mixed(self):
        self.assertEqual(normalize_answer("zero 1 2 3"), "0123")


class TestUrlValidation(unittest.TestCase):
    def test_allowlisted(self):
        u = ("https://www.google.com/recaptcha/api2/payload/audio.mp3"
             "?c=abc123")
        self.assertTrue(validate_audio_url(u).startswith("https://"))

    def test_rejects_evil_host(self):
        with self.assertRaises(AudioError):
            validate_audio_url("https://evil.example/recaptcha/api2/payload")

    def test_rejects_http(self):
        with self.assertRaises(AudioError):
            validate_audio_url("http://www.google.com/recaptcha/api2/payload")


class TestImports(unittest.TestCase):
    def test_solver_construct(self):
        s = RecaptchaSolver(headless=True, timeout=10)
        self.assertEqual(s.audio_rounds, 5)


if __name__ == "__main__":
    unittest.main()
