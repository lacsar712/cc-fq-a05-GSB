"""Server-side generation and signing of per-job QC excerpts.

The excerpt text is produced solely by the backend from the database and
carries an HMAC signature so a browser/client cannot handcraft or tamper
with a downloaded excerpt and pass it off as an issued one.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone

from app.config import settings
from app.models import Job, JobStage

STATUS_LABELS = {
    "pending": "排队中",
    "running": "运行中",
    "success": "成功",
    "failed": "失败",
    "skipped": "已跳过",
}

METRIC_LABELS = [
    ("reads", "读段数"),
    ("mean_quality", "平均质量"),
    ("n_rate", "N 含量比例"),
]


def _fmt_dt(value: datetime | None) -> str:
    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _status_label(status: str) -> str:
    return STATUS_LABELS.get(status, status)


def _metric_value(metrics: dict | None, key: str) -> str:
    if not metrics:
        return "—"
    value = metrics.get(key)
    if value is None:
        summary = metrics.get("summary")
        if isinstance(summary, dict):
            value = summary.get(key)
    return "—" if value is None else str(value)


def render_excerpt(job: Job, stages: list[JobStage], issued_at: datetime) -> str:
    """Render the signed QC excerpt text for a job (any terminal status)."""
    lines: list[str] = []
    lines.append("FASTQ 质控摘录 / FASTQ QC EXCERPT")
    lines.append("=" * 48)
    lines.append("签发服务: fastq-qc-pipeline")
    lines.append(f"签发时间: {_fmt_dt(issued_at)}")
    lines.append(f"作业编号: {job.id}")
    lines.append(f"作业状态: {_status_label(job.status)} ({job.status})")
    lines.append(f"样例名称: {job.sample_name}")
    lines.append(f"提交人: {job.created_by}")
    lines.append(f"创建时间: {_fmt_dt(job.created_at)}")
    lines.append(f"完成时间: {_fmt_dt(job.finished_at)}")
    lines.append("")
    lines.append("三指标:")
    for key, label in METRIC_LABELS:
        lines.append(f"  {label:<10}: {_metric_value(job.metrics, key)} ({key})")
    lines.append("")
    lines.append(f"Actor 阶段（共 {len(stages)} 个）:")
    for st in stages:
        lines.append(
            f"  #{st.stage_order + 1} {st.actor_name}"
            f" — 状态: {_status_label(st.status)} ({st.status})"
        )
        detail = st.message or "—"
        lines.append(f"      开始: {_fmt_dt(st.started_at)}  结束: {_fmt_dt(st.finished_at)}")
        lines.append(f"      说明: {detail}")
    if job.status == "failed" and job.error_message:
        lines.append("")
        lines.append(f"失败原因: {job.error_message}")

    body = "\n".join(lines)
    signature = hmac.new(
        settings.jwt_secret.encode("utf-8"),
        body.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    lines.append("")
    lines.append("-" * 48)
    lines.append(f"签发签名: sha256={signature}")
    return "\n".join(lines) + "\n"


def excerpt_filename(job: Job) -> str:
    """ASCII-only fallback filename (header's filename token is latin-1)."""
    safe_name = "".join(
        c if (c.isascii() and c.isalnum()) or c in "-_" else "_"
        for c in job.sample_name
    )
    return f"qc_excerpt_job{job.id}_{safe_name}.txt"


def excerpt_filename_utf8(job: Job) -> str:
    """Human-readable filename preserving the (possibly Chinese) sample name."""
    return f"qc_excerpt_job{job.id}_{job.sample_name}.txt"
