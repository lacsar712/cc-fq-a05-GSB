"""Integration tests for the server-issued QC excerpt download."""

import hashlib
import hmac
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import api as api_module
from app.api import router
from app.config import settings
from app.database import Base, get_db
from app.excerpt import render_excerpt
from app.models import Job, JobStage


GOOD_FASTQ = """@SEQ1
ACGTACGT
+
IIIIHHHH
@SEQ2
NNNNACGT
+
IIIIIIII
"""

BROKEN_FASTQ = """@SEQ1
ACGT
NOTPLUS
IIII
"""

SIGNATURE_LINE = "签发签名: sha256="


@pytest.fixture()
def client(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    test_session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)
    # Background runner imports SessionLocal at module level.
    monkeypatch.setattr(api_module, "SessionLocal", test_session)

    app = FastAPI()
    app.include_router(router)

    def override_get_db():
        db = test_session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def _token(client, username, password):
    res = client.post("/api/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200
    return res.json()["access_token"]


@pytest.fixture()
def bioops_headers(client):
    return {"Authorization": f"Bearer {_token(client, 'bioops', 'fastq123456')}"}


@pytest.fixture()
def auditor_headers(client):
    return {"Authorization": f"Bearer {_token(client, 'auditor', 'audit123456')}"}


def _submit_job(client, headers, fastq_text):
    res = client.post("/api/jobs", json={"fastqText": fastq_text}, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _verify_signature(text: str) -> bool:
    lines = text.splitlines()
    sig = next(line for line in lines if line.startswith(SIGNATURE_LINE)).split("=", 1)[1]
    # Everything before the trailing separator block is the signed body.
    signed_base = text.split("-" * 48)[0].rstrip("\n")
    expected = hmac.new(
        settings.jwt_secret.encode("utf-8"),
        signed_base.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(sig, expected)


def test_render_excerpt_unit():
    job = Job(
        id=7,
        sample_name="demo-good-r1",
        status="failed",
        created_by="bioops",
        metrics=None,
        error_message="FASTQ 记录不完整",
        created_at=datetime(2026, 9, 20, tzinfo=timezone.utc),
        finished_at=datetime(2026, 9, 20, 0, 0, 1, tzinfo=timezone.utc),
    )
    stages = [
        JobStage(
            job_id=7,
            actor_name="ParseActor",
            stage_order=0,
            status="failed",
            message="FASTQ 记录不完整",
        ),
        JobStage(
            job_id=7,
            actor_name="QualityHistActor",
            stage_order=1,
            status="skipped",
            message="因 ParseActor 失败而跳过",
        ),
    ]
    text = render_excerpt(job, stages, datetime(2026, 9, 20, tzinfo=timezone.utc))
    assert "作业编号: 7" in text
    assert "样例名称: demo-good-r1" in text
    assert "失败 (failed)" in text
    assert "ParseActor" in text and "QualityHistActor" in text
    assert "Actor 阶段（共 2 个）" in text
    assert "读段数" in text and "平均质量" in text and "N 含量比例" in text
    assert "失败原因: FASTQ 记录不完整" in text
    assert _verify_signature(text)


def test_download_success_excerpt_stage_count_matches_detail(client, bioops_headers):
    job_id = _submit_job(client, bioops_headers, GOOD_FASTQ)

    detail = client.get(f"/api/jobs/{job_id}", headers=bioops_headers)
    assert detail.status_code == 200
    detail_stages = detail.json()["stages"]
    assert detail.json()["status"] == "success"
    assert len(detail_stages) == 4

    res = client.get(f"/api/jobs/{job_id}/excerpt", headers=bioops_headers)
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/plain")
    assert res.headers["content-disposition"].startswith(
        f'attachment; filename="qc_excerpt_job{job_id}_'
    )

    text = res.content.decode("utf-8")
    assert "作业状态: 成功 (success)" in text
    assert "样例名称: 自定义输入" in text
    assert "Actor 阶段（共 4 个）" in text
    # Self-test: stage count in file equals detail stage count.
    listed = [ln for ln in text.splitlines() if " — 状态: " in ln]
    assert len(listed) == len(detail_stages)
    for stage in detail_stages:
        assert stage["actor_name"] in text
    # Three metrics present with real values.
    assert "读段数" in text and ": 2 (reads)" in text
    assert "(mean_quality)" in text
    assert "(n_rate)" in text
    # Every actor state from the detail appears in the excerpt.
    for stage in detail_stages:
        assert f"({stage['status']})" in text
    assert _verify_signature(text)


def test_download_failed_excerpt(client, bioops_headers):
    job_id = _submit_job(client, bioops_headers, BROKEN_FASTQ)

    detail = client.get(f"/api/jobs/{job_id}", headers=bioops_headers).json()
    assert detail["status"] == "failed"

    res = client.get(f"/api/jobs/{job_id}/excerpt", headers=bioops_headers)
    assert res.status_code == 200
    text = res.content.decode("utf-8")
    assert "作业状态: 失败 (failed)" in text
    assert "ParseActor" in text and "失败 (failed)" in text
    assert "已跳过 (skipped)" in text
    assert "失败原因:" in text
    listed = [ln for ln in text.splitlines() if " — 状态: " in ln]
    assert len(listed) == len(detail["stages"]) == 4
    assert _verify_signature(text)


def test_auditor_may_download(client, bioops_headers, auditor_headers):
    job_id = _submit_job(client, bioops_headers, GOOD_FASTQ)
    res = client.get(f"/api/jobs/{job_id}/excerpt", headers=auditor_headers)
    assert res.status_code == 200
    assert _verify_signature(res.content.decode("utf-8"))


def test_excerpt_requires_auth(client):
    res = client.get("/api/jobs/1/excerpt")
    assert res.status_code == 401


def test_excerpt_unknown_job_404(client, bioops_headers):
    res = client.get("/api/jobs/999/excerpt", headers=bioops_headers)
    assert res.status_code == 404


def test_tampered_body_fails_signature_verification(client, bioops_headers):
    job_id = _submit_job(client, bioops_headers, GOOD_FASTQ)
    text = client.get(f"/api/jobs/{job_id}/excerpt", headers=bioops_headers).text
    tampered = text.replace("作业状态: 成功", "作业状态: 失败", 1)
    assert not _verify_signature(tampered)
