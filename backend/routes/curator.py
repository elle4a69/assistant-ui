"""Knowledge curation, learned rules, and knowledge files management routes."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import re
import shutil
from typing import Any, Dict, List, Optional, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy.orm import Session

try:
    from backend.core.config import DATA_DIR, KNOWLEDGE_DIR, BASE_DIR
    from backend.core.database import get_db, SessionLocal
    from backend.core.clients import openai_client, GoogleCalendarService
    from backend.models.domain import Thread, Message
    from backend.schemas.domain import (
        ManualLearningInput, LearnedInformationUpdateInput,
        LearnedInformationBulkApproveInput, SmsLearningPreviewInput,
        SmsLearningImportInput, FileSaveInput, FileSearchInput, FilePurgeInput,
    )
    from backend.curator import (
        inspect_knowledge_integrity,
        run_knowledge_curator,
        get_knowledge_curator_state,
        accept_knowledge_curator_proposal,
        resolve_knowledge_curator_proposal,
        transition_knowledge_curator_proposal,
        KnowledgeCuratorService,
    )
    from backend.curator.authority import LEARNED_INFORMATION_FILENAME
    from backend.services.learning_service import (
        list_learned_information,
        save_manual_learning,
        generate_manual_learning,
        replace_learned_information_entry,
        approve_learned_information_entry,
        approve_pending_learned_information,
        approve_selected_learned_information,
        redraft_learned_information_entry,
        redraft_all_pending_learned_information,
        move_all_learned_information_to_review,
        delete_learned_information_entry,
        save_edited_draft_learning,
        LEARNED_INFORMATION_FILE,
    )
    from backend.services.sms_service import (
        preview_sms_pair_learnings,
        save_sms_pair_learning_candidates,
    )
    from backend.services.knowledge_service import load_knowledge_base
    from backend.curator.classifier import classify_all_learned_information
except ImportError:
    from core.config import DATA_DIR, KNOWLEDGE_DIR, BASE_DIR
    from core.database import get_db, SessionLocal
    from core.clients import openai_client, GoogleCalendarService
    from models.domain import Thread, Message
    from schemas.domain import (
        ManualLearningInput, LearnedInformationUpdateInput,
        LearnedInformationBulkApproveInput, SmsLearningPreviewInput,
        SmsLearningImportInput, FileSaveInput, FileSearchInput, FilePurgeInput,
    )
    from curator import (
        inspect_knowledge_integrity,
        run_knowledge_curator,
        get_knowledge_curator_state,
        accept_knowledge_curator_proposal,
        resolve_knowledge_curator_proposal,
        transition_knowledge_curator_proposal,
        KnowledgeCuratorService,
    )
    from curator.authority import LEARNED_INFORMATION_FILENAME
    from services.learning_service import (
        list_learned_information,
        save_manual_learning,
        generate_manual_learning,
        replace_learned_information_entry,
        approve_learned_information_entry,
        approve_pending_learned_information,
        approve_selected_learned_information,
        redraft_learned_information_entry,
        redraft_all_pending_learned_information,
        move_all_learned_information_to_review,
        delete_learned_information_entry,
        save_edited_draft_learning,
        LEARNED_INFORMATION_FILE,
    )
    from services.sms_service import (
        preview_sms_pair_learnings,
        save_sms_pair_learning_candidates,
    )
    from services.knowledge_service import load_knowledge_base
    from curator.classifier import classify_all_learned_information

router = APIRouter()

@router.post("/api/settings/learnings")
def create_manual_learning(payload: ManualLearningInput):
    structured = generate_manual_learning(payload.topic, payload.guidance)
    entry = save_manual_learning(payload.topic, payload.guidance, structured, payload.scope)
    return {
        "status": "success",
        "filename": LEARNED_INFORMATION_FILENAME,
        "entry": entry,
    }


@router.get("/api/settings/learnings")
def get_learned_information():
    return {"entries": list_learned_information()}


@router.put("/api/settings/learnings/{entry_id}")
def update_learned_information(entry_id: str, payload: LearnedInformationUpdateInput):
    try:
        # An edit changes the meaning of a learning, so it must be reviewed
        # again before it can affect a customer reply.
        entry = replace_learned_information_entry(entry_id, {
            **payload.model_dump(),
            "review_status": "pending",
            "retrieval_enabled": False,
        })
    except KeyError:
        raise HTTPException(status_code=404, detail="Learned entry not found.")
    return {"status": "success", "entry": entry}


@router.post("/api/settings/learnings/{entry_id}/approve")
def approve_learned_information(entry_id: str):
    try:
        entry = approve_learned_information_entry(entry_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Learned entry not found.")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "success", "entry": entry}


@router.post("/api/settings/learnings/approve-pending")
def approve_pending_learned_information_endpoint():
    return {"status": "success", **approve_pending_learned_information()}


@router.post("/api/settings/learnings/approve-selected")
def approve_selected_learned_information_endpoint(payload: LearnedInformationBulkApproveInput):
    try:
        return {"status": "success", **approve_selected_learned_information(payload.entry_ids)}
    except KeyError:
        raise HTTPException(status_code=404, detail="One or more learned entries were not found.")


@router.post("/api/settings/learnings/sms-pair-preview")
def sms_pair_learning_preview(payload: SmsLearningPreviewInput, db: Session = Depends(get_db)):
    return {"status": "success", **preview_sms_pair_learnings(db, payload.limit)}


@router.post("/api/settings/learnings/sms-pair-import")
def sms_pair_learning_import(payload: SmsLearningImportInput):
    candidates = [candidate.model_dump() for candidate in payload.candidates]
    return {"status": "success", **save_sms_pair_learning_candidates(candidates)}


@router.post("/api/settings/learnings/{entry_id}/redraft")
def redraft_learned_information(entry_id: str):
    try:
        entry = redraft_learned_information_entry(entry_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Learned entry not found.")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"status": "success", "entry": entry}


@router.post("/api/settings/learnings/redraft-pending")
def redraft_pending_learned_information():
    return {"status": "success", **redraft_all_pending_learned_information()}


@router.post("/api/settings/learnings/move-all-to-review")
def move_all_learnings_to_review():
    return {"status": "success", "moved": move_all_learned_information_to_review()}


@router.delete("/api/settings/learnings/{entry_id}")
def remove_learned_information(entry_id: str):
    try:
        delete_learned_information_entry(entry_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Learned entry not found.")
    return {"status": "success"}


@router.post("/api/settings/learnings/classify")
def classify_learned_information():
    if not openai_client:
        raise HTTPException(status_code=503, detail="The AI classifier is unavailable.")
    return {"status": "success", **classify_all_learned_information()}


@router.get("/api/settings/knowledge-curator")
def list_knowledge_curator_state():
    return get_knowledge_curator_state()


@router.post("/api/settings/knowledge-curator/run")
def run_knowledge_curator_endpoint():
    return run_knowledge_curator()


@router.post("/api/settings/knowledge-curator/proposals/{proposal_id}/accept")
def accept_knowledge_curator_proposal_endpoint(proposal_id: str):
    try:
        return {"status": "success", "proposal": accept_knowledge_curator_proposal(proposal_id)}
    except KeyError:
        raise HTTPException(status_code=404, detail="Knowledge curator proposal not found.")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/settings/knowledge-curator/proposals/{proposal_id}/resolve")
def resolve_knowledge_curator_proposal_endpoint(proposal_id: str, payload: Dict[str, Any]):
    try:
        resolution = str(payload.get("resolution") or "")
        selected = payload.get("selected_record_ids")
        if selected is not None and not isinstance(selected, list):
            raise ValueError("selected_record_ids must be a list.")
        return {"status": "success", "proposal": resolve_knowledge_curator_proposal(proposal_id, resolution, selected)}
    except KeyError:
        raise HTTPException(status_code=404, detail="Knowledge curator proposal not found.")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/settings/knowledge-curator/proposals/{proposal_id}/{transition}")
def transition_knowledge_curator_proposal_endpoint(proposal_id: str, transition: Literal["reject", "dismiss"]):
    try:
        proposal = transition_knowledge_curator_proposal(proposal_id, "rejected" if transition == "reject" else "dismissed")
        return {"status": "success", "proposal": proposal}
    except KeyError:
        raise HTTPException(status_code=404, detail="Knowledge curator proposal not found.")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/api/settings/knowledge-files")
def get_knowledge_files():
    knowledge_dir = KNOWLEDGE_DIR
    files_list = []
    if os.path.exists(knowledge_dir):
        for filename in os.listdir(knowledge_dir):
            filepath = os.path.join(knowledge_dir, filename)
            if os.path.isfile(filepath):
                files_list.append({
                    "name": filename,
                    "sizeBytes": os.path.getsize(filepath)
                })
    return files_list


@router.post("/api/settings/upload-knowledge")
def upload_knowledge_file(file: UploadFile = File(...)):
    knowledge_dir = KNOWLEDGE_DIR
    os.makedirs(knowledge_dir, exist_ok=True)
    filepath = os.path.join(knowledge_dir, file.filename)
    
    with open(filepath, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    # Reload and re-index
    load_knowledge_base()
    
    return {"status": "success", "filename": file.filename}


@router.post("/api/settings/upload-credentials")
def upload_credentials_file(file: UploadFile = File(...)):
    dest_path = os.path.join(BASE_DIR, "service_account.json")
    with open(dest_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    global calendar_service
    calendar_service = GoogleCalendarService(SessionLocal)
    
    return {"status": "success"}


@router.get("/api/settings/knowledge-files/{filename}")
def get_knowledge_file_content(filename: str):
    knowledge_dir = KNOWLEDGE_DIR
    filepath = os.path.join(knowledge_dir, filename)
    if not os.path.exists(filepath) or not os.path.isfile(filepath):
        raise HTTPException(status_code=404, detail="File not found")
        
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        return {"content": content}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read file: {e}")


@router.post("/api/settings/knowledge-files/{filename}")
def save_knowledge_file_content(filename: str, payload: FileSaveInput):
    knowledge_dir = KNOWLEDGE_DIR
    filepath = os.path.join(knowledge_dir, filename)
    
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(payload.content)
            
        load_knowledge_base()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save file: {e}")


@router.delete("/api/settings/knowledge-files/{filename}")
def delete_knowledge_file(filename: str):
    knowledge_dir = KNOWLEDGE_DIR
    filepath = os.path.join(knowledge_dir, filename)
    if not os.path.exists(filepath) or not os.path.isfile(filepath):
        raise HTTPException(status_code=404, detail="File not found")
        
    try:
        os.remove(filepath)
        load_knowledge_base()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete file: {e}")


@router.post("/api/settings/knowledge-files/{filename}/search")
def search_knowledge_file_lines(filename: str, payload: FileSearchInput):
    knowledge_dir = KNOWLEDGE_DIR
    filepath = os.path.join(knowledge_dir, filename)
    if not os.path.exists(filepath) or not os.path.isfile(filepath):
        raise HTTPException(status_code=404, detail="File not found")
        
    q = payload.query.lower().strip()
    results = []
    total = 0
    
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for idx, line in enumerate(f):
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                    text_to_search = json.dumps(obj).lower()
                    if q in text_to_search:
                        total += 1
                        if len(results) < 100:
                            results.append({
                                "index": idx,
                                "input": obj.get("input", ""),
                                "output": obj.get("output", "")
                            })
                except Exception:
                    if q in line.lower():
                        total += 1
                        if len(results) < 100:
                            results.append({
                                "index": idx,
                                "input": line,
                                "output": ""
                            })
        return {"results": results, "totalMatches": total}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to search file: {e}")


@router.post("/api/settings/knowledge-files/{filename}/purge")
def purge_knowledge_file_lines(filename: str, payload: FilePurgeInput):
    knowledge_dir = KNOWLEDGE_DIR
    filepath = os.path.join(knowledge_dir, filename)
    if not os.path.exists(filepath) or not os.path.isfile(filepath):
        raise HTTPException(status_code=404, detail="File not found")
        
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()
            
        purged_lines = []
        purged_count = 0
        
        q = payload.query.lower().strip() if payload.query else None
        indices_set = set(payload.indices) if payload.indices is not None else set()
        
        for idx, line in enumerate(lines):
            if not line.strip():
                continue
                
            if idx in indices_set:
                purged_count += 1
                continue
                
            if q:
                matched = False
                try:
                    obj = json.loads(line)
                    inp = str(obj.get("input", "")).lower()
                    out = str(obj.get("output", "")).lower()
                    if q in inp or q in out:
                        matched = True
                except Exception:
                    if q in line.lower():
                        matched = True
                        
                if matched:
                    purged_count += 1
                    continue
                    
            purged_lines.append(line)
            
        with open(filepath, "w", encoding="utf-8") as f:
            f.writelines(purged_lines)
            
        load_knowledge_base()
        return {"status": "success", "purgedCount": purged_count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to purge file: {e}")


__all__ = [
    "router",
    "create_manual_learning",
    "get_learned_information",
    "update_learned_information",
    "approve_learned_information",
    "approve_pending_learned_information_endpoint",
    "approve_selected_learned_information_endpoint",
    "sms_pair_learning_preview",
    "sms_pair_learning_import",
    "redraft_learned_information",
    "redraft_pending_learned_information",
    "move_all_learnings_to_review",
    "remove_learned_information",
    "classify_learned_information",
    "list_knowledge_curator_state",
    "run_knowledge_curator_endpoint",
    "accept_knowledge_curator_proposal_endpoint",
    "resolve_knowledge_curator_proposal_endpoint",
    "transition_knowledge_curator_proposal_endpoint",
    "get_knowledge_files",
    "upload_knowledge_file",
    "upload_credentials_file",
    "get_knowledge_file_content",
    "save_knowledge_file_content",
    "delete_knowledge_file",
    "search_knowledge_file_lines",
    "purge_knowledge_file_lines",
]
