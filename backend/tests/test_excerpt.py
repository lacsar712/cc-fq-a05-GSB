"""Tests for the backend-issued QC excerpt: builder unit tests + download API."""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.excerpt import build_job_excerpt
from app.main import app
from app.models import Job, JobStage


NOW = datetime(2026, 9, 20, 8, 0, 0, tzinfo=timezone.utc)

GOOD_METRICS = {"reads": 2, "mean_quality": 36.875, "n_rate": 0.25}


def _make_job(status="success", metrics=None, error_message=None) -> Job:
    job = Job(
        id=1,
        sample_id=None,
        sample_name="demo-good-r1",
        status=status,
        created_by="bioops",
        metrics=metrics,
        error_message=error_message,
        fastq_snapshot="@SEQ1\nACGT\n+\nIIII\n",
        created_at=NOW,
        finished_at=NOW,
    )
    specs = [
        ("ParseActor", "success", "完成"),
        ("QualityHistActor", "success", "完成"),
        ("NContentActor", "success", "完成"),
        ("ReportActor", "success", "完成"),
    ]
    if status == "failed":
        specs = [
            ("ParseActor", "failed", "第 3 行分隔符必须以 + 开头"),
            ("QualityHistActor", "skipped", "因 ParseActor 失败而跳过"),
            ("NContentActor", "skipped", "因 ParseActor 失败而跳过"),
            ("ReportActor", "skipped", "因 ParseActor 失败而跳过"),
        ]
    job.stages = [
        JobStage(
            job_id=job.id,
            actor_name=name,
            stage_order=order,
            status=st,
            message=msg,
        )
        for order, (name, st, msg) in enumerate(specs)
    ]
    return job


def test_excerpt_success_contains_status_sample_metrics_and_all_stages():
    text = build_job_excerpt(_make_job(metrics=GOOD_METRICS))
    assert "作业状态: 成功 (success)" in text
    assert "样例名称: demo-good-r1" in text
    assert "reads: 2" in text
    assert "mean_quality: 36.875" in text
    assert "n_rate: 0.25" in text
    assert "Actor 阶段（共 4 个）" in text
    for name in ("ParseActor", "QualityHistActor", "NContentActor", "ReportActor"):
        assert name in text


def test_excerpt_failed_job_shows_error_and_skipped_stages():
    text = build_job_excerpt(
        _make_job(status="failed", error_message="第 3 行分隔符必须以 + 开头")
    )
    assert "作业状态: 失败 (failed)" in text
    assert "失败原因: 第 3 行分隔符必须以 + 开头" in text
    assert "Actor 阶段（共 4 个）" in text
    assert "ParseActor  失败 (failed)" in text
    assert text.count("已跳过 (skipped)") == 3
    # No metrics persisted for a parse failure: placeholders, not fabricated values
    assert "reads: —" in text
    assert "mean_quality: —" in text
    assert "n_rate: —" in text


# --- Download API (SQLite-backed, no Postgres needed) ---


engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def _override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = _override_get_db


@pytest.fixture()
def client():
    Base.metadata.create_all(bind=engine)
    yield TestClient(app)
    Base.metadata.drop_all(bind=engine)


def _token(client: TestClient, username: str, password: str) -> str:
    res = client.post("/api/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200, res.text
    return res.json()["access_token"]


def _seed_job(status: str, metrics, error_message=None) -> int:
    db = TestingSessionLocal()
    try:
        job = _make_job(status=status, metrics=metrics, error_message=error_message)
        db.add(job)
        db.commit()
        return job.id
    finally:
        db.close()


def test_download_excerpt_success_job_stage_count_matches_stages_api(client):
    job_id = _seed_job("success", GOOD_METRICS)
    token = _token(client, "bioops", "fastq123456")
    headers = {"Authorization": f"Bearer {token}"}

    res = client.get(f"/api/jobs/{job_id}/excerpt", headers=headers)
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/plain")
    disposition = res.headers["content-disposition"]
    assert "attachment" in disposition
    assert f"job-{job_id}-qc-excerpt.txt" in disposition

    text = res.text
    assert "作业状态: 成功 (success)" in text
    assert "样例名称: demo-good-r1" in text
    assert "reads: 2" in text

    # Stage count in the file must match the detail view's stage list
    stages = client.get(f"/api/jobs/{job_id}/stages", headers=headers).json()
    assert f"Actor 阶段（共 {len(stages)} 个）" in text
    for stage in stages:
        assert stage["actor_name"] in text


def test_download_excerpt_failed_job(client):
    job_id = _seed_job("failed", None, error_message="第 3 行分隔符必须以 + 开头")
    token = _token(client, "bioops", "fastq123456")
    res = client.get(
        f"/api/jobs/{job_id}/excerpt", headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 200
    assert "作业状态: 失败 (failed)" in res.text
    assert "失败原因: 第 3 行分隔符必须以 + 开头" in res.text
    assert "Actor 阶段（共 4 个）" in res.text


def test_download_excerpt_allowed_for_auditor(client):
    job_id = _seed_job("success", GOOD_METRICS)
    token = _token(client, "auditor", "audit123456")
    res = client.get(
        f"/api/jobs/{job_id}/excerpt", headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 200
    assert "作业状态: 成功 (success)" in res.text


def test_download_excerpt_requires_login(client):
    job_id = _seed_job("success", GOOD_METRICS)
    res = client.get(f"/api/jobs/{job_id}/excerpt")
    assert res.status_code == 401


def test_download_excerpt_missing_job(client):
    token = _token(client, "bioops", "fastq123456")
    res = client.get(
        "/api/jobs/9999/excerpt", headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 404
