"""Thin HTTP command and event boundary for the standalone local service."""

from fastapi import APIRouter, Query, Request

from forj_review_config_service.application.handoff import export_handoff
from forj_review_config_service.contracts.models import (
    AdoptionCommand,
    BrowserResultCommand,
    CaptureCommand,
    CaseSetCommand,
    ConfirmationCommand,
    CreateTask,
    EditCommand,
    InteractionCommand,
    LocalReportCommand,
    MappingCommand,
    MessageCommand,
    RebaseCommand,
    ReferenceCommand,
    RefreshPageCommand,
    ReplayCommand,
    RevisionCommand,
    SplitCommand,
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
    await request.app.state.service.store.delete_capture(capture_id)
    return {"ok": True}


@router.get("/tasks")
async def sessions(request: Request):
    return await request.app.state.service.list()


@router.post("/tasks")
async def create(command: CreateTask, request: Request):
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


@router.get("/case-sets")
async def read_cases(case_set_id: str, request: Request):
    return await request.app.state.service.kernel.read_cases(case_set_id)


@router.get("/tasks/{sid}")
@router.get("/tasks/{sid}/draft")
async def session(sid: str, request: Request):
    return await request.app.state.service.detail(sid)


@router.get("/tasks/{sid}/events")
async def events(
    sid: str,
    request: Request,
    after_seq: int = Query(0, ge=0),
    before_seq: int | None = Query(None, ge=0),
    limit: int = Query(200, ge=1, le=200),
    include_messages: bool = True,
):
    await request.app.state.service.store.get(sid)
    return await request.app.state.service.store.events(
        sid, after_seq, before=before_seq, limit=limit, include_messages=include_messages
    )


@router.get("/tasks/{sid}/captures")
async def task_captures(sid: str, request: Request):
    await request.app.state.service.store.get(sid)
    return await request.app.state.service.store.list_captures(sid)


@router.get("/tasks/{sid}/export")
async def export_candidate(sid: str, request: Request):
    return await request.app.state.service.export_candidate(sid)


@router.get("/tasks/{sid}/handoff")
async def handoff(sid: str, request: Request):
    return export_handoff(await request.app.state.service.store.get(sid))


@router.post("/tasks/{sid}/messages")
async def message(sid: str, command: MessageCommand, request: Request):
    return await request.app.state.service.command(sid, "message", command)


@router.get("/tasks/{sid}/messages")
async def messages(sid: str, request: Request, after_seq: int = 0, limit: int = 100):
    return await request.app.state.service.store.messages(sid, after_seq, limit)


@router.post("/tasks/{sid}/draft")
async def edit(sid: str, command: EditCommand, request: Request):
    return await request.app.state.service.command(sid, "edit", command)


@router.post("/tasks/{sid}/mappings")
async def mappings(sid: str, command: MappingCommand, request: Request):
    return await request.app.state.service.command(sid, "mappings", command)


@router.post("/tasks/{sid}/references")
async def references(sid: str, command: ReferenceCommand, request: Request):
    return await request.app.state.service.command(sid, "references", command)


@router.get("/tasks/{sid}/rebase")
async def rebase_preview(sid: str, request: Request):
    service = request.app.state.service
    return await service.rebase_preview(await service.store.get(sid))


@router.post("/tasks/{sid}/rebase")
async def rebase(sid: str, command: RebaseCommand, request: Request):
    return await request.app.state.service.command(sid, "rebase", command)


@router.post("/tasks/{sid}/split")
async def split(sid: str, command: SplitCommand, request: Request):
    return await request.app.state.service.command(sid, "split", command)


@router.post("/tasks/{sid}/local-report")
async def local_report(sid: str, command: LocalReportCommand, request: Request):
    return await request.app.state.service.command(sid, "local-report", command)


@router.post("/tasks/{sid}/adoption")
async def adoption(sid: str, command: AdoptionCommand, request: Request):
    return await request.app.state.service.command(sid, "adoption", command)


@router.post("/tasks/{sid}/trial")
async def collection_trial(sid: str, command: RevisionCommand, request: Request):
    return await request.app.state.service.command(sid, "trial", command)


@router.post("/tasks/{sid}/replay")
async def replay(sid: str, command: ReplayCommand, request: Request):
    return await request.app.state.service.command(sid, "replay", command)


@router.post("/tasks/{sid}/page")
async def refresh_page(sid: str, command: RefreshPageCommand, request: Request):
    return await request.app.state.service.command(sid, "page", command)


@router.post("/tasks/{sid}/interactions/{interaction_id}/answer")
async def answer(sid: str, interaction_id: str, command: InteractionCommand, request: Request):
    return await request.app.state.service.command(sid, "answer", command, interaction_id)


@router.post("/tasks/{sid}/browser-tasks/{task_id}/result")
async def browser_result(sid: str, task_id: str, command: BrowserResultCommand, request: Request):
    return await request.app.state.service.command(sid, "browser", command, task_id)


@router.post("/tasks/{sid}/confirm")
async def confirm(sid: str, command: ConfirmationCommand, request: Request):
    return await request.app.state.service.command(sid, "confirm", command)


@router.post("/tasks/{sid}/resume")
async def resume(sid: str, command: RevisionCommand, request: Request):
    return await request.app.state.service.command(sid, "resume", command)


@router.post("/tasks/{sid}/cancel")
async def cancel(sid: str, request: Request):
    await request.app.state.service.cancel(sid)
    return {"ok": True}


@router.post("/tasks/{sid}/archive")
async def archive(sid: str, command: RevisionCommand, request: Request):
    return await request.app.state.service.command(sid, "archive", command)


@router.post("/tasks/{sid}/restore")
async def restore(sid: str, command: RevisionCommand, request: Request):
    return await request.app.state.service.command(sid, "restore", command)


@router.post("/tasks/{sid}/validate")
async def validate(sid: str, command: RevisionCommand, request: Request):
    return await request.app.state.service.validate(sid, command)


@router.get("/tasks/{sid}/activation")
async def activation(sid: str, request: Request):
    return await request.app.state.service.activation(sid)


@router.delete("/tasks/{sid}")
async def delete(sid: str, request: Request):
    await request.app.state.service.delete(sid)
    return {"ok": True}
