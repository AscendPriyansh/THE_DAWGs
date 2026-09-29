import hashlib
import json
import logging
from datetime import timedelta
from typing import Optional, Dict, Any, Callable

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.core.exceptions import ValidationError

from apps.events.models import Event, EventMembership
from apps.integrations.models import BackgroundJob
from apps.integrations.outbox import publish_domain_event
from apps.audit.models import AuditEvent

logger = logging.getLogger(__name__)

# Registry of job runners
JOB_HANDLERS: Dict[str, Callable[[BackgroundJob], Dict[str, Any]]] = {}


def register_job_handler(kind: str, handler: Callable[[BackgroundJob], Dict[str, Any]]):
    JOB_HANDLERS[kind] = handler


def default_generic_handler(job: BackgroundJob) -> Dict[str, Any]:
    """Default simulated execution for registered background job kinds."""
    job.progress_total = 100
    job.progress_current = 100
    job.save(update_fields=["progress_total", "progress_current"])
    return {
        "status": "completed",
        "result_key": f"jobs/{job.id}/output.bin",
    }


# Register default handlers for known kinds
for k in BackgroundJob.Kind.values:
    register_job_handler(k, default_generic_handler)


def export_event_job_handler(job: BackgroundJob) -> Dict[str, Any]:
    if not job.event:
        return default_generic_handler(job)
    import os
    from django.conf import settings
    from apps.imports.portable_archive import PortableArchiveExporter

    exporter = PortableArchiveExporter(event=job.event, exporting_user=job.requested_by)
    zip_bytes, sha256_hex = exporter.export_to_bytes()

    media_root = getattr(settings, "MEDIA_ROOT", "/tmp")
    out_dir = os.path.join(media_root, "exports")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{job.id}.zip")
    with open(out_path, "wb") as f:
        f.write(zip_bytes)

    job.result_storage_key = f"exports/{job.id}.zip"
    job.result_sha256 = sha256_hex
    job.progress_current = 100
    job.progress_total = 100
    job.save(update_fields=["result_storage_key", "result_sha256", "progress_current", "progress_total"])
    return {
        "status": "completed",
        "result_key": job.result_storage_key,
        "sha256": sha256_hex,
    }


register_job_handler(BackgroundJob.Kind.EXPORT_EVENT, export_event_job_handler)


def submit_background_job(
    event: Optional[Event],
    requested_by,
    kind: str,
    parameters: Optional[Dict[str, Any]] = None,
) -> BackgroundJob:
    """
    Submits a background job to the shared queue.
    """
    if kind not in BackgroundJob.Kind.values:
        raise ValidationError(f"Invalid job kind '{kind}'. Must be one of {BackgroundJob.Kind.values}")

    params = parameters or {}
    # Parameter bytes SHA-256
    param_bytes = json.dumps(params, sort_keys=True).encode("utf-8")
    input_sha256 = hashlib.sha256(param_bytes).hexdigest()

    job = BackgroundJob.objects.create(
        event=event,
        requested_by=requested_by,
        kind=kind,
        parameters_json=params,
        input_sha256=input_sha256,
        state=BackgroundJob.State.QUEUED,
    )

    if event:
        AuditEvent.objects.create(
            event=event,
            actor_user=requested_by,
            actor_kind=AuditEvent.ActorKind.USER,
            action="JOB_ENQUEUED",
            entity_type="BackgroundJob",
            entity_id=job.id,
            after_json={"job_id": str(job.id), "kind": kind},
            reason="Background job submitted",
        )

    return job


def claim_next_background_job(
    worker_id: str,
    lease_duration_seconds: int = 60,
) -> Optional[BackgroundJob]:
    """
    Claims the next queued background job using SELECT ... FOR UPDATE SKIP LOCKED.
    """
    now = timezone.now()
    with transaction.atomic():
        job = (
            BackgroundJob.objects.select_for_update(of=("self",), skip_locked=True)
            .filter(
                (
                    Q(state=BackgroundJob.State.QUEUED)
                    & (Q(lease_until__isnull=True) | Q(lease_until__lte=now))
                )
                | (
                    Q(state=BackgroundJob.State.RUNNING)
                    & Q(lease_until__lte=now)
                )
            )
            .order_by("created_at")
            .first()
        )
        if not job:
            return None

        job.state = BackgroundJob.State.RUNNING
        job.lease_owner = worker_id
        job.lease_until = now + timedelta(seconds=lease_duration_seconds)
        job.attempts += 1
        job.save(update_fields=["state", "lease_owner", "lease_until", "attempts"])
        return job


def execute_background_job(job_id: str, worker_id: str) -> bool:
    """
    Executes a claimed background job.
    Rechecks actor permissions before performing work.
    """
    try:
        job = BackgroundJob.objects.select_related("event", "requested_by").get(id=job_id)
    except BackgroundJob.DoesNotExist:
        return False

    # 1. Authority recheck: user must still have active organiser permission on event if job is event-scoped
    if job.event:
        membership = EventMembership.objects.filter(
            event=job.event,
            user=job.requested_by,
            role=EventMembership.Role.ORGANISER,
        ).first()

        if not membership and not getattr(job.requested_by, "is_superuser", False):
            logger.warning(
                "Requester %s lacks organiser role on event %s. Aborting job %s.",
                job.requested_by,
                job.event,
                job.id,
            )
            with transaction.atomic():
                j = BackgroundJob.objects.select_for_update().filter(id=job_id).first()
                if j and j.lease_owner == worker_id:
                    j.state = BackgroundJob.State.FAILED
                    j.error_code = "PERMISSION_REVOKED"
                    j.error_details = "Requester organiser authority was revoked before execution."
                    j.finished_at = timezone.now()
                    j.lease_owner = None
                    j.lease_until = None
                    j.save()
            return False

    # 2. Dispatch job handler
    handler = JOB_HANDLERS.get(job.kind, default_generic_handler)
    try:
        result = handler(job)
        success = True
        error_code = None
        error_details = None
        storage_key = result.get("result_key")
        result_sha256 = result.get("result_sha256")
    except Exception as e:
        logger.exception("Background job %s failed with exception: %s", job.id, e)
        success = False
        error_code = "EXECUTION_ERROR"
        error_details = str(e)
        storage_key = None
        result_sha256 = None

    # 3. Finalize in database if lease still intact
    now = timezone.now()
    with transaction.atomic():
        j = BackgroundJob.objects.select_for_update().filter(id=job_id).first()
        if not j:
            return False

        if j.lease_owner != worker_id or (j.lease_until and now > j.lease_until):
            logger.warning("Lease expired or stolen for background job %s", job_id)
            return False

        if success:
            j.state = BackgroundJob.State.SUCCEEDED
            j.result_storage_key = storage_key
            j.result_sha256 = result_sha256
            j.finished_at = now
            j.lease_owner = None
            j.lease_until = None
            j.save()

            if j.event:
                publish_domain_event(
                    event=j.event,
                    event_type="job.completed",
                    entity_id=str(j.id),
                    payload={"job_id": str(j.id), "kind": j.kind, "state": "SUCCEEDED"},
                )
            return True
        else:
            j.state = BackgroundJob.State.FAILED
            j.error_code = error_code
            j.error_details = error_details[:2000] if error_details else None
            j.finished_at = now
            j.lease_owner = None
            j.lease_until = None
            j.save()
            return False
