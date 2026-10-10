"""Bring the nndrei.dev form's mailbox into the app. `--loop` is what the inbox-sync service runs."""
import logging
import signal
import threading
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections


class Command(BaseCommand):
    help = 'Sincroniza las consultas de nndrei.dev (buzón SQLite de portfolio-api) y avisa por Telegram.'

    def add_arguments(self, parser):
        parser.add_argument('--loop', action='store_true', help='Repetir cada INQUIRY_SYNC_INTERVAL segundos hasta recibir SIGTERM.')

    def handle(self, *args, loop=False, **options):
        from core.inquiry_sync import log, notify_pending, sync_once
        logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
        stop = threading.Event()
        if loop:
            signal.signal(signal.SIGTERM, lambda *_: stop.set())
            signal.signal(signal.SIGINT, lambda *_: stop.set())
            log.info('Sincronización de consultas cada %ss.', settings.INQUIRY_SYNC_INTERVAL)
        while True:
            close_old_connections()
            try:
                sync_once()
                notify_pending()
            except Exception as error:
                # One bad cycle (database restarting, disk full) must not end the service.
                log.error('Ciclo de sincronización fallido (%s).', type(error).__name__)
                close_old_connections()
            if not loop or stop.wait(settings.INQUIRY_SYNC_INTERVAL):
                break
