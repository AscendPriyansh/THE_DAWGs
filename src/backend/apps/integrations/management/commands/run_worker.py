import os
import signal
import socket
import sys
import time
import uuid
import logging
from django.core.management.base import BaseCommand
from apps.integrations.delivery import claim_next_webhook_delivery, execute_webhook_delivery
from apps.integrations.jobs import claim_next_background_job, execute_background_job

logger = logging.getLogger("worker")


class Command(BaseCommand):
    help = "Run the shared persistent worker for background jobs and webhook deliveries"

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="Run all currently due deliveries and jobs once, then exit.",
        )
        parser.add_argument(
            "--worker-id",
            type=str,
            default="",
            help="Worker identifier used for lease acquisition.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=20,
            help="Maximum items to process per batch before polling pause.",
        )
        parser.add_argument(
            "--poll-interval",
            type=float,
            default=1.0,
            help="Seconds to sleep when idle.",
        )
        parser.add_argument(
            "--max-runs",
            type=int,
            default=0,
            help="Maximum number of loop iterations (0 for infinite).",
        )

    def handle(self, *args, **options):
        run_once = options["once"]
        poll_interval = options["poll_interval"]
        max_runs = options["max_runs"]
        worker_id = options["worker_id"] or f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"

        self.stdout.write(self.style.SUCCESS(f"Starting worker [{worker_id}] (run_once={run_once})..."))

        running = True

        def handle_shutdown(signum, frame):
            nonlocal running
            self.stdout.write(self.style.WARNING(f"\nReceived signal {signum}, gracefully shutting down..."))
            running = False

        signal.signal(signal.SIGINT, handle_shutdown)
        signal.signal(signal.SIGTERM, handle_shutdown)

        iterations = 0
        total_deliveries = 0
        total_jobs = 0

        while running:
            iterations += 1
            work_done = False

            # 1. Process due webhook deliveries
            delivery = claim_next_webhook_delivery(worker_id)
            if delivery:
                work_done = True
                total_deliveries += 1
                self.stdout.write(f"Executing webhook delivery {delivery.id} to {delivery.endpoint.url}...")
                success = execute_webhook_delivery(str(delivery.id), worker_id)
                self.stdout.write(f"Webhook delivery {delivery.id} completed: success={success}")

            # 2. Process queued background jobs
            job = claim_next_background_job(worker_id)
            if job:
                work_done = True
                total_jobs += 1
                self.stdout.write(f"Executing background job {job.id} [{job.kind}]...")
                success = execute_background_job(str(job.id), worker_id)
                self.stdout.write(f"Background job {job.id} completed: success={success}")

            if run_once:
                if not work_done:
                    break
            else:
                if not work_done:
                    time.sleep(poll_interval)

            if max_runs > 0 and iterations >= max_runs:
                break

        self.stdout.write(
            self.style.SUCCESS(
                f"Worker [{worker_id}] finished. Processed {total_deliveries} deliveries, {total_jobs} jobs."
            )
        )
