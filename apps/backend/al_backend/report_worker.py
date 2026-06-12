from __future__ import annotations

import logging
import os
import signal
import socket
import time
from dataclasses import dataclass

from .container import BackendContainer
from .settings import load_settings


LOGGER = logging.getLogger("al_backend.report_worker")


@dataclass(frozen=True)
class ReportWorkerConfig:
    poll_interval_seconds: float
    lease_seconds: int
    max_attempts: int
    batch_limit: int


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()

    if not raw:
        return default

    return float(raw)


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()

    if not raw:
        return default

    return int(raw)


def load_report_worker_config() -> ReportWorkerConfig:
    return ReportWorkerConfig(
        poll_interval_seconds=max(0.1, _float_env("AL_REPORT_WORKER_POLL_SECONDS", 1.0)),
        lease_seconds=max(30, _int_env("AL_REPORT_WORKER_LEASE_SECONDS", 300)),
        max_attempts=max(1, _int_env("AL_REPORT_WORKER_MAX_ATTEMPTS", 5)),
        batch_limit=max(1, _int_env("AL_REPORT_WORKER_BATCH_LIMIT", 1)),
    )


class ReportWorker:
    def __init__(self, container: BackendContainer, config: ReportWorkerConfig):
        self.container = container
        self.config = config
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}"
        self._stopping = False

    def stop(self, *_args: object) -> None:
        self._stopping = True

    def run_forever(self) -> None:
        LOGGER.info("Report worker started worker_id=%s", self.worker_id)

        while not self._stopping:
            processed = self.run_once()

            if processed == 0:
                time.sleep(self.config.poll_interval_seconds)

        LOGGER.info("Report worker stopped worker_id=%s", self.worker_id)

    def run_once(self) -> int:
        processed = 0

        for _ in range(self.config.batch_limit):
            report = self.container.report_ingest.claim_next_queued_report(
                worker_id=self.worker_id,
                lease_seconds=self.config.lease_seconds,
                max_attempts=self.config.max_attempts,
            )

            if not report:
                break

            ok = self.container.report_ingest.process_claimed_report(
                report,
                max_attempts=self.config.max_attempts,
            )
            processed += 1

            if ok:
                LOGGER.info("Processed queued report report_id=%s", report.get("_id"))
            else:
                LOGGER.warning("Failed queued report report_id=%s", report.get("_id"))

        return processed


def main() -> None:
    logging.basicConfig(level=os.getenv("AL_LOG_LEVEL", "INFO"))
    container = BackendContainer(load_settings())
    container.indexes.ensure_indexes()
    worker = ReportWorker(container, load_report_worker_config())
    signal.signal(signal.SIGTERM, worker.stop)
    signal.signal(signal.SIGINT, worker.stop)

    try:
        worker.run_forever()
    finally:
        container.close()


if __name__ == "__main__":
    main()
