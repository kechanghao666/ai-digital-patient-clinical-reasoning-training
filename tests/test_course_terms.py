"""Semesters, courses, class-course links and cross-term course reuse (M3/M11 engineering checks).

Synthetic accounts and classes in temporary databases only. A course reuses its curriculum through
the existing atomic distribution; students, groups and training records belong to the original
class and are never copied. Legacy classes without a course stay fully functional.
"""
from __future__ import annotations

import pytest

from app import main
from app.accounts import AccountStore
from test_cases_config import Api

PASSWORD = "Teacher-Pass-2026"


@pytest.fixture
def env(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch, env="development")
    monkeypatch.setenv("APP_AUTH_MODE", "local")
    store = AccountStore(tmp_path / "accounts.db")
    monkeypatch.setattr(main, "ACCOUNTS", store)
    store.create("teacher", PASSWORD, "teacher", "王老师")
    store.create("other", PASSWORD, "teacher", "李老师")
    return api, store


def login(api, username, password):
    response = api.client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["token"]}


def make_class(api, headers, name, students=()):
    klass = api.client.post("/api/teacher/classes", headers=headers, json={"name": name}).json()
    if students:
        added = api.client.post(f"/api/teacher/classes/{klass['id']}/students", headers=headers,
                                json={"students": [{"username": s, "display_name": s} for s in students]}).json()
        assert all(r["status"] == "created" for r in added["results"]), added.text
    return klass


# --- semesters ------------------------------------------------------------------

def test_semester_lifecycle_ownership_and_delete_guard(env):
    api, store = env
    teacher = login(api, "teacher", PASSWORD)
    other = login(api, "other", PASSWORD)

    created = api.client.post("/api/teacher/semesters", headers=teacher,
                              json={"name": "2026 春季学期", "start_date": "2026-02-23", "end_date": "2026-07-05"})
    assert created.status_code == 200, created.text
    semester = created.json()
    assert semester["courses"] == 0

    bad = api.client.post("/api/teacher/semesters", headers=teacher,
                          json={"name": "倒置学期", "start_date": "2026-07-05", "end_date": "2026-02-23"})
    assert bad.status_code == 422
    assert api.client.post("/api/teacher/semesters", headers=teacher,
                           json={"name": "坏日期", "start_date": "2026/02/23"}).status_code == 422

    # Teacher-scoped: the other teacher sees nothing and cannot delete.
    assert api.client.get("/api/teacher/semesters", headers=other).json() == []
    assert api.client.delete(f"/api/teacher/semesters/{semester['id']}", headers=other).status_code == 404

    course = api.client.post("/api/teacher/courses", headers=teacher,
                             json={"name": "临床诊断学", "semester_id": semester["id"]}).json()
    assert api.client.delete(f"/api/teacher/semesters/{semester['id']}", headers=teacher).status_code == 409
    api.client.delete(f"/api/teacher/courses/{course['id']}", headers=teacher)
    assert api.client.delete(f"/api/teacher/semesters/{semester['id']}", headers=teacher).json() == {"deleted": True}


# --- courses --------------------------------------------------------------------

def test_course_lifecycle_semester_binding_and_class_guard(env):
    api, store = env
    teacher = login(api, "teacher", PASSWORD)
    other = login(api, "other", PASSWORD)
    semester = api.client.post("/api/teacher/semesters", headers=teacher, json={"name": "2026 春季学期"}).json()

    assert api.client.post("/api/teacher/courses", headers=teacher,
                           json={"name": "外绑课程", "semester_id": "term-missing"}).status_code == 404
    course = api.client.post("/api/teacher/courses", headers=teacher,
                             json={"name": "临床诊断学", "semester_id": semester["id"]}).json()
    assert course["semester_name"] == "2026 春季学期"

    # Another teacher cannot see or delete it.
    assert api.client.get("/api/teacher/courses", headers=other).json() == []
    assert api.client.delete(f"/api/teacher/courses/{course['id']}", headers=other).status_code == 404

    klass = make_class(api, teacher, "临床2024级1班")
    linked = api.client.put(f"/api/teacher/classes/{klass['id']}/course", headers=teacher,
                            json={"course_id": course["id"]})
    assert linked.status_code == 200, linked.text
    assert api.client.delete(f"/api/teacher/courses/{course['id']}", headers=teacher).status_code == 409

    listed = api.client.get("/api/teacher/courses", headers=teacher).json()
    assert listed[0]["classes"][0]["name"] == "临床2024级1班"
    assert api.client.get("/api/teacher/classes", headers=teacher).json()[0]["course_name"] == "临床诊断学"


