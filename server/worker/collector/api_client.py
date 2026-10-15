"""Documented customer read APIs. Authentication tokens are supplied by the operator."""
import json
import time
from datetime import datetime,timezone
import os
from pathlib import Path
from urllib.parse import urlsplit, quote
from urllib.request import Request, build_opener
from urllib.error import HTTPError, URLError
from generation_client import NoRedirect

class SourceUnavailable(RuntimeError):
    pass

class CustomerAPI:
    def __init__(self, configuration_url, monitoring_url, token, *, allowed_http_host="", timeout=10):
        self.configuration_url = self._base(configuration_url, allowed_http_host)
        self.monitoring_url = self._base(monitoring_url, allowed_http_host)
        if not isinstance(token,str) or not token.strip() or any(c in token for c in "\r\n"):
            raise ValueError("collector_token_required")
        self.token, self.timeout = token, timeout
        self.opener = build_opener(NoRedirect())
        self.last_receipt = None

    @staticmethod
    def _base(value, allowed):
        parsed = urlsplit(value)
        if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/") or not (
            parsed.scheme == "https" or parsed.scheme == "http" and parsed.hostname == allowed):
            raise ValueError("collector_base_url_rejected")
        return value.rstrip("/")

    def _read(self, base, path, body=None):
        # Only read operations observed in the attached transcript.
        if path != "/configuration/interfaces/list" and path != "/monitoring/transactions/in-progress" and not path.startswith("/configuration/interfaces/"):
            raise ValueError("unknown_read_endpoint")
        request = Request(base + path, data=None if body is None else json.dumps(body).encode(),
            headers={"Authorization":"Bearer " + self.token,"Content-Type":"application/json"})
        try:
            with self.opener.open(request,timeout=self.timeout) as response:
                self.last_receipt["http_status"] = response.status
                raw=response.read(8_000_001)
                self.last_receipt["response_bytes"] = len(raw)
        except HTTPError as exc:
            self.last_receipt["http_status"] = exc.code
            code = "source_auth_rejected" if exc.code in (401,403) else "source_http_failed"
            exc.close()
            raise SourceUnavailable(code) from None
        except (URLError,OSError):
            raise SourceUnavailable("source_unavailable") from None
        if len(raw)>8_000_000:
            raise SourceUnavailable("source_response_too_large")
        try:
            result=json.loads(raw)
        except (ValueError,UnicodeError):
            raise SourceUnavailable("source_json_rejected") from None
        if isinstance(result,dict) and isinstance(result.get("responseCode"),str):
            self.last_receipt["response_code"] = result["responseCode"][:32]
        if not isinstance(result,dict) or result.get("responseCode") != "0000" or "responseObject" not in result:
            raise SourceUnavailable("source_response_rejected")
        return result["responseObject"]

    def read(self, base, path, body=None):
        started = time.perf_counter()
        self.last_receipt = {"started_at": datetime.now(timezone.utc), "http_status": None,
                             "response_code": None,"response_bytes": 0,"error_code": None}
        try:
            return self._read(base,path,body)
        except (ValueError,SourceUnavailable) as exc:
            self.last_receipt["error_code"] = str(exc)[:100]
            raise
        finally:
            self.last_receipt["duration_ms"] = (time.perf_counter()-started)*1000
            self.last_receipt["finished_at"] = datetime.now(timezone.utc)
            self.last_receipt["received_monotonic"] = time.monotonic()

    def interfaces(self,page,size=10):
        result=self.read(self.configuration_url,"/configuration/interfaces/list",
            {"body":{"searchConditions":[],"currentPage":page,"pageSize":size}})
        if not isinstance(result,dict) or not isinstance(result.get("content"),list) or type(result.get("totalPages")) is not int or not 0 <= result["totalPages"] <= 100000 or len(result["content"])>size:
            raise SourceUnavailable("source_page_rejected")
        return result

    def interface(self,identifier):
        if not isinstance(identifier,str) or not identifier or len(identifier)>256:
            raise ValueError("invalid_interface_id")
        return self.read(self.configuration_url,"/configuration/interfaces/"+quote(identifier,safe=""))

    def in_progress(self):
        result=self.read(self.monitoring_url,"/monitoring/transactions/in-progress")
        if not isinstance(result,list):
            raise SourceUnavailable("source_in_progress_rejected")
        return result


def from_environment():
    token_file=os.environ.get("COLLECTOR_ACCESS_TOKEN_FILE")
    token=Path(token_file).read_text().strip() if token_file else os.environ.get("COLLECTOR_ACCESS_TOKEN") or os.environ.get("ACCESS_TOKEN") or os.environ.get("TOKEN","")
    return CustomerAPI(os.environ["COLLECTOR_CONFIGURATION_URL"],os.environ["COLLECTOR_MONITORING_URL"],token,
        allowed_http_host=os.environ.get("COLLECTOR_HTTP_HOST",""))
