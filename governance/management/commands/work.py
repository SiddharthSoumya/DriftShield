import logging
import signal
import threading
import time

import httpx
from django.core.management.base import BaseCommand
from django.db import close_old_connections

from governance.github import GitHub
from governance.worker import check_overdue, deliver_one, heartbeat, prune, scan_one, weekly_digests

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Run the durable scan and email worker."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        stop = threading.Event()
        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, lambda *_: stop.set())
        next_prune = next_overdue = 0
        with httpx.Client(timeout=httpx.Timeout(8, connect=4), follow_redirects=False, trust_env=False) as client:
            github = GitHub(client)
            while not stop.is_set():
                try:
                    close_old_connections()
                    heartbeat()
                    delivered = deliver_one()
                    scanned = scan_one(github)
                    if time.monotonic() >= next_overdue:
                        check_overdue()
                        next_overdue = time.monotonic() + 60
                    if time.monotonic() >= next_prune:
                        prune()
                        weekly_digests()
                        next_prune = time.monotonic() + 3600
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
