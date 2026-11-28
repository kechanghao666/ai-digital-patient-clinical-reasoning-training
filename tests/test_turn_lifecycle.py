"""Turn replacement and expiry must hold across independent SQLite readers."""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import sqlite3
from threading import Event

import pytest

from app.integration import turns


def issue(store, **overrides):
    values = dict(kind=turns.PATIENT, owner_id="demo-owner", session_id="session",
                  case_id="case", case_version=1, request_id="request", state_version=0)
    values.update(overrides)
    return store.issue(**values)


def test_reader_never_sees_half_replaced_turn(tmp_path, monkeypatch):
    store = turns.TurnStore(tmp_path / "turns.db")
    reader = turns.TurnStore(store.path)
    old, _ = issue(store)
    before_insert, release = Event(), Event()
    original = store._connect

    @contextmanager
    def paused_connection():
        with original() as connection:
            def pause(sql):
                if sql.startswith("INSERT INTO turns("):
                    before_insert.set()
                    release.wait(5)
            connection.set_trace_callback(pause)
            yield connection

    monkeypatch.setattr(store, "_connect", paused_connection)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(issue, store, request_id="replacement")
        try:
            assert before_insert.wait(5)
            observed = reader.get(old.turn_id).status
        finally:
            release.set()
        replacement, _ = pending.result(timeout=5)
    assert observed == turns.OPEN
    assert reader.get(old.turn_id).status == turns.CANCELLED
    assert reader.get(replacement.turn_id).status == turns.OPEN


def test_failed_replacement_keeps_existing_turn_usable(tmp_path):
    store = turns.TurnStore(tmp_path / "turns.db")
    old, token = issue(store)
    with sqlite3.connect(store.path) as connection:
        connection.execute("CREATE TRIGGER reject_replacement BEFORE INSERT ON turns "
                           "BEGIN SELECT RAISE(ABORT, 'injected write failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        issue(store, request_id="replacement")
    assert store.resolve(token, kind=turns.PATIENT).turn_id == old.turn_id


@pytest.mark.parametrize("finished_at, accepted", [(104.0, True), (105.0, True), (106.0, False)])
def test_finalization_rejects_expired_submissions(tmp_path, monkeypatch, finished_at, accepted):
    store = turns.TurnStore(tmp_path / "turns.db")
    turn, _ = issue(store, now=100.0, ttl_s=5.0)
    monkeypatch.setattr(turns.time, "time", lambda: 101.0)
    assert store.submit(turn.turn_id, {"fact_ids": []})
    monkeypatch.setattr(turns.time, "time", lambda: finished_at)
    assert store.finalize(turn.turn_id) is accepted


def test_boundary_write_failure_rolls_back_selection_and_retry_records_once(tmp_path):
    store = turns.TurnStore(tmp_path / 'turns.db')
    turn, _ = issue(store)
    with sqlite3.connect(store.path) as db:
        db.execute("CREATE TRIGGER reject_sample BEFORE INSERT ON turn_boundaries BEGIN SELECT RAISE(ABORT, 'sample'); END")
    with pytest.raises(sqlite3.IntegrityError):
        store.submit(turn.turn_id, {"fact_ids": []}, observe_patient_submit=True)
    assert store.get(turn.turn_id).status == turns.OPEN
    assert store.get(turn.turn_id).submission is None and store.boundaries(turn.turn_id) == []
    with sqlite3.connect(store.path) as db:
        db.execute('DROP TRIGGER reject_sample')
    assert store.submit(turn.turn_id, {"fact_ids": []}, observe_patient_submit=True)
    assert not store.submit(turn.turn_id, {"fact_ids": []}, observe_patient_submit=True)
    sample, = store.boundaries(turn.turn_id)
    assert (sample['semantic'], sample['request_id']) == ('submit_fact_selection_before_commit', turn.request_id)
