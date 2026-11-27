"""Explicit class memberships for the isolated, non-login demo test identities."""
from app import main
from app.accounts import AccountStore
from app.roster import roster_for


def bind_demo_class(tmp_path, monkeypatch, teachers, students):
    store = AccountStore(tmp_path / "scope-accounts.db")
    monkeypatch.setattr(main, "ACCOUNTS", store)
    # These fixture accounts intentionally have no usable password. Their only purpose is to
    # make the pre-existing demo identities obey exactly the same roster checks as real users.
    with store.connect() as db:
        for role, ids in (("teacher", teachers), ("student", students)):
            for account_id in ids:
                db.execute("""INSERT OR IGNORE INTO accounts
                    (id,username,display_name,role,salt,password_hash) VALUES (?,?,?,?,?,?)""",
                    (account_id, account_id, account_id, role, b"fixture", b"unusable"))
    roster = roster_for(store)
    for teacher in teachers:
        klass = roster.create_class(teacher, "合成测试班级")
        # Trusted test seeding, not an enrollment API shortcut available to a teacher.
        with store.connect() as db:
            db.executemany("INSERT INTO class_members VALUES (?,?,0)",
                           [(klass["id"], student) for student in students])
    return roster
