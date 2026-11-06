from ..models import ScoreSnapshot, SessionState
from ..scoring import score_session
from .context import check_session


def score_stage(session_id: str, state: SessionState, case, stage: str) -> dict:
    check_session(session_id)
    snapshot: ScoreSnapshot = score_session(state, case)
    return {"stage": stage, "items": [item.model_dump() for item in snapshot.items if item.stage == stage]}


def score_ddx(session_id: str, candidates: list[dict]) -> dict:
    check_session(session_id)
    return {
        "candidates": candidates,
        "coverage": len(candidates),
        "note": "候选覆盖不是鉴别诊断推理分，推理必须提供支持/反对证据和事件引用。",
    }