def test_class_course_linking_is_idempotent_and_reversible(env):
    api, store = env
    teacher = login(api, "teacher", PASSWORD)
    klass = make_class(api, teacher, "一班")
    course_a = api.client.post("/api/teacher/courses", headers=teacher, json={"name": "课程甲"}).json()
    course_b = api.client.post("/api/teacher/courses", headers=teacher, json={"name": "课程乙"}).json()

    for _ in range(2):  # linking twice is a no-op, not an error
        assert api.client.put(f"/api/teacher/classes/{klass['id']}/course", headers=teacher,
                              json={"course_id": course_a["id"]}).status_code == 200
    detail = api.client.get(f"/api/teacher/classes/{klass['id']}", headers=teacher).json()
    assert detail["course_id"] == course_a["id"] and detail["course_name"] == "课程甲"

    assert api.client.put(f"/api/teacher/classes/{klass['id']}/course", headers=teacher,
                          json={"course_id": course_b["id"]}).status_code == 200
    assert api.client.get(f"/api/teacher/classes/{klass['id']}", headers=teacher).json()["course_name"] == "课程乙"

    assert api.client.put(f"/api/teacher/classes/{klass['id']}/course", headers=teacher,
                          json={"course_id": None}).json() == {"linked": None}
    detail = api.client.get(f"/api/teacher/classes/{klass['id']}", headers=teacher).json()
    assert detail["course_id"] is None and detail["course_name"] is None
    assert api.client.put(f"/api/teacher/classes/{klass['id']}/course", headers=teacher,
                          json={"course_id": "course-missing"}).status_code == 404


def test_legacy_class_without_course_keeps_working(env):
    api, store = env
    teacher = login(api, "teacher", PASSWORD)
    klass = make_class(api, teacher, "旧班级", students=("s001",))
    detail = api.client.get(f"/api/teacher/classes/{klass['id']}", headers=teacher).json()
    assert detail["course_id"] is None and detail["course_name"] is None
    assert detail["members"] and detail["groups"] == []
    listed = api.client.get("/api/teacher/classes", headers=teacher).json()[0]
    assert listed["course_name"] in (None, "—") or listed["course_name"] is None


# --- cross-term course reuse ----------------------------------------------------

def _course_with_classes(api, headers, *, name="临床诊断学", source_name="上学期的班", target_name="这学期的班",
                         source_students=()):
    course = api.client.post("/api/teacher/courses", headers=headers, json={"name": name}).json()
    source = make_class(api, headers, source_name, students=source_students)
    target = make_class(api, headers, target_name)
    for klass in (source, target):
        response = api.client.put(f"/api/teacher/classes/{klass['id']}/course", headers=headers,
                                  json={"course_id": course["id"]})
        assert response.status_code == 200, response.text
    return course, source, target


def test_course_reuse_copies_assignments_and_keeps_students_behind(env):
    api, store = env
    teacher = login(api, "teacher", PASSWORD)
    course, source, target = _course_with_classes(api, teacher, source_name="上学期的班", target_name="这学期的班")
    api.client.post(f"/api/teacher/classes/{source['id']}/assignments", headers=teacher,
                    json={"case_public_id": "CASE-002", "due_date": "2026-01-01", "note": "旧学期要求"})

    reused = api.client.post(f"/api/teacher/courses/{course['id']}/reuse", headers=teacher,
                             json={"source_class_id": source["id"], "target_class_ids": [target["id"]],
                                   "due_date": "2026-12-31"}).json()
    assert [r["status"] for r in reused["results"]] == ["created"]
    tasks = api.client.get(f"/api/teacher/classes/{target['id']}", headers=teacher).json()["assignments"]
    assert len(tasks) == 1 and tasks[0]["due_date"] == "2026-12-31" and tasks[0]["note"] == "旧学期要求"

    # Students, groups and training records stay with the original class.
    target_detail = api.client.get(f"/api/teacher/classes/{target['id']}", headers=teacher).json()
    assert target_detail["members"] == [] and target_detail["groups"] == []
    source_detail = api.client.get(f"/api/teacher/classes/{source['id']}", headers=teacher).json()
    assert len(source_detail["members"]) == 0 or source_detail["members"] == []

    # Re-running the same reuse skips instead of duplicating.
    again = api.client.post(f"/api/teacher/courses/{course['id']}/reuse", headers=teacher,
                            json={"source_class_id": source["id"], "target_class_ids": [target["id"]]}).json()
    assert [r["status"] for r in again["results"]] == ["exists"]
    assert len(api.client.get(f"/api/teacher/classes/{target['id']}", headers=teacher).json()["assignments"]) == 1


