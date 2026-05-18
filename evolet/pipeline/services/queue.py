"""Queue-backed pipeline launch helpers."""

from __future__ import annotations

import logging
import threading

from django.utils import timezone

from ..models import PipelineRun

logger = logging.getLogger("pipeline")


def run_pipeline_job(run_id: str) -> None:
    from ..orchestrator import run_full_pipeline

    run = PipelineRun.objects.get(id=run_id)
    run_full_pipeline(run)


def enqueue_pipeline_run(run: PipelineRun) -> None:
    """
    Enqueue the pipeline into RQ when available, inline thread fallback otherwise.

    The queue path is the production default. The thread fallback remains as a
    local-dev safety valve when Redis is unavailable.
    """
    try:
        import django_rq

        queue = django_rq.get_queue("default")
        queue.enqueue(run_pipeline_job, str(run.id), job_timeout="8h")
        logger.info("Queued pipeline run %s through RQ", run.id)
        return
    except Exception as exc:
        logger.warning("RQ unavailable, falling back to daemon thread: %s", exc)

    def _worker() -> None:
        from ..orchestrator import run_full_pipeline

        try:
            run_full_pipeline(PipelineRun.objects.get(id=run.id))
        except Exception as worker_exc:
            fresh = PipelineRun.objects.get(id=run.id)
            fresh.status = PipelineRun.Status.FAILED
            fresh.error_message = str(worker_exc)
            fresh.completed_at = timezone.now()
            fresh.save(update_fields=["status", "error_message", "completed_at"])
            logger.exception("Pipeline run %s failed in fallback worker", run.id)

    threading.Thread(target=_worker, daemon=True).start()
