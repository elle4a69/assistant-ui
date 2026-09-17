"""Operations AI, agent console, and realtime session routes."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timezone
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

try:
    from backend.core.database import get_db, SessionLocal
    from backend.core.utils import format_dt, _dyn
    from backend.core.state import _agent_run_tasks, _agent_start_lock
    from backend.core.clients import openai_client
    from backend.core.constants import AGENT_CONSOLE_PROTOCOL_VERSION
    from backend.models.domain import (
        OperationsAgentRun, OperationsAgentEvent, OperationsChatMessage,
    )
    from backend.schemas.domain import (
        OperationsChatInput, OperationsRealtimeTurnInput, OperationsRealtimeToolInput,
        OperationsVoiceToolInput,
    )
    from backend.services.operations_service import (
        _serialize_agent_run,
        _serialize_agent_event,
        _build_agent_conversation_context,
        _create_agent_run,
        _run_agent_console,
        _stream_agent_run,
        _agent_websocket_authenticated,
        _agent_websocket_origin_allowed,
        _prune_agent_console_history,
        _agent_task_done,
        AgentConsoleBusyError,
        AgentConsoleError,
        agent_console_enabled,
        agent_console_max_steps,
        agent_console_total_timeout_seconds,
        build_operations_ai_memory_context,
        build_operations_ai_snapshot,
        create_operations_realtime_session,
        ensure_operations_owner_working_style,
        execute_operations_tool,
        execute_operations_voice_tool,
        operations_ai_instructions,
        operations_code_access_available,
        persist_operations_realtime_turn,
        serialize_operations_chat_message,
        OPERATIONS_AI_TOOLS,
        _operations_claim_worker_task,
    )
except ImportError:
    from core.database import get_db, SessionLocal
    from core.utils import format_dt, _dyn
    from core.state import _agent_run_tasks, _agent_start_lock
    from core.clients import openai_client
    from core.constants import AGENT_CONSOLE_PROTOCOL_VERSION
    from models.domain import (
        OperationsAgentRun, OperationsAgentEvent, OperationsChatMessage,
    )
    from schemas.domain import (
        OperationsChatInput, OperationsRealtimeTurnInput, OperationsRealtimeToolInput,
        OperationsVoiceToolInput,
    )
    from services.operations_service import (
        _serialize_agent_run,
        _serialize_agent_event,
        _build_agent_conversation_context,
        _create_agent_run,
        _run_agent_console,
        _stream_agent_run,
        _agent_websocket_authenticated,
        _agent_websocket_origin_allowed,
        _prune_agent_console_history,
        _agent_task_done,
        AgentConsoleBusyError,
        AgentConsoleError,
        agent_console_enabled,
        agent_console_max_steps,
        agent_console_total_timeout_seconds,
        build_operations_ai_memory_context,
        build_operations_ai_snapshot,
        create_operations_realtime_session,
        ensure_operations_owner_working_style,
        execute_operations_tool,
        execute_operations_voice_tool,
        operations_ai_instructions,
        operations_code_access_available,
        persist_operations_realtime_turn,
        serialize_operations_chat_message,
        OPERATIONS_AI_TOOLS,
        _operations_claim_worker_task,
    )

router = APIRouter()

@router.get("/api/settings/agent-console/runs")
def list_agent_console_runs(limit: int = Query(default=20, ge=1, le=50), db: Session = Depends(get_db)):
    runs = (
        db.query(OperationsAgentRun)
        .order_by(OperationsAgentRun.created_at.desc(), OperationsAgentRun.id.desc())
        .limit(limit)
        .all()
    )
    return {
        "enabled": agent_console_enabled(),
        "runs": [_serialize_agent_run(run) for run in runs],
    }


@router.get("/api/settings/agent-console/runs/{run_id}/events")
def list_agent_console_events(
    run_id: str,
    after: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    run = db.query(OperationsAgentRun).filter(OperationsAgentRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Operations Console run not found.")
    events = (
        db.query(OperationsAgentEvent)
        .filter(OperationsAgentEvent.run_id == run_id, OperationsAgentEvent.sequence > after)
        .order_by(OperationsAgentEvent.sequence.asc())
        .limit(500)
        .all()
    )
    return {"run": _serialize_agent_run(run), "events": [_serialize_agent_event(event) for event in events]}


@router.websocket("/ws/agent")
async def operations_agent_websocket(websocket: WebSocket):
    if not _agent_websocket_authenticated(websocket):
        await websocket.close(code=4401, reason="Admin authentication required.")
        return
    if not _agent_websocket_origin_allowed(websocket):
        await websocket.close(code=4403, reason="WebSocket origin rejected.")
        return

    await websocket.accept()
    await websocket.send_json({
        "type": "ready",
        "protocolVersion": AGENT_CONSOLE_PROTOCOL_VERSION,
        "enabled": agent_console_enabled(),
        "limits": {
            "maxSteps": agent_console_max_steps(),
            "actionTimeoutSeconds": 30,
            "totalTimeoutSeconds": agent_console_total_timeout_seconds(),
        },
    })
    try:
        payload = await asyncio.wait_for(websocket.receive_json(), timeout=30)
    except asyncio.TimeoutError:
        await websocket.send_json({
            "type": "error", "code": "handshake_timeout",
            "message": "No objective or run attachment was received.", "retryable": True,
        })
        await websocket.close(code=4408)
        return
    except (WebSocketDisconnect, ValueError):
        return
    if not isinstance(payload, dict):
        await websocket.send_json({
            "type": "error", "code": "invalid_request",
            "message": "The Operations Console request is invalid.", "retryable": True,
        })
        await websocket.close(code=4400)
        return

    message_type = str(payload.get("type") or "")
    try:
        after_sequence = max(0, min(1_000_000, int(payload.get("afterSequence") or 0)))
    except (TypeError, ValueError):
        after_sequence = 0
    if message_type == "start":
        if not agent_console_enabled():
            await websocket.send_json({
                "type": "error", "code": "console_unavailable",
                "message": "The Operations Coding Agent is not configured.", "retryable": False,
            })
            await websocket.close(code=1013)
            return
        try:
            run, created = await asyncio.to_thread(
                _create_agent_run,
                str(payload.get("requestId") or ""),
                str(payload.get("objective") or ""),
            )
        except AgentConsoleBusyError as exc:
            await websocket.send_json({
                "type": "error", "code": "run_busy",
                "message": str(exc), "retryable": True,
            })
            await websocket.close(code=4429)
            return
        except AgentConsoleError as exc:
            await websocket.send_json({
                "type": "error", "code": "invalid_request",
                "message": str(exc), "retryable": True,
            })
            await websocket.close(code=4400)
            return
        run_id = run.id
        if created:
            task = asyncio.create_task(_run_agent_console(run.id, run.objective, run.max_steps))
            _agent_run_tasks[run.id] = task
            task.add_done_callback(lambda completed, value=run.id: _agent_task_done(value, completed))
    elif message_type in {"attach", "resume"}:
        run_id = str(payload.get("runId") or "").strip()
        if not run_id:
            await websocket.send_json({
                "type": "error", "code": "invalid_request",
                "message": "A run ID is required to reconnect.", "retryable": False,
            })
            await websocket.close(code=4400)
            return
    else:
        await websocket.send_json({
            "type": "error", "code": "invalid_request",
            "message": "Start a new run or attach to an existing run.", "retryable": True,
        })
        await websocket.close(code=4400)
        return

    await _stream_agent_run(websocket, run_id, after_sequence)
    with contextlib.suppress(RuntimeError):
        await websocket.close(code=1000)


@router.post("/api/internal/operations/worker-claim")
def claim_operations_worker_task(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """Let one verified GitHub-hosted queue run claim an audited action."""
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.casefold() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="The GitHub worker identity was rejected.")
    response.headers["Cache-Control"] = "no-store"
    return _operations_claim_worker_task(db, token.strip())


@router.get("/api/settings/operations-chat/messages")
def get_operations_chat_messages(db: Session = Depends(get_db)):
    messages = (
        db.query(OperationsChatMessage)
        .order_by(OperationsChatMessage.created_at.desc(), OperationsChatMessage.id.desc())
        .limit(200)
        .all()
    )
    messages.reverse()
    return {"messages": [serialize_operations_chat_message(item) for item in messages]}


@router.post("/api/settings/operations-chat/messages")
def send_operations_chat_message(payload: OperationsChatInput, db: Session = Depends(get_db)):
    effective_openai_client = _dyn("openai_client", openai_client)
    if not effective_openai_client:
        raise HTTPException(status_code=503, detail="The operations AI is unavailable because OpenAI is not configured.")

    content = payload.message.strip()
    if not content:
        raise HTTPException(status_code=422, detail="Message cannot be empty.")

    ensure_operations_owner_working_style(db)
    user_message = OperationsChatMessage(role="user", content=content)
    db.add(user_message)
    db.commit()
    db.refresh(user_message)

    history = (
        db.query(OperationsChatMessage)
        .order_by(OperationsChatMessage.created_at.desc(), OperationsChatMessage.id.desc())
        .limit(60)
        .all()
    )
    history.reverse()
    model_input = [
        {"role": item.role, "content": item.content}
        for item in history
    ]
    instructions = operations_ai_instructions(
        build_operations_ai_snapshot(db),
        build_operations_ai_memory_context(db),
    )
    try:
        response = effective_openai_client.responses.create(
            model="gpt-5.6-terra",
            instructions=instructions,
            input=model_input,
            tools=OPERATIONS_AI_TOOLS,
            max_output_tokens=1200,
            store=False,
        )
        tool_round = 0
        while True:
            tool_calls = [
                item for item in (getattr(response, "output", None) or [])
                if getattr(item, "type", None) == "function_call"
            ]
            if not tool_calls:
                reply = (response.output_text or "").strip()
                break
            if tool_round >= 6:
                raise RuntimeError("Operations AI exceeded its tool-step limit")
            tool_round += 1
            model_input.extend({
                "type": "function_call",
                "call_id": item.call_id,
                "name": item.name,
                "arguments": item.arguments,
            } for item in tool_calls)
            for item in tool_calls:
                try:
                    arguments = json.loads(item.arguments or "{}")
                except (TypeError, json.JSONDecodeError):
                    arguments = {}
                result = execute_operations_tool(db, item.name, arguments, content)
                model_input.append({
                    "type": "function_call_output",
                    "call_id": item.call_id,
                    "output": json.dumps(result, ensure_ascii=False),
                })
            response = effective_openai_client.responses.create(
                model="gpt-5.6-terra",
                instructions=instructions,
                input=model_input,
                tools=OPERATIONS_AI_TOOLS,
                max_output_tokens=1200,
                store=False,
            )
    except Exception as exc:
        print(f"Operations AI request failed: {type(exc).__name__}")
        raise HTTPException(status_code=502, detail="The operations AI could not answer right now.") from exc
    if not reply:
        raise HTTPException(status_code=502, detail="The operations AI returned an empty answer.")

    assistant_message = OperationsChatMessage(role="assistant", content=reply)
    db.add(assistant_message)
    db.commit()
    db.refresh(assistant_message)
    return {
        "userMessage": serialize_operations_chat_message(user_message),
        "assistantMessage": serialize_operations_chat_message(assistant_message),
        "capabilities": {
            "readOnly": False,
            "liveSnapshot": True,
            "codeAccess": operations_code_access_available(),
            "logAccess": True,
            "diagnosticTools": True,
            "messageSelfDiagnosis": True,
            "webSearch": True,
            "persistentMemory": True,
            "controlledActions": True,
            "requiresConfirmation": True,
        },
    }


@router.post("/api/settings/operations-chat/realtime")
async def start_operations_realtime_session(request: Request, db: Session = Depends(get_db)):
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().casefold()
    if content_type != "application/sdp":
        raise HTTPException(status_code=415, detail="Realtime voice requires an application/sdp offer.")
    raw_offer = await request.body()
    try:
        sdp = raw_offer.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=422, detail="The realtime session offer is invalid.") from exc
    ensure_operations_owner_working_style(db)
    answer = create_operations_realtime_session(
        sdp,
        build_operations_ai_snapshot(db),
        build_operations_ai_memory_context(db),
        _build_agent_conversation_context(
            db,
            current_run_id=f"realtime-{uuid.uuid4()}",
            max_chars=12_000,
        ),
    )
    return Response(content=answer, media_type="application/sdp")


@router.post("/api/settings/operations-chat/realtime/turns")
def save_operations_realtime_turn(
    payload: OperationsRealtimeTurnInput,
    db: Session = Depends(get_db),
):
    return persist_operations_realtime_turn(payload, db)


@router.post("/api/settings/operations-chat/realtime/tool")
def run_operations_realtime_tool(
    payload: OperationsVoiceToolInput,
    db: Session = Depends(get_db),
):
    return execute_operations_voice_tool(db, payload.name, payload.arguments)


__all__ = [
    "router",
    "list_agent_console_runs",
    "list_agent_console_events",
    "operations_agent_websocket",
    "claim_operations_worker_task",
    "get_operations_chat_messages",
    "send_operations_chat_message",
    "start_operations_realtime_session",
    "save_operations_realtime_turn",
    "run_operations_realtime_tool",
]
