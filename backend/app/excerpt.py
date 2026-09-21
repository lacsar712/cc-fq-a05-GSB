"""Build the backend-issued plain-text QC excerpt for a single job."""

from __future__ import annotations

from datetime import datetime, timezone

from app.models import Job


STATUS_LABELS = {
    "pending": "排队中",
    "running": "运行中",
    "success": "成功",
    "failed": "失败",
    "skipped": "已跳过",
}

# The three headline QC metrics, same as the detail-page metric cards.
METRIC_KEYS = ("reads", "mean_quality", "n_rate")


def _status_label(status: str | None) -> str:
    if not status:
        return "未知"
    return f"{STATUS_LABELS.get(status, status)} ({status})"


def _fmt_time(value: datetime | None) -> str:
    return value.isoformat() if value else "—"


def _fmt_metric(metrics: dict | None, key: str) -> str:
    if not metrics:
        return "—"
    value = metrics.get(key)
    return "—" if value is None else str(value)


def build_job_excerpt(job: Job) -> str:
    """Render the QC excerpt for ``job`` as plain text (issued by the backend)."""
    stages = sorted(job.stages, key=lambda s: s.stage_order)
    issued_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    lines = [
        "FASTQ 质控摘录（后端签发）",
        "=" * 40,
        f"签发时间: {issued_at}",
        "",
        f"作业编号: #{job.id}",
        f"作业状态: {_status_label(job.status)}",
        f"样例名称: {job.sample_name}",
        f"提交人: {job.created_by}",
        f"创建时间: {_fmt_time(job.created_at)}",
        f"完成时间: {_fmt_time(job.finished_at)}",
    ]
    if job.error_message:
        lines.append(f"失败原因: {job.error_message}")

    lines += ["", "三指标:"]
    for key in METRIC_KEYS:
        lines.append(f"  {key}: {_fmt_metric(job.metrics, key)}")

    lines += ["", f"Actor 阶段（共 {len(stages)} 个）:"]
    for stage in stages:
        lines.append(
            f"  {stage.stage_order + 1}. {stage.actor_name}"
            f"  {_status_label(stage.status)}  {stage.message or '—'}"
        )

    return "\n".join(lines) + "\n"
