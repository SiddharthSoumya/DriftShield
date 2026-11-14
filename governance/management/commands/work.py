import logging
import signal
import threading
import time

import httpx
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from django.utils import timezone

from governance.github import GitHub
from governance.worker import check_overdue, deliver_one, enqueue_mail_canary, heartbeat, prune, scan_one, weekly_digests
from governance.models import Workspace
from governance.pilot import activation_required
from infrastructure.domain import check_overdue as check_infrastructure
from infrastructure.domain import expire_exceptions
from infrastructure.pilot_intake import check_pilot_gaps

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Run the durable scan and email worker."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        stop = threading.Event()
        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, lambda *_: stop.set())
        if settings.DS_PILOT_EBS and settings.DS_PILOT_RECOVERY_FENCED:
            if not options["once"]:
                stop.wait()
            return
        next_prune = next_overdue = 0
        with httpx.Client(timeout=httpx.Timeout(8, connect=4), follow_redirects=False, trust_env=False) as client:
            github = GitHub(client)
            while not stop.is_set():
                try:
                    close_old_connections()
                    if settings.DS_PILOT_EBS:
                        workspace_id = Workspace.objects.filter(slug=settings.DS_PILOT_WORKSPACE).values_list("pk", flat=True).first()
                        if activation_required(workspace_id):
                            if options["once"]:
                                raise CommandError("Pilot activation has not been approved.")
                            stop.wait(5)
                            continue
                        enqueue_mail_canary(timezone.now())
                    delivered = deliver_one()
                    heartbeat("email")
                    scanned = not settings.DS_PILOT_EBS and scan_one(github)
                    if not settings.DS_PILOT_EBS:
                        heartbeat("github")
                    if time.monotonic() >= next_overdue:
                        now = timezone.now()
                        if settings.DS_PILOT_EBS:
                            check_pilot_gaps(now)
                        else:
                            check_overdue()
                            check_infrastructure(now)
                            expire_exceptions(now)
                        heartbeat("freshness")
                        next_overdue = time.monotonic() + 60
                    if time.monotonic() >= next_prune:
                        prune()
                        if not settings.DS_PILOT_EBS:
                            weekly_digests()
                        heartbeat("retention")
                        next_prune = time.monotonic() + 3600
                    heartbeat()
                except Exception:
                    if options["once"]:
                        raise
                    logger.exception("Worker iteration failed")
                    stop.wait(5)
                else:
                    if options["once"]:
                        return
                    if not delivered and not scanned:
                        stop.wait(5)
