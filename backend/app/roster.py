"""Classes, student rosters and training assignments (local account mode).

Stored next to the accounts in accounts.db. Teachers manage only the classes they created; a
student sees assignments of the classes they belong to. Scores shown here are the original rule
scores of linked trainings, used for teaching follow-up, not formal grades.
"""
from __future__ import annotations

import re
import secrets
import time
import uuid
from datetime import date
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .accounts import AccountStore, auth_mode, normalize_username
from .models import SessionState

_CACHE: dict[str, "RosterStore"] = {}


def roster_for(accounts: AccountStore) -> "RosterStore":
    key = str(accounts.path)
    if key not in _CACHE or _CACHE[key].accounts is not accounts:
        _CACHE[key] = RosterStore(accounts)
    return _CACHE[key]


def temporary_password() -> str:
    # 16 characters without look-alike letters, easy to read out to a student once.
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(16))


class RosterStore:
    def __init__(self, accounts: AccountStore):
        self.accounts = accounts
        with accounts.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS classes (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, teacher_id TEXT NOT NULL REFERENCES accounts(id),
                    created_at REAL NOT NULL, archived INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS class_members (
                    class_id TEXT NOT NULL REFERENCES classes(id), account_id TEXT NOT NULL REFERENCES accounts(id),
                    added_at REAL NOT NULL, PRIMARY KEY (class_id, account_id)
                );
                CREATE TABLE IF NOT EXISTS assignments (
                    id TEXT PRIMARY KEY, class_id TEXT NOT NULL REFERENCES classes(id), case_public_id TEXT NOT NULL,
                    title TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', due_date TEXT, created_by TEXT NOT NULL,
                    created_at REAL NOT NULL, archived INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS assignment_sessions (
                    assignment_id TEXT NOT NULL REFERENCES assignments(id), account_id TEXT NOT NULL,
                    session_id TEXT NOT NULL UNIQUE, started_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS class_groups (
                    id TEXT PRIMARY KEY, class_id TEXT NOT NULL REFERENCES classes(id),
                    name TEXT NOT NULL, created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS class_group_members (
                    group_id TEXT NOT NULL REFERENCES class_groups(id), account_id TEXT NOT NULL,
                    added_at REAL NOT NULL, PRIMARY KEY (group_id, account_id)
                );
                CREATE TABLE IF NOT EXISTS task_templates (
                    id TEXT PRIMARY KEY, teacher_id TEXT NOT NULL REFERENCES accounts(id),
                    name TEXT NOT NULL, case_public_id TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL,
                    archived INTEGER NOT NULL DEFAULT 0
                );
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(assignments)")}
            if "group_id" not in columns:
                # Group-scoped assignments are optional; existing rows stay whole-class. Deliberately
                # no foreign key: deleting a group must not be blocked by archived history, and a
                # stale group id on an archived task only loses its display label.
                db.execute("ALTER TABLE assignments ADD COLUMN group_id TEXT")

    # ------------------------------------------------------------ classes
    def owned_class(self, class_id: str, teacher_id: str):
        with self.accounts.connect() as db:
            row = db.execute("SELECT * FROM classes WHERE id=? AND teacher_id=?", (class_id, teacher_id)).fetchone()
        if not row:
            raise KeyError(class_id)
        return dict(row)

    def classes(self, teacher_id: str) -> list[dict]:
        with self.accounts.connect() as db:
            rows = db.execute("""
                SELECT c.*, (SELECT COUNT(*) FROM class_members m WHERE m.class_id=c.id) AS students,
                       (SELECT COUNT(*) FROM assignments a WHERE a.class_id=c.id AND a.archived=0) AS assignments
                FROM classes c WHERE c.teacher_id=? ORDER BY c.archived, c.created_at DESC""", (teacher_id,)).fetchall()
        return [dict(row) | {"archived": bool(row["archived"])} for row in rows]

    def create_class(self, teacher_id: str, name: str) -> dict:
        class_id = "class-" + uuid.uuid4().hex[:12]
        with self.accounts.connect() as db:
            db.execute("INSERT INTO classes VALUES (?,?,?,?,0)", (class_id, name.strip(), teacher_id, time.time()))
        return self.owned_class(class_id, teacher_id)

    def update_class(self, class_id: str, teacher_id: str, name: str | None, archived: bool | None) -> dict:
        self.owned_class(class_id, teacher_id)
        with self.accounts.connect() as db:
            if name is not None:
                db.execute("UPDATE classes SET name=? WHERE id=?", (name.strip(), class_id))
            if archived is not None:
                db.execute("UPDATE classes SET archived=? WHERE id=?", (int(archived), class_id))
        return self.owned_class(class_id, teacher_id)

    def member_ids(self, class_id: str) -> list[str]:
        with self.accounts.connect() as db:
            return [r[0] for r in db.execute("SELECT account_id FROM class_members WHERE class_id=? ORDER BY added_at",
                                             (class_id,))]

    def members(self, class_id: str) -> list[dict]:
        with self.accounts.connect() as db:
            rows = db.execute("""SELECT a.id, a.username, a.display_name, a.disabled, a.must_change_password
                FROM class_members m JOIN accounts a ON a.id=m.account_id WHERE m.class_id=? ORDER BY a.username""",
                              (class_id,)).fetchall()
        return [{"id": r["id"], "username": r["username"], "display_name": r["display_name"],
                 "disabled": bool(r["disabled"]), "must_change_password": bool(r["must_change_password"])} for r in rows]

    def teaches(self, teacher_id: str, account_id: str) -> bool:
        with self.accounts.connect() as db:
            return db.execute("""SELECT 1 FROM class_members m JOIN classes c ON c.id=m.class_id
                WHERE c.teacher_id=? AND m.account_id=?""", (teacher_id, account_id)).fetchone() is not None

    def add_students(self, class_id: str, teacher_id: str, students: list[dict]) -> list[dict]:
        """Create new students, or reuse only students already taught by this teacher."""
        self.owned_class(class_id, teacher_id)
        results = []
        for item in students:
            try:
                username = normalize_username(item["username"])
            except ValueError as exc:
                results.append({"username": item["username"], "status": "rejected", "message": str(exc)})
                continue
            display = (item.get("display_name") or username).strip()
            existing = self.accounts.find(username)
            password = None
            if existing is None:
                password = temporary_password()
                try:
                    account_id = self.accounts.create(
                        username, password, "student", display, must_change=True,
                        actor_id=teacher_id, entry="teacher_api",
                    )
                except ValueError as exc:
                    # Another request may create this username after find(). Never adopt that
                    # account or return this request's unused generated password to the caller.
                    results.append({"username": username, "status": "rejected", "message": str(exc)})
                    continue
                status = "created"
            elif existing["role"] != "student":
                results.append({"username": username, "status": "rejected", "message": "该账号不是学生账号"})
                continue
            else:
                account_id = existing["id"]
                status = "enrolled"
            with self.accounts.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                if existing is not None and not db.execute("""SELECT 1 FROM class_members m
                        JOIN classes c ON c.id=m.class_id WHERE c.teacher_id=? AND m.account_id=?""",
                        (teacher_id, account_id)).fetchone():
                    results.append({"username": username, "status": "rejected",
                                    "message": "该已有学生账号不属于当前教师，不能加入班级"})
                    continue
                db.execute("INSERT OR IGNORE INTO class_members VALUES (?,?,?)", (class_id, account_id, time.time()))
            results.append({"username": username, "display_name": display, "status": status,
                            "temporary_password": password})
        return results

    def remove_student(self, class_id: str, teacher_id: str, account_id: str) -> None:
        self.owned_class(class_id, teacher_id)
        with self.accounts.connect() as db:
            db.execute("DELETE FROM class_members WHERE class_id=? AND account_id=?", (class_id, account_id))

    # -------------------------------------------------------------- groups
    def groups(self, class_id: str) -> list[dict]:
        with self.accounts.connect() as db:
            rows = db.execute("SELECT * FROM class_groups WHERE class_id=? ORDER BY created_at", (class_id,)).fetchall()
            members = db.execute("""SELECT m.group_id, m.account_id FROM class_group_members m
                JOIN class_groups g ON g.id=m.group_id WHERE g.class_id=? ORDER BY m.added_at""", (class_id,)).fetchall()
        by_group: dict[str, list[str]] = {}
        for row in members:
            by_group.setdefault(row["group_id"], []).append(row["account_id"])
        return [{"id": row["id"], "name": row["name"], "member_ids": by_group.get(row["id"], [])}
                for row in rows]

    def group_of(self, class_id: str, group_id: str) -> dict | None:
        for group in self.groups(class_id):
            if group["id"] == group_id:
                return group
        return None

    def create_group(self, class_id: str, teacher_id: str, name: str) -> dict:
        self.owned_class(class_id, teacher_id)
        group_id = "group-" + uuid.uuid4().hex[:12]
        with self.accounts.connect() as db:
            db.execute("INSERT INTO class_groups VALUES (?,?,?,?)", (group_id, class_id, name.strip(), time.time()))
        return {"id": group_id, "name": name.strip(), "member_ids": []}

    def delete_group(self, class_id: str, teacher_id: str, group_id: str) -> None:
        self.owned_class(class_id, teacher_id)
        if self.group_of(class_id, group_id) is None:
            raise KeyError(group_id)
        with self.accounts.connect() as db:
            active = db.execute("SELECT COUNT(*) FROM assignments WHERE group_id=? AND archived=0", (group_id,)).fetchone()[0]
            if active:
                raise ValueError("该分组仍有进行中的任务；先撤回任务或换成分组后再删除")
            db.execute("DELETE FROM class_group_members WHERE group_id=?", (group_id,))
            db.execute("DELETE FROM class_groups WHERE id=?", (group_id,))

    def set_group_member(self, class_id: str, teacher_id: str, group_id: str, account_id: str, member: bool) -> None:
        """Add or remove one student in a group; a student belongs to at most one group per class."""
        self.owned_class(class_id, teacher_id)
        if self.group_of(class_id, group_id) is None:
            raise KeyError(group_id)
        with self.accounts.connect() as db:
            if member:
                if not db.execute("SELECT 1 FROM class_members WHERE class_id=? AND account_id=?",
                                  (class_id, account_id)).fetchone():
                    raise ValueError("该账号不是本班学生，不能加入分组")
                # One group per class: leaving the previous group is part of the same write.
                db.execute("""DELETE FROM class_group_members WHERE account_id=? AND group_id IN
                    (SELECT id FROM class_groups WHERE class_id=?)""", (account_id, class_id))
                db.execute("INSERT OR IGNORE INTO class_group_members VALUES (?,?,?)", (group_id, account_id, time.time()))
            else:
                db.execute("DELETE FROM class_group_members WHERE group_id=? AND account_id=?", (group_id, account_id))

    # ------------------------------------------------------------ templates
    def create_template(self, teacher_id: str, name: str, case_public_id: str, title: str, note: str) -> dict:
        template_id = "tpl-" + uuid.uuid4().hex[:12]
        with self.accounts.connect() as db:
            db.execute("INSERT INTO task_templates VALUES (?,?,?,?,?,?,?,0)",
                       (template_id, teacher_id, name.strip(), case_public_id, title.strip(), note.strip(), time.time()))
        return self.template(template_id, teacher_id)

    def template(self, template_id: str, teacher_id: str) -> dict:
        with self.accounts.connect() as db:
            row = db.execute("SELECT * FROM task_templates WHERE id=? AND teacher_id=? AND archived=0",
                             (template_id, teacher_id)).fetchone()
        if not row:
            raise KeyError(template_id)
        return dict(row)

    def templates(self, teacher_id: str) -> list[dict]:
        with self.accounts.connect() as db:
            rows = db.execute("""SELECT * FROM task_templates WHERE teacher_id=? AND archived=0
                ORDER BY created_at DESC""", (teacher_id,)).fetchall()
        return [dict(row) for row in rows]

    def delete_template(self, template_id: str, teacher_id: str) -> None:
        self.template(template_id, teacher_id)
        with self.accounts.connect() as db:
            db.execute("UPDATE task_templates SET archived=1 WHERE id=?", (template_id,))

    # -------------------------------------------------------- assignments
    def create_assignment(self, class_id: str, teacher_id: str, case_public_id: str, title: str,
                          note: str, due_date: str | None, group_id: str | None = None) -> dict:
        self.owned_class(class_id, teacher_id)
        assignment_id = "task-" + uuid.uuid4().hex[:12]
        with self.accounts.connect() as db:
            db.execute("INSERT INTO assignments (id, class_id, case_public_id, title, note, due_date, "
                       "created_by, created_at, archived, group_id) VALUES (?,?,?,?,?,?,?,?,0,?)",
                       (assignment_id, class_id, case_public_id, title.strip(), note.strip(), due_date,
                        teacher_id, time.time(), group_id))
        return self.assignment(assignment_id)

    def _active_case_exists(self, class_id: str, case_public_id: str) -> bool:
        with self.accounts.connect() as db:
            return db.execute("SELECT 1 FROM assignments WHERE class_id=? AND case_public_id=? AND archived=0",
                              (class_id, case_public_id)).fetchone() is not None

    def assign_template(self, template_id: str, teacher_id: str, class_ids: list[str],
                        due_date: str | None) -> list[dict]:
        """Assign one template to several own classes; every class is validated before any insert.

        A class that already has an active assignment for the same case is reported as "exists"
        instead of being duplicated, so re-running a batch never creates copy storms.
        """
        template = self.template(template_id, teacher_id)
        for class_id in class_ids:
            self.owned_class(class_id, teacher_id)  # raises KeyError before anything is created
        results = []
        for class_id in class_ids:
            if self._active_case_exists(class_id, template["case_public_id"]):
                results.append({"class_id": class_id, "status": "exists",
                                "message": "该班已有此病例的进行中任务，未重复布置"})
                continue
            task = self.create_assignment(class_id, teacher_id, template["case_public_id"],
                                          template["title"] or template["name"], template["note"], due_date)
            results.append({"class_id": class_id, "status": "created", "assignment_id": task["id"]})
        return results

    def copy_class_assignments(self, source_class_id: str, teacher_id: str, target_class_ids: list[str],
                               due_date: str | None) -> list[dict]:
        """Copy the whole-class assignments of one own class into other own classes.

        Group-scoped assignments are deliberately not copied: group membership is per class and a
        silent copy would point at groups that do not exist in the target. They are reported back
        as skipped instead. Every target class is validated before any insert.
        """
        self.owned_class(source_class_id, teacher_id)
        for class_id in target_class_ids:
            self.owned_class(class_id, teacher_id)
        results = []
        skipped_grouped = 0
        for task in self.class_assignments(source_class_id):
            if task.get("group_id"):
                skipped_grouped += 1
                continue
            for class_id in target_class_ids:
                if self._active_case_exists(class_id, task["case_public_id"]):
                    results.append({"class_id": class_id, "case_public_id": task["case_public_id"],
                                    "status": "exists", "message": "该班已有此病例的进行中任务，未重复布置"})
                    continue
                created = self.create_assignment(class_id, teacher_id, task["case_public_id"], task["title"],
                                                 task["note"], due_date if due_date is not None else task["due_date"])
                results.append({"class_id": class_id, "case_public_id": task["case_public_id"],
                                "status": "created", "assignment_id": created["id"]})
        if skipped_grouped:
            results.append({"status": "skipped_group_scoped", "count": skipped_grouped,
                            "message": f"{skipped_grouped} 项分组任务未复制（分组按班级独立，不能跨班复制）"})
        return results

    def assignment(self, assignment_id: str) -> dict:
        with self.accounts.connect() as db:
            row = db.execute("SELECT * FROM assignments WHERE id=?", (assignment_id,)).fetchone()
        if not row:
            raise KeyError(assignment_id)
        return dict(row) | {"archived": bool(row["archived"])}

    def class_assignments(self, class_id: str) -> list[dict]:
        with self.accounts.connect() as db:
            rows = db.execute("SELECT * FROM assignments WHERE class_id=? AND archived=0 ORDER BY created_at DESC",
                              (class_id,)).fetchall()
        return [dict(r) | {"archived": False} for r in rows]

    def archive_assignment(self, assignment_id: str, teacher_id: str) -> None:
        task = self.assignment(assignment_id)
        self.owned_class(task["class_id"], teacher_id)
        with self.accounts.connect() as db:
            db.execute("UPDATE assignments SET archived=1 WHERE id=?", (assignment_id,))

    def student_assignments(self, account_id: str) -> list[dict]:
        with self.accounts.connect() as db:
            rows = db.execute("""SELECT a.*, c.name AS class_name FROM assignments a
                JOIN classes c ON c.id=a.class_id JOIN class_members m ON m.class_id=c.id
                WHERE m.account_id=? AND a.archived=0 AND c.archived=0
                  AND (a.group_id IS NULL OR EXISTS (
                      SELECT 1 FROM class_group_members g WHERE g.group_id=a.group_id AND g.account_id=?))
                ORDER BY a.due_date IS NULL, a.due_date, a.created_at""",
                              (account_id, account_id)).fetchall()
        return [dict(r) for r in rows]

    def linked_sessions(self, assignment_id: str, account_id: str | None = None) -> list[dict]:
        query, params = "SELECT * FROM assignment_sessions WHERE assignment_id=?", [assignment_id]
        if account_id:
            query += " AND account_id=?"
            params.append(account_id)
        with self.accounts.connect() as db:
            return [dict(r) for r in db.execute(query + " ORDER BY started_at", params)]

    def link(self, assignment_id: str, account_id: str, session_id: str) -> None:
        with self.accounts.connect() as db:
            db.execute("INSERT INTO assignment_sessions VALUES (?,?,?,?)", (assignment_id, account_id, session_id, time.time()))


def _progress(states: list[SessionState]) -> dict:
    """Status of one student's trainings for one assignment."""
    sealed = [s for s in states if s.sealed and s.score_snapshot]
    best = max((s.score_snapshot.total_score / s.score_snapshot.max_score for s in sealed
                if s.score_snapshot.max_score), default=None)
    status = "completed" if sealed else "in_progress" if states else "not_started"
    current = next((s.session_id for s in reversed(states) if not s.sealed), None)
    latest = states[-1].session_id if states else None
    return {"status": status, "best_ratio": round(best, 4) if best is not None else None, "attempts": len(states),
            "open_session_id": current, "latest_session_id": latest,
            "latest_sealed_session_id": sealed[-1].session_id if sealed else None,
            "needs_review": any(s.score_snapshot.status == "needs_review" for s in sealed)}


class ClassBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=60)


class ClassPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str | None = Field(default=None, min_length=1, max_length=60)
    archived: bool | None = None


class StudentRow(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    username: str = Field(min_length=1, max_length=64)
    display_name: str = Field(default="", max_length=80)


class StudentsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    students: list[StudentRow] = Field(min_length=1, max_length=300)


class AssignmentBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    case_public_id: str = Field(min_length=1, max_length=40)
    title: str = Field(default="", max_length=80)
    note: str = Field(default="", max_length=500)
    due_date: str | None = Field(default=None, max_length=10)
    group_id: str | None = Field(default=None, max_length=64)


class GroupBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=60)


class TemplateBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=60)
    case_public_id: str = Field(min_length=1, max_length=40)
    title: str = Field(default="", max_length=80)
    note: str = Field(default="", max_length=500)


class TemplateAssignBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    class_ids: list[str] = Field(min_length=1, max_length=50)
    due_date: str | None = Field(default=None, max_length=10)


class CopyAssignmentsBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    target_class_ids: list[str] = Field(min_length=1, max_length=50)
    due_date: str | None = Field(default=None, max_length=10)


def build_router(get_roster: Callable[[], RosterStore], get_accounts: Callable[[], AccountStore],
                 get_repository: Callable, get_cases: Callable, current_teacher: Callable, current_owner: Callable,
                 development: Callable[[], bool],
                 is_training_stopped: Callable[[str, int], bool] | None = None,
                 is_department_enabled: Callable[[Any], bool] | None = None) -> APIRouter:
    router = APIRouter(prefix="/api")

    def roster() -> RosterStore:
        if auth_mode() != "local":
            raise HTTPException(409, "班级与任务需要独立账号模式（APP_AUTH_MODE=local）")
        return get_roster()

    def owned(class_id: str, teacher_id: str) -> dict:
        try:
            return roster().owned_class(class_id, teacher_id)
        except KeyError as exc:
            raise HTTPException(404, "班级不存在或不属于当前教师") from exc

    def states_for(assignment_id: str, account_id: str) -> list[SessionState]:
        states = []
        for link in roster().linked_sessions(assignment_id, account_id):
            try:
                states.append(get_repository().get(link["session_id"], account_id))
            except KeyError:
                continue
        return states

    def case_title(public_id: str) -> dict:
        try:
            case = get_cases().resolve_for_student(public_id, include_unpublished=development())
        except KeyError:
            return {"title": "病例不可用", "specialty": "", "available": False,
                    "unavailable_reason": "missing"}
        stopped = is_training_stopped is not None and is_training_stopped(case.case_id, case.version)
        department_enabled = is_department_enabled is None or is_department_enabled(case)
        return {"title": case.title, "specialty": case.specialty, "chief_complaint": case.chief_complaint,
                "available": not stopped and department_enabled,
                "unavailable_reason": "stopped" if stopped else "department_disabled" if not department_enabled else None}

    # ---------------------------------------------------------------- teacher
    @router.get("/teacher/classes")
    def list_classes(teacher_id: str = Depends(current_teacher)) -> list[dict]:
        return roster().classes(teacher_id)

    @router.post("/teacher/classes")
    def create_class(body: ClassBody, teacher_id: str = Depends(current_teacher)) -> dict:
        return roster().create_class(teacher_id, body.name)

    @router.patch("/teacher/classes/{class_id}")
    def update_class(class_id: str, body: ClassPatch, teacher_id: str = Depends(current_teacher)) -> dict:
        owned(class_id, teacher_id)
        return roster().update_class(class_id, teacher_id, body.name, body.archived)

    @router.get("/teacher/classes/{class_id}")
    def class_detail(class_id: str, teacher_id: str = Depends(current_teacher)) -> dict:
        info = owned(class_id, teacher_id)
        store = roster()
        members = store.members(class_id)
        groups = store.groups(class_id)
        group_names = {g["id"]: g["name"] for g in groups}
        tasks = []
        for task in store.class_assignments(class_id):
            # A group-scoped assignment is counted over its group only; other classmates are not
            # part of the denominator and see nothing in their own task list.
            group_id = task.get("group_id")
            if group_id:
                audience_ids = next((set(g["member_ids"]) for g in groups if g["id"] == group_id), set())
            else:
                audience_ids = {m["id"] for m in members}
            audience = [m for m in members if m["id"] in audience_ids]
            progress = {m["id"]: _progress(states_for(task["id"], m["id"])) for m in audience}
            done = [p for p in progress.values() if p["status"] == "completed"]
            ratios = [p["best_ratio"] for p in done if p["best_ratio"] is not None]
            tasks.append({**task, "group": group_names.get(group_id) if group_id else None,
                          "case": case_title(task["case_public_id"]), "progress": progress,
                          "completed": len(done), "in_progress": sum(p["status"] == "in_progress" for p in progress.values()),
                          "average_ratio": round(sum(ratios) / len(ratios), 4) if ratios else None})
        sessions_by_owner = {m["id"]: get_repository().list_for_owner(m["id"]) for m in members}
        for member in members:
            sessions = sessions_by_owner[member["id"]]
            sealed = [s for s in sessions if s.sealed and s.score_snapshot and s.score_snapshot.max_score]
            member["trainings"] = len(sessions)
            member["sealed"] = len(sealed)
            member["average_ratio"] = round(sum(s.score_snapshot.total_score / s.score_snapshot.max_score for s in sealed)
                                            / len(sealed), 4) if sealed else None
        return {**info, "archived": bool(info["archived"]), "members": members, "groups": groups,
                "assignments": tasks}

    @router.post("/teacher/classes/{class_id}/students")
    def add_students(class_id: str, body: StudentsBody, teacher_id: str = Depends(current_teacher)) -> dict:
        owned(class_id, teacher_id)
        try:
            results = roster().add_students(class_id, teacher_id, [row.model_dump() for row in body.students])
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"results": results,
                "notice": "初始密码只显示这一次，请导出后分发；学生首次登录须修改密码。"}

    @router.delete("/teacher/classes/{class_id}/students/{account_id}")
    def remove_student(class_id: str, account_id: str, teacher_id: str = Depends(current_teacher)) -> dict:
        owned(class_id, teacher_id)
        roster().remove_student(class_id, teacher_id, account_id)
        return {"removed": True}

    def student_of(teacher_id: str, account_id: str) -> dict:
        if not roster().teaches(teacher_id, account_id):
            raise HTTPException(404, "学生不在你的班级中")
        return get_accounts().get(account_id)

    @router.post("/teacher/students/{account_id}/reset-password")
    def reset_password(account_id: str, teacher_id: str = Depends(current_teacher)) -> dict:
        student = student_of(teacher_id, account_id)
        password = temporary_password()
        get_accounts().issue_password(
            account_id, password, must_change=True, actor_id=teacher_id, entry="teacher_api",
        )
        return {"username": student["username"], "temporary_password": password,
                "notice": "新密码只显示这一次；该学生原有登录已失效，下次登录须修改密码。"}

    @router.post("/teacher/students/{account_id}/disable")
    def disable(account_id: str, teacher_id: str = Depends(current_teacher)) -> dict:
        student_of(teacher_id, account_id)
        changed = get_accounts().set_disabled(account_id, True, actor_id=teacher_id, entry="teacher_api")
        return {"disabled": True, "changed": changed}

    @router.post("/teacher/students/{account_id}/enable")
    def enable(account_id: str, teacher_id: str = Depends(current_teacher)) -> dict:
        student_of(teacher_id, account_id)
        changed = get_accounts().set_disabled(account_id, False, actor_id=teacher_id, entry="teacher_api")
        return {"disabled": False, "changed": changed}

    def validated_due_date(due_date: str | None) -> str | None:
        if not due_date:
            return None
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", due_date):
            raise HTTPException(422, "截止日期格式须为 YYYY-MM-DD")
        try:
            date.fromisoformat(due_date)
        except ValueError as exc:
            raise HTTPException(422, "截止日期无效") from exc
        return due_date

    def require_group(class_id: str, group_id: str | None) -> str | None:
        if not group_id:
            return None
        if roster().group_of(class_id, group_id) is None:
            raise HTTPException(422, "分组不存在或不属于该班级")
        return group_id

    @router.post("/teacher/classes/{class_id}/assignments")
    def create_assignment(class_id: str, body: AssignmentBody, teacher_id: str = Depends(current_teacher)) -> dict:
        owned(class_id, teacher_id)
        case = case_title(body.case_public_id)
        if not case["available"]:
            raise HTTPException(422, "病例不存在或当前不可用于训练")
        group_id = require_group(class_id, body.group_id)
        due_date = validated_due_date(body.due_date)
        return roster().create_assignment(class_id, teacher_id, body.case_public_id,
                                          body.title or case["title"], body.note, due_date, group_id)

    @router.delete("/teacher/assignments/{assignment_id}")
    def archive_assignment(assignment_id: str, teacher_id: str = Depends(current_teacher)) -> dict:
        try:
            roster().archive_assignment(assignment_id, teacher_id)
        except KeyError as exc:
            raise HTTPException(404, "任务不存在或不属于当前教师") from exc
        return {"archived": True}

    # ---------------------------------------------------------------- groups
    @router.post("/teacher/classes/{class_id}/groups")
    def create_group(class_id: str, body: GroupBody, teacher_id: str = Depends(current_teacher)) -> dict:
        owned(class_id, teacher_id)
        return roster().create_group(class_id, teacher_id, body.name)

    @router.delete("/teacher/classes/{class_id}/groups/{group_id}")
    def delete_group(class_id: str, group_id: str, teacher_id: str = Depends(current_teacher)) -> dict:
        owned(class_id, teacher_id)
        try:
            roster().delete_group(class_id, teacher_id, group_id)
        except KeyError as exc:
            raise HTTPException(404, "分组不存在或不属于该班级") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"deleted": True}

    @router.put("/teacher/classes/{class_id}/groups/{group_id}/students/{account_id}")
    def add_group_member(class_id: str, group_id: str, account_id: str,
                         teacher_id: str = Depends(current_teacher)) -> dict:
        owned(class_id, teacher_id)
        try:
            roster().set_group_member(class_id, teacher_id, group_id, account_id, True)
        except KeyError as exc:
            raise HTTPException(404, "分组不存在或不属于该班级") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"added": True}

    @router.delete("/teacher/classes/{class_id}/groups/{group_id}/students/{account_id}")
    def remove_group_member(class_id: str, group_id: str, account_id: str,
                            teacher_id: str = Depends(current_teacher)) -> dict:
        owned(class_id, teacher_id)
        try:
            roster().set_group_member(class_id, teacher_id, group_id, account_id, False)
        except KeyError as exc:
            raise HTTPException(404, "分组不存在或不属于该班级") from exc
        return {"removed": True}

    # ------------------------------------------------------------- templates
    @router.get("/teacher/task-templates")
    def list_templates(teacher_id: str = Depends(current_teacher)) -> list[dict]:
        return [{**template, "case": case_title(template["case_public_id"])}
                for template in roster().templates(teacher_id)]

    @router.post("/teacher/task-templates")
    def create_template(body: TemplateBody, teacher_id: str = Depends(current_teacher)) -> dict:
        case = case_title(body.case_public_id)
        if not case["available"]:
            raise HTTPException(422, "病例不存在或当前不可用于训练")
        return {**roster().create_template(teacher_id, body.name, body.case_public_id,
                                           body.title or case["title"], body.note),
                "case": case}

    @router.delete("/teacher/task-templates/{template_id}")
    def delete_template(template_id: str, teacher_id: str = Depends(current_teacher)) -> dict:
        try:
            roster().delete_template(template_id, teacher_id)
        except KeyError as exc:
            raise HTTPException(404, "模板不存在或不属于当前教师") from exc
        return {"deleted": True}

    @router.post("/teacher/task-templates/{template_id}/assign")
    def assign_template(template_id: str, body: TemplateAssignBody,
                        teacher_id: str = Depends(current_teacher)) -> dict:
        store = roster()
        due_date = validated_due_date(body.due_date)
        for class_id in body.class_ids:
            owned(class_id, teacher_id)  # every target is checked before anything is created
        try:
            results = store.assign_template(template_id, teacher_id, body.class_ids, due_date)
        except KeyError as exc:
            raise HTTPException(404, "模板不存在或不属于当前教师") from exc
        names = {cid: owned(cid, teacher_id)["name"] for cid in body.class_ids}
        results = [({**result, "class_name": names[result["class_id"]]} if "class_id" in result else result)
                   for result in results]
        return {"results": results, "notice": "按班级逐个布置；已有同病例进行中任务的班级未重复布置。"}

    @router.post("/teacher/classes/{class_id}/copy-assignments")
    def copy_assignments(class_id: str, body: CopyAssignmentsBody,
                         teacher_id: str = Depends(current_teacher)) -> dict:
        store = roster()
        due_date = validated_due_date(body.due_date)
        names = {cid: owned(cid, teacher_id)["name"] for cid in body.target_class_ids}  # all targets first
        try:
            results = store.copy_class_assignments(class_id, teacher_id, body.target_class_ids, due_date)
        except KeyError as exc:
            raise HTTPException(404, "班级不存在或不属于当前教师") from exc
        results = [({**result, "class_name": names[result["class_id"]]} if "class_id" in result else result)
                   for result in results]
        return {"results": results,
                "notice": "整班任务按病例复制；分组任务不跨班复制，已有同病例任务的班级跳过。"}

    # ---------------------------------------------------------------- student
    @router.get("/assignments")
    def my_assignments(owner_id: str = Depends(current_owner)) -> list[dict]:
        if auth_mode() != "local":
            return []
        result = []
        for task in roster().student_assignments(owner_id):
            result.append({**task, "case": case_title(task["case_public_id"]),
                           **_progress(states_for(task["id"], owner_id))})
        return result

    @router.post("/assignments/{assignment_id}/start")
    def start_assignment(assignment_id: str, owner_id: str = Depends(current_owner)) -> dict:
        store = roster()
        task = next((t for t in store.student_assignments(owner_id) if t["id"] == assignment_id), None)
        if task is None:
            raise HTTPException(404, "任务不存在或你不在该班级")
        progress = _progress(states_for(assignment_id, owner_id))
        if progress["open_session_id"]:
            return {"session_id": progress["open_session_id"], "resumed": True}
        try:
            case = get_cases().resolve_for_student(task["case_public_id"], include_unpublished=development())
        except KeyError as exc:
            raise HTTPException(404, "病例当前不可用于训练") from exc
        if is_training_stopped is not None and is_training_stopped(case.case_id, case.version):
            # The assignment follows the newest published version; never fall back to an older
            # published version when that newest one has been stopped.
            raise HTTPException(409, "该病例版本已停止接受新训练，请联系教师重新选版")
        if is_department_enabled is not None and not is_department_enabled(case):
            raise HTTPException(409, "该病例所属科室或亚专科未启用，暂不可开始新训练")
        state = SessionState(owner_id=owner_id, case_id=case.case_id, case_version=case.version)
        get_repository().create(state)
        store.link(assignment_id, owner_id, state.session_id)
        return {"session_id": state.session_id, "resumed": False}

    return router
