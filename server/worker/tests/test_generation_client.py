"""컨테이너 HTTP 예외가 지정한 생성 서비스에만 적용되는지 확인한다."""
import io
import unittest
from unittest.mock import patch

from generation_client import generate, GenerationUnavailable


class GenerationClientTests(unittest.TestCase):
    def test_internal_http_requires_explicit_host(self):
        env = {"DESIGN_MODEL_URL": "http://generation:8010", "DESIGN_MODEL_TOKEN": "x" * 32}
        with patch.dict("os.environ", env, clear=True):
            with self.assertRaises(GenerationUnavailable):
                generate("feature", {})
        env["DESIGN_MODEL_HTTP_HOST"] = "generation"
        with patch.dict("os.environ", env, clear=True), patch("urllib.request.build_opener") as opener:
            opener.return_value.open.return_value = io.BytesIO(b'{"output":{},"truncated":false}')
            self.assertEqual(generate("feature", {})["output"], {})
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, "http://generation:8010/generate")

    def test_internal_exception_rejects_other_hosts_and_credentials(self):
        for url in ("http://other:8010", "http://user:password@generation:8010",
                    "http://generation:8010?token=x", "http://generation:8010#fragment"):
            with self.subTest(url=url), patch.dict("os.environ", {
                    "DESIGN_MODEL_URL": url, "DESIGN_MODEL_HTTP_HOST": "generation",
                    "DESIGN_MODEL_TOKEN": "x" * 32}, clear=True):
                with self.assertRaises(GenerationUnavailable):
                    generate("rule", {})
