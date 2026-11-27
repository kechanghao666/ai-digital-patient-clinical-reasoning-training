"""Synthetic credential records for isolated tests; never proof of medical review."""
from app.accounts import AccountStore

SYNTHETIC_REVIEWER = "模拟审核（测试夹具，非真实医学审核）"


def activate_synthetic_qualification(store, username, department="04", subspecialty="04.01"):
    qualification_id = store.create_reviewer_qualification(
        username, department, subspecialty, "2020-01-01", "2099-12-31",
    )
    store.set_reviewer_qualification_status(
        qualification_id, "active", confirmed_external_verification=True,
    )
    return qualification_id


def bind_synthetic_demo_reviewer(tmp_path, monkeypatch):
    from app import main
    store = AccountStore(tmp_path / "synthetic-reviewers.db")
    with store.connect() as db:
        db.execute("""INSERT INTO accounts (id,username,display_name,role,salt,password_hash)
                      VALUES (?,?,?,'teacher',?,?)""",
                   ("teacher-wang", "teacher-wang", SYNTHETIC_REVIEWER, b"fixture", b"unusable"))
    activate_synthetic_qualification(store, "teacher-wang")
    monkeypatch.setattr(main, "ACCOUNTS", store)
    return store
