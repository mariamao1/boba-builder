"""Small protocol edge cases in the Task 1 ordering client."""

from __future__ import annotations

import io
import unittest
import urllib.error
from unittest import mock

from scripts import kft_api


class Response:
    def __init__(self, body=b""):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.body


class ApiClientTests(unittest.TestCase):
    def test_empty_success_body_is_valid_for_quote(self):
        with mock.patch.object(kft_api.urllib.request, "urlopen",
                               return_value=Response(b"")):
            self.assertEqual(kft_api.quote_order("order", "token"), {})

    def test_http_error_exposes_the_store_message_without_the_request_url(self):
        error = urllib.error.HTTPError(
            "https://example.invalid/?access_token=secret", 400, "Bad Request", {},
            io.BytesIO(b'{"error":{"display_message":"This drink sold out"}}'),
        )
        with mock.patch.object(kft_api.urllib.request, "urlopen", side_effect=error):
            with self.assertRaises(kft_api.ApiError) as caught:
                kft_api.add_item("order", "secret", "item")
        self.assertEqual(str(caught.exception), "This drink sold out")
        self.assertEqual(caught.exception.status, 400)
        self.assertNotIn("secret", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
