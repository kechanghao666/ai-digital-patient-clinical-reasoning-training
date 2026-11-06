from __future__ import annotations

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

from .models import ActionRequest, ActionResponse
from .storage import ConflictError, SessionStore

app = FastAPI(title="AI Digital Patient Topic 1")
store = SessionStore()


def owner_from_header(x_owner: str | None) -> str:
    if not x_owner or not x_owner.strip():
        raise HTTPException(status_code=401, detail="缺少会话所有者")
    return x_owner.strip()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/sessions", status_code=201)
def create_session(x_owner: str | None = Header(default=None)) -> dict[str, str]:
    state = store.create(owner_from_header(x_owner))
    return {"session_id": state.session_id, "status": state.status}


@app.get("/sessions/{session_id}")
def get_session(session_id: str, x_owner: str | None = Header(default=None)) -> dict:
    owner = owner_from_header(x_owner)
    state = store.get(session_id)
    if state is None or state.owner != owner:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {
        "session_id": state.session_id,
        "status": state.status,
        "round": state.round,
        "score_status": state.score_status,
        "score": state.score,
        "events": [event.model_dump() for event in state.events[-5:]],
        "error": state.last_error,
    }


@app.post("/sessions/{session_id}/actions", response_model=ActionResponse)
def action(session_id: str, req: ActionRequest, x_owner: str | None = Header(default=None)) -> ActionResponse:
    owner = owner_from_header(x_owner)
    state = store.get(session_id)
    if state is None or state.owner != owner:
        raise HTTPException(status_code=404, detail="会话不存在")
    try:
        return store.apply(session_id, req)
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="会话不存在") from exc


@app.exception_handler(HTTPException)
async def http_exception_handler(_, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})

