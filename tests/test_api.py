import io
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from xlagent.api import create_app
from xlagent.config import Settings


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(replay=True, work_dir=tmp_path / "jobs"))
    with TestClient(app) as c:
        yield c


def _wait(client, job_id, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/jobs/{job_id}").json()
        if body["status"] in ("done", "failed"):
            return body
        time.sleep(0.2)
    raise AssertionError("job did not finish")


def test_job_lifecycle(client, examples_dir):
    folder = examples_dir / "sales_consolidation"
    files = [
        ("files", (p.name, p.read_bytes(), "application/octet-stream"))
        for p in sorted((folder / "inputs").iterdir())
    ]
    expected = folder / "expected" / "sales_summary.xlsx"
    files.append(("expected", (expected.name, expected.read_bytes(), "application/octet-stream")))
    task = (folder / "task.md").read_text(encoding="utf-8")

    created = client.post("/jobs", data={"task": task}, files=files)
    assert created.status_code == 202
    job = created.json()
    assert job["status"] in ("queued", "running", "done")
    assert job["download_url"] is None or job["status"] == "done"

    body = _wait(client, job["id"])
    assert body["status"] == "done", body["error"]
    assert body["spec"]["output_format"]["file_name"] == "sales_summary.xlsx"
    assert len(body["assumptions"]) == 3
    assert [a["outcome"] for a in body["attempts"]] == ["safety_rejected", "passed"]

    download = client.get(body["download_url"])
    assert download.status_code == 200
    with zipfile.ZipFile(io.BytesIO(download.content)) as zf:
        assert sorted(zf.namelist()) == [
            "README_for_colleague.md",
            "requirements.txt",
            "run.bat",
            "script.py",
        ]


def test_unknown_job_is_404(client):
    assert client.get("/jobs/doesnotexist").status_code == 404
    assert client.get("/jobs/doesnotexist/download").status_code == 404


def test_unrecorded_task_fails_cleanly_in_replay_mode(client, examples_dir):
    p = sorted((examples_dir / "sales_consolidation" / "inputs").iterdir())[0]
    created = client.post(
        "/jobs",
        data={"task": "Something nobody recorded before"},
        files=[("files", (p.name, p.read_bytes(), "application/octet-stream"))],
    )
    body = _wait(client, created.json()["id"])
    assert body["status"] == "failed"
    assert "No recorded session" in body["error"]
    assert client.get(f"/jobs/{body['id']}/download").status_code == 409


def test_rejects_non_spreadsheet_upload(client):
    created = client.post(
        "/jobs",
        data={"task": "Please do something useful"},
        files=[("files", ("notes.txt", b"hello", "text/plain"))],
    )
    assert created.status_code == 422


def test_upload_names_are_sanitised(client, examples_dir, tmp_path):
    p = sorted((examples_dir / "sales_consolidation" / "inputs").iterdir())[0]
    created = client.post(
        "/jobs",
        data={"task": "Something nobody recorded before"},
        files=[("files", ("../../evil name.xlsx", p.read_bytes(), "application/octet-stream"))],
    )
    assert created.json()["inputs"] == ["evil_name.xlsx"]


def test_json_endpoint_runs_the_same_job(client, examples_dir):
    import base64

    folder = examples_dir / "asset_depreciation"
    payload = {
        "task": (folder / "task.md").read_text(encoding="utf-8"),
        "files": [
            {"name": p.name, "content_base64": base64.b64encode(p.read_bytes()).decode()}
            for p in sorted((folder / "inputs").iterdir())
        ],
        "expected": {
            "name": "depreciation_2026.xlsx",
            "content_base64": base64.b64encode(
                (folder / "expected" / "depreciation_2026.xlsx").read_bytes()
            ).decode(),
        },
    }
    created = client.post("/jobs/json", json=payload)
    assert created.status_code == 202
    body = _wait(client, created.json()["id"])
    assert body["status"] == "done", body["error"]
    assert [a["outcome"] for a in body["attempts"]] == ["mismatch", "passed"]


def test_json_endpoint_rejects_bad_base64(client):
    created = client.post(
        "/jobs/json",
        json={"task": "Please do something", "files": [{"name": "a.xlsx", "content_base64": "%%"}]},
    )
    assert created.status_code == 422