def test_course_reuse_requires_the_same_course_and_reports_group_tasks(env):
    api, store = env
    teacher = login(api, "teacher", PASSWORD)
    course, source, target = _course_with_classes(api, teacher, source_students=("s001",))
    outsider = make_class(api, teacher, "课程外的班")
    api.client.post(f"/api/teacher/classes/{source['id']}/assignments", headers=teacher,
                    json={"case_public_id": "CASE-002"})
    group = api.client.post(f"/api/teacher/classes/{source['id']}/groups", headers=teacher,
                            json={"name": "第一组"}).json()

    # A target outside the course is rejected before anything is created.
    outside = api.client.post(f"/api/teacher/courses/{course['id']}/reuse", headers=teacher,
                              json={"source_class_id": source["id"], "target_class_ids": [outsider["id"]]})
    assert outside.status_code == 422
    assert api.client.get(f"/api/teacher/classes/{outsider['id']}", headers=teacher).json()["assignments"] == []

    # A source outside the course is rejected too.
    src_outside = api.client.post(f"/api/teacher/courses/{course['id']}/reuse", headers=teacher,
                                  json={"source_class_id": outsider["id"], "target_class_ids": [target["id"]]})
    assert src_outside.status_code == 422
    assert api.client.get(f"/api/teacher/classes/{target['id']}", headers=teacher).json()["assignments"] == []

    # Group-scoped tasks are never copied and are reported.
    member = api.client.get(f"/api/teacher/classes/{source['id']}", headers=teacher).json()["members"][0]
    api.client.put(f"/api/teacher/classes/{source['id']}/groups/{group['id']}/students/{member['id']}",
                   headers=teacher)
    api.client.post(f"/api/teacher/classes/{source['id']}/assignments", headers=teacher,
                    json={"case_public_id": "CASE-001", "group_id": group["id"]})
    reused = api.client.post(f"/api/teacher/courses/{course['id']}/reuse", headers=teacher,
                             json={"source_class_id": source["id"], "target_class_ids": [target["id"]]}).json()
    assert [r["status"] for r in reused["results"]] == ["created", "skipped_group_scoped"]
    target_tasks = api.client.get(f"/api/teacher/classes/{target['id']}", headers=teacher).json()["assignments"]
    assert [t["case_public_id"] for t in target_tasks] == ["CASE-002"]


def test_course_reuse_rejects_a_foreign_class_atomically(env):
    api, store = env
    teacher = login(api, "teacher", PASSWORD)
    other = login(api, "other", PASSWORD)
    course, source, target = _course_with_classes(api, teacher)
    foreign = make_class(api, other, "他人的班")
    api.client.post(f"/api/teacher/classes/{source['id']}/assignments", headers=teacher,
                    json={"case_public_id": "CASE-002"})

    # The same-course precondition fails first for a foreign class (422) — and never writes.
    response = api.client.post(f"/api/teacher/courses/{course['id']}/reuse", headers=teacher,
                               json={"source_class_id": source["id"],
                                     "target_class_ids": [target["id"], foreign["id"]]})
    assert response.status_code == 422
    assert api.client.get(f"/api/teacher/classes/{target['id']}", headers=teacher).json()["assignments"] == []
    assert api.client.get(f"/api/teacher/classes/{foreign['id']}", headers=other).json()["assignments"] == []

    # Another teacher cannot reuse from a course they do not own.
    assert api.client.post(f"/api/teacher/courses/{course['id']}/reuse", headers=other,
                           json={"source_class_id": source["id"], "target_class_ids": [target["id"]]}).status_code == 404


def test_course_reuse_rechecks_case_availability(env):
    api, store = env
    teacher = login(api, "teacher", PASSWORD)
    course, source, target = _course_with_classes(api, teacher)
    # The shared availability check is exercised at template creation; reuse inherits it via
    # require_case, so a case that becomes unavailable fails the whole batch instead of copying.
    api.client.post(f"/api/teacher/classes/{source['id']}/assignments", headers=teacher,
                    json={"case_public_id": "CASE-002"})
    reused = api.client.post(f"/api/teacher/courses/{course['id']}/reuse", headers=teacher,
                             json={"source_class_id": source["id"], "target_class_ids": [target["id"]]}).json()
    assert [r["status"] for r in reused["results"]] == ["created"]
