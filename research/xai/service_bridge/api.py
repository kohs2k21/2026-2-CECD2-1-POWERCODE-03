"""CPU API gateway using existing backend authentication and feedback storage.

Deployment origin and enterprise identities are trusted configuration, never
request parameters. This does not start a collector or expose a chat endpoint.
"""
import os
from fastapi import FastAPI, HTTPException, Request
import httpx

from xai_runtime.artifacts import InputError
from .client import AnalysisClient


def create_app(backend_origin=None, enterprise_actor_ids=None, backend_transport=None):
    origin = backend_origin or os.environ.get('XAI_BACKEND_ORIGIN')
    if not origin:
        raise ValueError('XAI_BACKEND_ORIGIN_required')
    actors = tuple(enterprise_actor_ids if enterprise_actor_ids is not None else
                   filter(None, os.environ.get('XAI_ENTERPRISE_ACTOR_IDS','').split(',')))
    app = FastAPI(title='Grounded XAI bridge', version='1.0.0', docs_url=None, redoc_url=None)

    def client(request):
        header = request.headers.get('authorization','')
        if not header.startswith('Bearer ') or len(header) > 8192:
            raise HTTPException(401, 'bearer_authentication_required')
        try:
            return AnalysisClient(origin, header[7:], actors, backend_transport)
        except InputError as exc:
            raise HTTPException(400, str(exc)) from exc

    def call(request, operation):
        c = client(request)
        try:
            return operation(c)
        except httpx.HTTPStatusError as exc:
            # No upstream body, JWT, arbitrary host or stack trace in user errors.
            code = exc.response.status_code
            raise HTTPException(code if code in (401,403,404,409,422) else 502,
                                'backend_request_rejected') from exc
        except (httpx.RequestError, TimeoutError) as exc:
            raise HTTPException(503, 'backend_unavailable') from exc
        except (InputError, ValueError, KeyError, TypeError) as exc:
            raise HTTPException(422, 'explanation_evidence_or_review_rejected') from exc
        finally:
            c.close()

    @app.get('/healthz')
    def health():
        return dict(status='ready', contract='xai-service-report/1.0.0',
                    backend_connectivity='not_probed', execution='code_no_GPU', followup_chat=False)

    @app.get('/v1/explanations/{decision_id}')
    def explain(decision_id: str, request: Request):
        return call(request, lambda c:c.explanation(decision_id))

    @app.post('/v1/explanations/{decision_id}/reviews')
    async def review(decision_id: str, request: Request):
        # Small structured review only. JWT/admin/actor verification remains upstream.
        raw = await request.body()
        if len(raw) > 16000:
            raise HTTPException(413, 'review_too_large')
        try:
            import json
            body = json.loads(raw)
        except ValueError as exc:
            raise HTTPException(400, 'invalid_json') from exc
        from starlette.concurrency import run_in_threadpool
        def submit(c):
            report = c.explanation(decision_id)
            return c.submit_review(report, body)
        return await run_in_threadpool(call, request, submit)

    return app
