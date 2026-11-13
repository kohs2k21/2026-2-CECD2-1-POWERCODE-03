"""검증된 GPU 생성 서비스에 집계 입력만 전달한다."""
from __future__ import annotations

import http.client
import json
import os
import urllib.error
import urllib.parse
import urllib.request


class GenerationUnavailable(RuntimeError):
    pass


class GenerationRejected(GenerationUnavailable):
    """서비스가 이 호출만 거부했다. 기존 작업에는 서비스 장애로 보이고, `code`는 항목 격리에만 쓴다."""

    def __init__(self, code: str = "generation_contract_rejected"):
        super().__init__("generation_service_unavailable")
        self.code = code


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_LOCAL_MODEL = None


def set_local_model(model):
    global _LOCAL_MODEL
    _LOCAL_MODEL = model


def _rejection_code(error: urllib.error.HTTPError) -> str:
    try:
        code = json.loads(error.read(4096)).get("code")
    except (AttributeError, OSError, ValueError, http.client.HTTPException):
        return "generation_contract_rejected"
    return code if isinstance(code, str) else "generation_contract_rejected"


def generate(kind: str, payload: dict, *, report_timeout: bool = False) -> dict:
    """`report_timeout`은 시간 초과를 `generation_timeout`으로 받을 호출만 켠다. 기존 작업은 이전 분류 그대로다."""
    if _LOCAL_MODEL is not None:
        from generation_service import GenerationBusy
        try:
            if not report_timeout:
                return _LOCAL_MODEL.generate(kind, payload)
            try:
                return _LOCAL_MODEL.generate(kind, payload, report_timeout=True)
            except TimeoutError as exc:
                raise GenerationUnavailable("generation_timeout") from exc
        except GenerationBusy as exc:
            raise GenerationUnavailable("generation_busy") from exc
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
    envelope = {"kind": kind, "input": payload, **({"reportTimeout": True} if report_timeout else {})}
    body = json.dumps(envelope, ensure_ascii=False, allow_nan=False).encode()
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
    except urllib.error.HTTPError as exc:
        if exc.code == 422:
            raise GenerationRejected(_rejection_code(exc)) from exc
        if exc.code == 503 and _rejection_code(exc) == "generation_busy":
            # 대화형 대기 상한 초과. 장애와 구분되는 재시도 가능 코드이며 성공으로 바꾸지 않는다.
            raise GenerationUnavailable("generation_busy") from exc
        raise GenerationUnavailable("generation_timeout" if report_timeout and exc.code == 504
                                    else "generation_service_unavailable") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        timed_out = isinstance(exc, TimeoutError) or isinstance(getattr(exc, "reason", None), TimeoutError)
        raise GenerationUnavailable("generation_timeout" if report_timeout and timed_out
                                    else "generation_service_unavailable") from exc
    if not isinstance(result, dict) or result.get("truncated") is not False or not isinstance(result.get("output"), dict):
        raise ValueError("invalid_generation_response")
    return result
