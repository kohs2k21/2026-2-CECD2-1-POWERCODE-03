"""검증된 GPU 생성 서비스에 집계 입력만 전달한다."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request


class GenerationUnavailable(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def generate(kind: str, payload: dict) -> dict:
    url = os.environ.get("DESIGN_MODEL_URL", "")
    token = os.environ.get("DESIGN_MODEL_TOKEN", "")
    parsed = urllib.parse.urlsplit(url)
    if kind not in {"feature", "rule"} or not token or not (
        parsed.scheme == "https" or parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}
    ) or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise GenerationUnavailable("generation_service_not_configured")
    body = json.dumps({"kind": kind, "input": payload}, ensure_ascii=False, allow_nan=False).encode()
    if len(body) > 256000:
        raise ValueError("generation_input_too_large")
    request = urllib.request.Request(url.rstrip("/") + "/generate", data=body,
                                    headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=300) as response:
            raw = response.read(256001)
        if len(raw) > 256000:
            raise ValueError("generation_response_too_large")
        result = json.loads(raw)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise GenerationUnavailable("generation_service_unavailable") from exc
    if not isinstance(result, dict) or result.get("truncated") is not False or not isinstance(result.get("output"), dict):
        raise ValueError("invalid_generation_response")
    return result
