from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class TrustedRunContext:
    owner_id: str
    session_id: str
    case_id: str
    case_version: int
    allowed_actions: frozenset[str]


_context: ContextVar[TrustedRunContext | None] = ContextVar("trusted_run_context", default=None)


def bind(context: TrustedRunContext):
    return _context.set(context)


def require_context() -> TrustedRunContext:
    context = _context.get()
    if context is None:
        raise PermissionError("missing protected tool context")
    return context


def check_session(session_id: str) -> TrustedRunContext:
    context = require_context()
    if context.session_id != session_id:
        raise PermissionError("session does not match protected run context")
    return context
