"""Thin client for existing authenticated analysis/jobs HTTP endpoints.

No auto retries on writes; caller owns idempotency. Secrets come from environment
or a server credential provider, never from saved experiment configuration.
"""
from urllib.parse import urlsplit
import httpx

from xai_runtime.artifacts import require
from .adapter import explain_detail, uuid_text
from .reviews import apply_feedback, feedback_payload


def detail_projection(detail):
    """Only saved evidence and bound identifiers enter XAI, not UI/user objects."""
    keys = ('decisionId','sourceId','processObservationId','transactionId','processId','status','inputManifestHash','evidence','processInput')
    return {key:detail[key] for key in keys if key in detail}


class AnalysisClient:
    def __init__(self, base_url, token, enterprise_actor_ids=(), transport=None):
        url = urlsplit(base_url)
        require(url.scheme in ('http','https') and bool(url.hostname) and not url.username
                and not url.password and not url.query and not url.fragment and url.path in ('','/'),
                'invalid_backend_origin')
        require(isinstance(token, str) and bool(token) and '\n' not in token and '\r' not in token, 'invalid_bearer_token')
        self.http = httpx.Client(base_url=base_url.rstrip('/'), headers={'Authorization':'Bearer '+token},
                                 transport=transport, timeout=10.0, follow_redirects=False)
        self.enterprise_actor_ids = tuple(enterprise_actor_ids)

    def close(self): self.http.close()

    def _request(self, method, url, **kwargs):
        response = self.http.request(method, url, **kwargs)
        response.raise_for_status()
        require(len(response.content) <= 1000000, 'backend_response_too_large')
        return response.json()

    def explanation(self, decision_id):
        decision_id = uuid_text(decision_id)
        detail = self._request('GET', '/api/analysis/decisions/' + decision_id)
        require(detail.get('decisionId') == decision_id, 'backend_decision_identity_mismatch')
        report = explain_detail(detail_projection(detail))
        try:
            feedback = self._request('GET', f'/api/analysis/decisions/{decision_id}/feedback')
            require(feedback['id'] == detail.get('id',decision_id), 'feedback_target_mismatch')
            return apply_feedback(report, feedback['items'], self.enterprise_actor_ids)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 403: raise
            report['enterprise_review'] = dict(status='restricted', independent_reviews=None,
                                              LLM_better_than_code='not_established')
            return report

    def submit_explanation(self, source_id, decision_id, idempotency_key):
        return self._request('POST', '/api/jobs', json=dict(kind='explain', sourceId=uuid_text(source_id),
            decisionId=uuid_text(decision_id)), headers={'Idempotency-Key':uuid_text(idempotency_key)})

    def job(self, job_id):
        return self._request('GET', '/api/jobs/' + uuid_text(job_id))

    def submit_review(self, report, record):
        # Backend authenticates author/admin and records actorId. Never spoof it.
        body = feedback_payload(report, record)
        return self._request('POST', f"/api/analysis/decisions/{uuid_text(report['decisionId'])}/feedback", json=body)
