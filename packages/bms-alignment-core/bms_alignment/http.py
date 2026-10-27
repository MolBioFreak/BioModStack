"""Small typed client for the existing BMS model and Job HTTP routes.

The server remains authoritative for admission, project ownership, and execution.
"""
from __future__ import annotations

import json
from typing import Any, Literal
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pydantic import BaseModel, ConfigDict, Field
from .models import AlignmentRequest


class ModelMode(BaseModel):
    model_config = ConfigDict(extra='allow')
    id: str
    parameter_schema: dict[str, Any] | None = None


class ModelInfo(BaseModel):
    model_config = ConfigDict(extra='allow')
    id: str
    modes: list[ModelMode]


class JobSubmission(BaseModel):
    """The alignment-specific subset of the existing JobCreate wire schema."""
    name: str
    model_id: Literal['family_msa_protein', 'family_msa_nucleic']
    mode: Literal['family_msa'] = 'family_msa'
    params: AlignmentRequest
    launch_context_id: str | None = None
    execution_target_id: str | None = None
    execution_plan_approval: str | None = None
    execution_policy: dict[str, Any] | None = None


class JobPreview(BaseModel):
    model_config = ConfigDict(extra='allow', populate_by_name=True)
    schema_name: Literal['bms.job.execution-preview.v1'] = Field(alias='schema')
    approval_digest: str
    admissible: bool
    request: dict[str, Any]
    plan: dict[str, Any]
    input_identities: list[dict[str, Any]]
    generated_inputs: list[dict[str, Any]]
    declared_expansions: list[dict[str, Any]] = Field(default_factory=list)
    deferred_preparation: list[str]
    blockers: list[dict[str, Any]]


class Job(BaseModel):
    model_config = ConfigDict(extra='allow')
    id: str
    name: str
    status: str
    model_id: str
    mode: str


class BmsHttpError(Exception):
    def __init__(self, status: int, body: bytes):
        self.status = status
        self.body = body
        super().__init__(f'BMS HTTP {status}: {body.decode("utf-8", "replace")}')


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class AlignmentClient:
    def __init__(self, base_url: str, *, headers: dict[str, str] | None = None, timeout: float = 30):
        parts = urlsplit(base_url)
        if parts.scheme not in ('http', 'https') or not parts.netloc or parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError('base_url must be an HTTP(S) origin or base path without credentials, query, or fragment')
        self.base_url = base_url.rstrip('/')
        self.headers = dict(headers or {})
        self.timeout = timeout
        self._opener = build_opener(_NoRedirect())

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None, *, launch_context_id: str | None = None) -> Any:
        headers = {**self.headers, 'Accept': 'application/json'}
        if launch_context_id is not None:
            headers['x-bms-launch-context-id'] = launch_context_id
        data = None
        if body is not None:
            data = json.dumps(body).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        request = Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                return json.load(response)
        except HTTPError as exc:
            raise BmsHttpError(exc.code, exc.read()) from exc

    def model(self, model_id: Literal['family_msa_protein', 'family_msa_nucleic']) -> ModelInfo:
        return ModelInfo.model_validate(self._request('GET', '/api/models/' + quote(model_id, safe='')))

    @staticmethod
    def _submission_body(submission: JobSubmission) -> dict[str, Any]:
        body = submission.model_dump(mode='json', exclude_unset=True)
        body['mode'] = submission.mode
        return body

    def preview(self, submission: JobSubmission) -> JobPreview:
        return JobPreview.model_validate(self._request('POST', '/api/jobs/execution-plan/preview',
            self._submission_body(submission), launch_context_id=submission.launch_context_id))

    def submit(self, submission: JobSubmission) -> Job:
        return Job.model_validate(self._request('POST', '/api/jobs',
            self._submission_body(submission), launch_context_id=submission.launch_context_id))

    def job(self, job_id: str) -> Job:
        return Job.model_validate(self._request('GET', '/api/jobs/' + quote(job_id, safe='')))
