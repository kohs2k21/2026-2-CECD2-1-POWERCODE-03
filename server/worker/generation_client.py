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


_LOCAL_MODEL = None


def set_local_model(model):
    global _LOCAL_MODEL
    _LOCAL_MODEL = model


def generate(kind: str, payload: dict) -> dict:
    if _LOCAL_MODEL is not None:
        return _LOCAL_MODEL.generate(kind, payload)
    url = os.environ.get("DESIGN_MODEL_URL", "")
    token = os.environ.get("DESIGN_MODEL_TOKEN", "")
    parsed = urllib.parse.urlsplit(url)
    # Docker의 내부 서비스 이름은 운영자가 지정한 한 호스트에만 HTTP를 허용한다.
    internal_host = os.environ.get("DESIGN_MODEL_HTTP_HOST", "").strip().lower()
    http_hosts = {"127.0.0.1", "localhost"}
    if internal_host:
        http_hosts.add(internal_host)
    if kind not in {"feature", "rule", "explain"} or not token or not (
        parsed.scheme == "https" or parsed.scheme == "http" and parsed.hostname in http_hosts
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
