"""Task templates, batch assignment, class-task copy and student groups (M3/M11 engineering checks).

Synthetic accounts and classes in temporary databases only. Permissions reuse the existing class
ownership checks: every batch validates all target classes before anything is created, and a
group-scoped assignment is invisible to classmates outside the group.
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


def student_login(api, store, username):
    """Change a just-issued student password and return the post-change auth header."""
    from app.roster import temporary_password

    password = temporary_password()
    store.reset_password(username, password)
    first = login(api, username, password)
    changed = api.client.post("/api/auth/password", headers=first,
                              json={"current_password": password, "new_password": f"St-{username}-2026"}).json()
    return {"Authorization": "Bearer " + changed["token"]}


# --- templates -----------------------------------------------------------------

def test_template_crud_is_teacher_scoped_and_validates_the_case(env):
    api, store = env
    teacher = login(api, "teacher", PASSWORD)
    other = login(api, "other", PASSWORD)

    created = api.client.post("/api/teacher/task-templates", headers=teacher,
                              json={"name": "腹痛问诊标准任务", "case_public_id": "CASE-002", "note": "重点鉴别"})
    assert created.status_code == 200, created.text
    template = created.json()
    assert template["case"]["available"] is True and template["title"] == "腹痛一天的青年男性模拟患者"
    assert api.client.post("/api/teacher/task-templates", headers=teacher,
                           json={"name": "坏病例", "case_public_id": "CASE-404"}).status_code == 422

    listed = api.client.get("/api/teacher/task-templates", headers=teacher).json()
    assert [t["id"] for t in listed] == [template["id"]]
    # Another teacher has no templates and cannot delete someone else's.
    assert api.client.get("/api/teacher/task-templates", headers=other).json() == []
    assert api.client.delete(f"/api/teacher/task-templates/{template['id']}", headers=other).status_code == 404
    assert api.client.delete(f"/api/teacher/task-templates/{template['id']}", headers=teacher).json() == {"deleted": True}
    assert api.client.get("/api/teacher/task-templates", headers=teacher).json() == []


def test_template_batch_assign_creates_one_task_per_class_and_skips_duplicates(env):
    api, store = env
    teacher = login(api, "teacher", PASSWORD)
    class_a = make_class(api, teacher, "甲班")
    class_b = make_class(api, teacher, "乙班")
    template = api.client.post("/api/teacher/task-templates", headers=teacher,
                               json={"name": "肺炎问诊", "case_public_id": "CASE-003", "note": "问清气短"}).json()

    first = api.client.post(f"/api/teacher/task-templates/{template['id']}/assign", headers=teacher,
                            json={"class_ids": [class_a["id"], class_b["id"]], "due_date": "2026-12-31"}).json()
    assert [(r["class_id"], r["status"]) for r in first["results"]] == \
        [(class_a["id"], "created"), (class_b["id"], "created")]
    tasks_a = api.client.get(f"/api/teacher/classes/{class_a['id']}", headers=teacher).json()["assignments"]
    assert len(tasks_a) == 1 and tasks_a[0]["due_date"] == "2026-12-31" and tasks_a[0]["note"] == "问清气短"

    rerun = api.client.post(f"/api/teacher/task-templates/{template['id']}/assign", headers=teacher,
                            json={"class_ids": [class_a["id"], class_b["id"]]}).json()
    assert [r["status"] for r in rerun["results"]] == ["exists", "exists"]
    assert len(api.client.get(f"/api/teacher/classes/{class_a['id']}", headers=teacher).json()["assignments"]) == 1


def test_batch_assign_rejects_a_foreign_class_before_creating_anything(env):
    api, store = env
    teacher = login(api, "teacher", PASSWORD)
    other = login(api, "other", PASSWORD)
    own = make_class(api, teacher, "甲班")
    foreign = make_class(api, other, "李老师班")
    template = api.client.post("/api/teacher/task-templates", headers=teacher,
                               json={"name": "模板", "case_public_id": "CASE-002"}).json()

    response = api.client.post(f"/api/teacher/task-templates/{template['id']}/assign", headers=teacher,
                               json={"class_ids": [own["id"], foreign["id"]]})
    assert response.status_code == 404
    assert api.client.get(f"/api/teacher/classes/{own['id']}", headers=teacher).json()["assignments"] == []
    assert api.client.get(f"/api/teacher/classes/{foreign['id']}", headers=other).json()["assignments"] == []


# --- copy assignments across own classes ----------------------------------------

def test_copy_class_assignments_to_other_own_classes(env):
    api, store = env
    teacher = login(api, "teacher", PASSWORD)
    source = make_class(api, teacher, "上学期的班")
    target = make_class(api, teacher, "这学期的班")
    api.client.post(f"/api/teacher/classes/{source['id']}/assignments", headers=teacher,
                    json={"case_public_id": "CASE-002", "due_date": "2026-01-01", "note": "旧学期要求"})
    api.client.post(f"/api/teacher/classes/{source['id']}/assignments", headers=teacher,
                    json={"case_public_id": "CASE-001"})

    copied = api.client.post(f"/api/teacher/classes/{source['id']}/copy-assignments", headers=teacher,
                             json={"target_class_ids": [target["id"]], "due_date": "2026-12-31"}).json()
    assert [r["status"] for r in copied["results"]] == ["created", "created"]
    tasks = api.client.get(f"/api/teacher/classes/{target['id']}", headers=teacher).json()["assignments"]
    assert {t["case_public_id"] for t in tasks} == {"CASE-001", "CASE-002"}
    assert all(t["due_date"] == "2026-12-31" for t in tasks)

    # Copying again skips both cases instead of duplicating.
    rerun = api.client.post(f"/api/teacher/classes/{source['id']}/copy-assignments", headers=teacher,
                            json={"target_class_ids": [target["id"]]}).json()
    assert [r["status"] for r in rerun["results"]] == ["exists", "exists"]


def test_copy_skips_group_scoped_assignments_and_refuses_foreign_targets(env):
    api, store = env
    teacher = login(api, "teacher", PASSWORD)
    other = login(api, "other", PASSWORD)
    source = make_class(api, teacher, "源班", students=("s001",))
    foreign = make_class(api, other, "他人的班")
    group = api.client.post(f"/api/teacher/classes/{source['id']}/groups", headers=teacher,
                            json={"name": "第一组"}).json()
    member = api.client.get(f"/api/teacher/classes/{source['id']}", headers=teacher).json()["members"][0]
    api.client.put(f"/api/teacher/classes/{source['id']}/groups/{group['id']}/students/{member['id']}",
                   headers=teacher)
    api.client.post(f"/api/teacher/classes/{source['id']}/assignments", headers=teacher,
                    json={"case_public_id": "CASE-002", "group_id": group["id"]})
    api.client.post(f"/api/teacher/classes/{source['id']}/assignments", headers=teacher,
                    json={"case_public_id": "CASE-001"})

    assert api.client.post(f"/api/teacher/classes/{source['id']}/copy-assignments", headers=teacher,
                           json={"target_class_ids": [foreign["id"]]}).status_code == 404
    copied = api.client.post(f"/api/teacher/classes/{source['id']}/copy-assignments", headers=teacher,
                             json={"target_class_ids": [make_class(api, teacher, '备用班')["id"]]}).json()
    skipped = [r for r in copied["results"] if r.get("status") == "skipped_group_scoped"]
    assert skipped and skipped[0]["count"] == 1


# --- groups ----------------------------------------------------------------------

def test_group_lifecycle_membership_and_delete_protection(env):
    api, store = env
    teacher = login(api, "teacher", PASSWORD)
    other = login(api, "other", PASSWORD)
    klass = make_class(api, teacher, "临床2024级1班", students=("s2024001", "s2024002", "s2024003"))
    members = api.client.get(f"/api/teacher/classes/{klass['id']}", headers=teacher).json()["members"]
    by_name = {m["username"]: m["id"] for m in members}

    assert api.client.post(f"/api/teacher/classes/{klass['id']}/groups", headers=other,
                           json={"name": "越权组"}).status_code == 404
    group = api.client.post(f"/api/teacher/classes/{klass['id']}/groups", headers=teacher,
                            json={"name": "第一组"}).json()
    group_b = api.client.post(f"/api/teacher/classes/{klass['id']}/groups", headers=teacher,
                              json={"name": "第二组"}).json()

    # Only own-class students can join; joining the second group leaves the first.
    assert api.client.put(f"/api/teacher/classes/{klass['id']}/groups/{group['id']}/students/{by_name['s2024001']}",
                          headers=teacher).json() == {"added": True}
    assert api.client.put(f"/api/teacher/classes/{klass['id']}/groups/{group['id']}/students/{store.find('teacher')['id']}",
                          headers=teacher).status_code == 422
    detail = api.client.get(f"/api/teacher/classes/{klass['id']}", headers=teacher).json()
    assert detail["groups"][0]["member_ids"] == [by_name["s2024001"]]

    api.client.put(f"/api/teacher/classes/{klass['id']}/groups/{group_b['id']}/students/{by_name['s2024001']}",
                   headers=teacher)
    detail = api.client.get(f"/api/teacher/classes/{klass['id']}", headers=teacher).json()
    moved = {g["name"]: g["member_ids"] for g in detail["groups"]}
    assert moved["第一组"] == [] and moved["第二组"] == [by_name["s2024001"]]

    # A group carrying an active assignment cannot be deleted; archiving the task reopens deletion.
    api.client.post(f"/api/teacher/classes/{klass['id']}/assignments", headers=teacher,
                    json={"case_public_id": "CASE-002", "group_id": group_b["id"]})
    assert api.client.delete(f"/api/teacher/classes/{klass['id']}/groups/{group_b['id']}",
                             headers=teacher).status_code == 409
    api.client.delete(f"/api/teacher/classes/{klass['id']}/groups/{group['id']}", headers=teacher)
    assert api.client.delete(f"/api/teacher/classes/{klass['id']}/groups/{group_b['id']}",
                             headers=teacher).status_code == 409


def test_group_scoped_assignment_is_invisible_outside_the_group(env):
    api, store = env
    teacher = login(api, "teacher", PASSWORD)
    klass = make_class(api, teacher, "临床2024级2班", students=("s301", "s302"))
    members = {m["username"]: m["id"] for m in
               api.client.get(f"/api/teacher/classes/{klass['id']}", headers=teacher).json()["members"]}
    group = api.client.post(f"/api/teacher/classes/{klass['id']}/groups", headers=teacher,
                            json={"name": "阑尾炎组"}).json()
    api.client.put(f"/api/teacher/classes/{klass['id']}/groups/{group['id']}/students/{members['s301']}",
                   headers=teacher)
    whole = api.client.post(f"/api/teacher/classes/{klass['id']}/assignments", headers=teacher,
                            json={"case_public_id": "CASE-001"}).json()
    scoped = api.client.post(f"/api/teacher/classes/{klass['id']}/assignments", headers=teacher,
                             json={"case_public_id": "CASE-002", "group_id": group["id"]}).json()

    in_group = student_login(api, store, "s301")
    out_group = student_login(api, store, "s302")
    mine = api.client.get("/api/assignments", headers=in_group).json()
    assert [(t["id"], t["status"]) for t in mine] == [(whole["id"], "not_started"), (scoped["id"], "not_started")]
    other = api.client.get("/api/assignments", headers=out_group).json()
    assert [t["id"] for t in other] == [whole["id"]]
    assert api.client.post(f"/api/assignments/{scoped['id']}/start", headers=out_group).status_code == 404
    started = api.client.post(f"/api/assignments/{scoped['id']}/start", headers=in_group)
    assert started.status_code == 200

    detail = api.client.get(f"/api/teacher/classes/{klass['id']}", headers=teacher).json()
    scoped_view = next(t for t in detail["assignments"] if t["id"] == scoped["id"])
    assert scoped_view["group"] == "阑尾炎组"
    assert set(scoped_view["progress"]) == {members["s301"]}
    whole_view = next(t for t in detail["assignments"] if t["id"] == whole["id"])
    assert set(whole_view["progress"]) == {members["s301"], members["s302"]}


def test_assignment_with_a_foreign_group_is_rejected(env):
    api, store = env
    teacher = login(api, "teacher", PASSWORD)
    klass_a = make_class(api, teacher, "一班", students=("s401",))
    klass_b = make_class(api, teacher, "二班")
    group_b = api.client.post(f"/api/teacher/classes/{klass_b['id']}/groups", headers=teacher,
                              json={"name": "二班的组"}).json()
    response = api.client.post(f"/api/teacher/classes/{klass_a['id']}/assignments", headers=teacher,
                               json={"case_public_id": "CASE-002", "group_id": group_b["id"]})
    assert response.status_code == 422
    assert api.client.get(f"/api/teacher/classes/{klass_a['id']}", headers=teacher).json()["assignments"] == []
