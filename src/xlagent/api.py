"""HTTP API for non-interactive jobs (this is what the n8n workflow calls).

POST /jobs                 files + task description -> job id (202)
POST /jobs/json            same, files as base64 in a JSON body (easier from n8n)
GET  /jobs/{id}            status, spec, assumptions, attempts
GET  /jobs/{id}/download   the deliverable zip once status == "done"

Why non-interactive here: an email-triggered workflow has nobody to answer
questions, so the agent makes conservative assumptions and lists them; the
workflow mails them back with the zip so a wrong guess is visible.

Jobs run in a small thread pool and live in memory (plus a folder per job on
disk). That is enough for an internal tool behind n8n; a queue and a database
would be the next step for multi-instance use.
"""

from __future__ import annotations

import base64
import binascii
import logging
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from xlagent.agent.pipeline import Attempt, run_noninteractive_job
from xlagent.config import Settings
from xlagent.llm.factory import make_provider
from xlagent.xlsx_io import is_table_file

log = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass
class Job:
    id: str
    task: str
    input_names: list[str]
    folder: Path
    status: str = "queued"  # queued -> running -> done | failed
    created_at: str = ""
    spec: dict[str, Any] | None = None
    assumptions: list[str] = field(default_factory=list)
    attempts: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    @property
    def zip_path(self) -> Path:
        return self.folder / "deliverable.zip"

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "created_at": self.created_at,
            "task": self.task,
            "inputs": self.input_names,
            "spec": self.spec,
            "assumptions": self.assumptions,
            "attempts": self.attempts,
            "error": self.error,
            "download_url": f"/jobs/{self.id}/download" if self.status == "done" else None,
        }


class JobStore:
    def __init__(self, settings: Settings, max_workers: int = 2) -> None:
        self.settings = settings
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()
        self.pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="xlagent-job")

    def get(self, job_id: str) -> Job:
        with self.lock:
            job = self.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return job

    def submit(self, job: Job, inputs: list[Path], expected: Path | None) -> None:
        with self.lock:
            self.jobs[job.id] = job
        self.pool.submit(self._run, job, inputs, expected)

    def _run(self, job: Job, inputs: list[Path], expected: Path | None) -> None:
        job.status = "running"

        def on_attempt(attempt: Attempt) -> None:
            job.attempts.append(attempt.to_dict())

        try:
            result = run_noninteractive_job(
                make_provider(self.settings),  # fresh provider per job (replay cursor)
                job.task,
                inputs,
                expected_file=expected,
                timeout_s=self.settings.sandbox_timeout_s,
                run_root=job.folder / "runs",
                zip_dest=job.zip_path,
                on_attempt=on_attempt,
            )
        except Exception as exc:  # a job must never take the server down
            log.exception("job %s crashed", job.id)
            job.status, job.error = "failed", f"internal error: {exc}"
            return
        if result.submission is not None:
            job.spec = result.submission.spec.model_dump(mode="json")
            job.assumptions = list(result.submission.assumptions)
        job.error = result.error
        job.status = "done" if result.status == "done" else "failed"
        log.info("job %s finished: %s", job.id, job.status)


def _safe_filename(name: str) -> str:
    cleaned = SAFE_NAME.sub("_", Path(name or "upload").name).strip("._")
    return cleaned or "upload"


class FilePayload(BaseModel):
    name: str = Field(min_length=1)
    content_base64: str = Field(min_length=1)

    def decode(self) -> bytes:
        try:
            return base64.b64decode(self.content_base64, validate=True)
        except binascii.Error as exc:
            raise HTTPException(status_code=422, detail=f"{self.name}: invalid base64") from exc


class JobRequest(BaseModel):
    """JSON variant of POST /jobs, for clients that cannot build multipart bodies easily."""

    task: str = Field(min_length=10)
    files: list[FilePayload] = Field(min_length=1)
    expected: FilePayload | None = None


def _check_upload(name: str, data: bytes) -> None:
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"{name} is larger than 20 MB")
    if not is_table_file(Path(name)):
        raise HTTPException(status_code=422, detail=f"{name}: only .xlsx/.csv accepted")


def _write_upload(name: str, data: bytes, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / _safe_filename(name)
    path.write_bytes(data)
    return path


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    store = JobStore(settings)
    app = FastAPI(title="xlagent", version="0.1.0", description=__doc__)
    app.state.store = store

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "replay": settings.replay}

    def _create(
        task: str, files: list[tuple[str, bytes]], expected: tuple[str, bytes] | None
    ) -> dict[str, Any]:
        for name, data in [*files, *([expected] if expected else [])]:
            _check_upload(name, data)  # validate everything before writing anything
        job_id = uuid.uuid4().hex[:12]
        folder = settings.work_dir / job_id
        inputs = [_write_upload(n, d, folder / "inputs") for n, d in files]
        expected_path = _write_upload(*expected, folder / "expected") if expected else None
        job = Job(
            id=job_id,
            task=task,
            input_names=[p.name for p in inputs],
            folder=folder,
            created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        store.submit(job, inputs, expected_path)
        log.info("job %s queued with %d files", job_id, len(inputs))
        return job.public()

    @app.post("/jobs", status_code=202)
    async def create_job(
        task: str = Form(..., min_length=10, description="what the colleague wants"),
        files: list[UploadFile] = File(..., description="sample input files (.xlsx/.csv)"),
        expected: UploadFile | None = File(None, description="optional expected output"),
    ) -> dict[str, Any]:
        raw = [(f.filename or "upload", await f.read()) for f in files]
        exp = (expected.filename or "expected", await expected.read()) if expected else None
        return _create(task, raw, exp)

    @app.post("/jobs/json", status_code=202)
    def create_job_json(request: JobRequest) -> dict[str, Any]:
        """Same as POST /jobs with base64 file contents (used by the n8n workflow)."""
        raw = [(f.name, f.decode()) for f in request.files]
        exp = (request.expected.name, request.expected.decode()) if request.expected else None
        return _create(request.task, raw, exp)

    @app.get("/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        return store.get(job_id).public()

    @app.get("/jobs/{job_id}/download")
    def download(job_id: str) -> FileResponse:
        job = store.get(job_id)
        if job.status != "done":
            raise HTTPException(status_code=409, detail=f"job is {job.status}, not done")
        return FileResponse(
            job.zip_path, media_type="application/zip", filename=f"xlagent_{job_id}.zip"
        )

    return app


app = create_app()
