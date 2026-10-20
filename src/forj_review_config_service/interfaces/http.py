"""Thin HTTP command and event boundary for the standalone local service."""

from fastapi import APIRouter, Request

from forj_review_config_service.application.conversation import chat_messages
from forj_review_config_service.contracts.models import (
    BrowserResultCommand,
    CaptureCommand,
    CaseSetCommand,
    ConfirmationCommand,
    CreateSession,
    EditCommand,
    InteractionCommand,
    MessageCommand,
    RefreshPageCommand,
    RevisionCommand,
)

router = APIRouter(prefix="/api/review-config")


@router.post("/captures")
async def capture(command: CaptureCommand, request: Request):
    return await request.app.state.service.capture(command)


@router.get("/captures")
async def captures(request: Request):
    return await request.app.state.service.store.list_captures()


@router.get("/captures/{capture_id}")
async def captured_page(capture_id: str, request: Request):
    return await request.app.state.service.store.get_capture(capture_id)


@router.delete("/captures/{capture_id}")
async def delete_capture(capture_id: str, request: Request):
    (await request.app.state.service.store.delete_capture(capture_id))
    return {"ok": True}


@router.get("/sessions")
async def sessions(request: Request):
    return await request.app.state.service.store.list()


@router.post("/sessions")
async def create(command: CreateSession, request: Request):
    return await request.app.state.service.create(command)


@router.get("/capabilities")
async def capabilities(request: Request):
    return await request.app.state.service.kernel.capabilities()


@router.get("/templates")
async def templates(request: Request):
    return await request.app.state.service.kernel.context()


@router.post("/case-sets")
async def register_cases(command: CaseSetCommand, request: Request):
    return await request.app.state.service.kernel.register_cases(command.case_set_id, command.suite)


@router.get("/sessions/{sid}")
@router.get("/sessions/{sid}/draft")
async def session(sid: str, request: Request):
    record = await request.app.state.service.store.get(sid)
    return {**record.model_dump(mode="json"), "chat_messages": chat_messages(record)}


@router.get("/sessions/{sid}/events")
async def events(sid: str, request: Request, after_seq: int = 0):
    (await request.app.state.service.store.get(sid))
    return await request.app.state.service.store.events(sid, after_seq)


@router.post("/sessions/{sid}/messages")
async def message(sid: str, command: MessageCommand, request: Request):
    return await request.app.state.service.command(sid, "message", command)


@router.post("/sessions/{sid}/draft")
async def edit(sid: str, command: EditCommand, request: Request):
    return await request.app.state.service.command(sid, "edit", command)


@router.post("/sessions/{sid}/page")
async def refresh_page(sid: str, command: RefreshPageCommand, request: Request):
    return await request.app.state.service.command(sid, "page", command)


@router.post("/sessions/{sid}/interactions/{interaction_id}/answer")
async def answer(sid: str, interaction_id: str, command: InteractionCommand, request: Request):
    return await request.app.state.service.command(sid, "answer", command, interaction_id)


@router.post("/sessions/{sid}/browser-tasks/{task_id}/result")
async def browser_result(sid: str, task_id: str, command: BrowserResultCommand, request: Request):
    return await request.app.state.service.command(sid, "browser", command, task_id)


@router.post("/sessions/{sid}/confirm")
async def confirm(sid: str, command: ConfirmationCommand, request: Request):
    return await request.app.state.service.command(sid, "confirm", command)


@router.post("/sessions/{sid}/resume")
async def resume(sid: str, command: RevisionCommand, request: Request):
    return await request.app.state.service.command(sid, "resume", command)


@router.post("/sessions/{sid}/cancel")
async def cancel(sid: str, request: Request):
    await request.app.state.service.cancel(sid)
    return {"ok": True}


@router.post("/sessions/{sid}/validate")
async def validate(sid: str, command: RevisionCommand, request: Request):
    return await request.app.state.service.validate(sid, command)


@router.get("/sessions/{sid}/activation")
async def activation(sid: str, request: Request):
    return await request.app.state.service.activation(sid)


@router.delete("/sessions/{sid}")
async def delete(sid: str, request: Request):
    await request.app.state.service.delete(sid)
    return {"ok": True}
