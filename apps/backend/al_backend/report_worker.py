from __future__ import annotations

import logging
import os
import signal
import socket
import threading
import time
from dataclasses import dataclass
from typing import Callable

from .container import BackendContainer
from .settings import load_settings


LOGGER = logging.getLogger("al_backend.report_worker")


@dataclass(frozen=True)
class ReportWorkerConfig:
    poll_interval_seconds: float
    lease_seconds: int
    max_attempts: int
    batch_limit: int
    concurrency: int = 1


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
        concurrency=max(1, _int_env("AL_REPORT_WORKER_CONCURRENCY", 4)),
    )


class ReportWorker:
    def __init__(
        self,
        container: BackendContainer,
        config: ReportWorkerConfig,
        container_factory: Callable[[], BackendContainer] | None = None,
    ):
        self.container = container
        self.config = config
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}"
        self.container_factory = container_factory
        self._stopping = False

    def stop(self, *_args: object) -> None:
        self._stopping = True

    def run_forever(self) -> None:
        LOGGER.info("Report worker started worker_id=%s concurrency=%s", self.worker_id, self.config.concurrency)

        if self.config.concurrency > 1 and self.container_factory is not None:
            self._run_pool_forever()
            return

        while not self._stopping:
            processed = self.run_once()

            if processed == 0:
                time.sleep(self.config.poll_interval_seconds)

        LOGGER.info("Report worker stopped worker_id=%s", self.worker_id)

    def _run_pool_forever(self) -> None:
        threads = [
            threading.Thread(target=self._run_lane_worker, args=(index + 1,), name=f"al-report-lane-{index + 1}", daemon=True)
            for index in range(self.config.concurrency)
        ]
        for thread in threads:
            thread.start()

        while not self._stopping:
            time.sleep(self.config.poll_interval_seconds)

        for thread in threads:
            thread.join(timeout=self.config.lease_seconds)
        LOGGER.info("Report worker stopped worker_id=%s", self.worker_id)

    def _run_lane_worker(self, lane_index: int) -> None:
        container = self.container_factory() if self.container_factory is not None else self.container
        lane_worker_id = f"{self.worker_id}:lane-{lane_index}"
        LOGGER.info("Report worker lane started worker_id=%s", lane_worker_id)

        try:
            while not self._stopping:
                processed = self._run_once_with_container(container, lane_worker_id)

                if processed == 0:
                    time.sleep(self.config.poll_interval_seconds)
        finally:
            if container is not self.container:
                container.close()
            LOGGER.info("Report worker lane stopped worker_id=%s", lane_worker_id)

    def run_once(self) -> int:
        return self._run_once_with_container(self.container, self.worker_id)

    def _run_once_with_container(self, container: BackendContainer, worker_id: str) -> int:
        processed = 0

        for _ in range(self.config.batch_limit):
            report = container.report_ingest.claim_next_queued_report(
                worker_id=worker_id,
                lease_seconds=self.config.lease_seconds,
                max_attempts=self.config.max_attempts,
            )

            if not report:
                break

            ok = container.report_ingest.process_claimed_report(
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
    settings = load_settings()
    container = BackendContainer(settings)
    container.indexes.ensure_indexes()
    worker = ReportWorker(container, load_report_worker_config(), container_factory=lambda: BackendContainer(settings))
    signal.signal(signal.SIGTERM, worker.stop)
    signal.signal(signal.SIGINT, worker.stop)

    try:
        worker.run_forever()
    finally:
        container.close()


if __name__ == "__main__":
    main()
