"""Opening the feedback/turn stores from concurrent requests must not fail with "database is locked".

The review page loads score, adjudication and feedback in parallel right after sealing; each request
constructs a store on the same SQLite file.
"""
from __future__ import annotations

import threading

from app.integration.feedback import FeedbackStore
from app.integration.turns import TurnStore


def _hammer(factory, path, rounds: int = 6, workers: int = 8) -> list[BaseException]:
    errors: list[BaseException] = []
    barrier = threading.Barrier(workers)

    def run():
        try:
            barrier.wait()
            for _ in range(rounds):
                store = factory(path)
                with store._connect() as connection:
                    connection.execute("SELECT 1").fetchone()
        except BaseException as exc:  # noqa: BLE001 - surfaced through the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return errors


def test_feedback_store_survives_concurrent_construction(tmp_path):
    path = tmp_path / "integration.db"
    assert _hammer(FeedbackStore, path) == []
    with FeedbackStore(path)._connect() as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_turn_store_survives_concurrent_construction(tmp_path):
    assert _hammer(TurnStore, tmp_path / "integration.db") == []
