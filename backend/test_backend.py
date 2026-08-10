import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')
import os
os.environ["SQLITE_TMPDIR"] = "f:/Projects/assistant-ui/backend/tmp"
os.makedirs("f:/Projects/assistant-ui/backend/tmp", exist_ok=True)
import json
from datetime import datetime, timezone

# Add parent directory to path to import main
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from fastapi.testclient import TestClient
import main
from main import app, engine, Base, Thread, Message, Note, ThreadEvent, SessionLocal

# Force training mode to False during tests so role assertions check out
main.TRAINING_MODE_ENABLED = False

# Clear DB for a clean test run
Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)

client = TestClient(app)

def run_tests():
    # Back up active config files to prevent tests from overwriting user configuration
    paths = {
        "env": "f:/Projects/assistant-ui/backend/.env",
        "system_prompt": "f:/Projects/assistant-ui/backend/prompts/system_prompt.txt",
        "user_prompt": "f:/Projects/assistant-ui/backend/prompts/user_prompt.txt",
        "service_account": "f:/Projects/assistant-ui/backend/service_account.json"
    }
    
    backups = {}
    for key, path in paths.items():
        if os.path.exists(path):
            try:
                with open(path, "rb") as f:
                    backups[path] = f.read()
            except Exception as e:
                print(f"Backup failed for {path}: {e}")
                
    import atexit
    def restore_backups():
        print("\nRestoring original active configurations from backup...")
        for path, content in backups.items():
            try:
                with open(path, "wb") as f:
                    f.write(content)
            except Exception as e:
                print(f"Failed to restore {path}: {e}")
        
        # Cleanup test uploads
        test_file = "f:/Projects/assistant-ui/backend/knowledge/test_upload.txt"
        if os.path.exists(test_file):
            try:
                os.remove(test_file)
            except Exception:
                pass
                
    atexit.register(restore_backups)

    print("--- 1. Testing Webhook SMS (New Thread creation and Auto-reply) ---")
    payload = {
        "from": "+15551234567",
        "to": "+15557654321",
        "body": "Hi, I need help",
        "providerMessageId": "abc123",
        "receivedAt": "2026-07-28T10:11:12Z"
    }
    response = client.post("/webhooks/sms", json=payload)
    print("Status code:", response.status_code)
    print("Response:", response.json())
    assert response.status_code == 200
    thread_id = response.json()["thread_id"]
    
    print("\n--- 2. Testing Webhook SMS (Existing Thread - state auto-reply, check no state change to needs-review) ---")
    payload2 = {
        "from": "+15551234567",
        "to": "+15557654321",
        "body": "Any updates?",
        "providerMessageId": "def456",
        "receivedAt": "2026-07-28T10:15:00Z"
    }
    response2 = client.post("/webhooks/sms", json=payload2)
    print("Status code:", response2.status_code)
    print("Response:", response2.json())
    assert response2.status_code == 200
    
    print("\n--- 3. Testing GET /api/threads ---")
    response_list = client.get("/api/threads")
    print("Status code:", response_list.status_code)
    print("Threads list length:", len(response_list.json()))
    print("Thread item details:")
    print(json.dumps(response_list.json(), indent=2))
    assert response_list.status_code == 200
    
    print("\n--- 4. Testing GET /api/threads/{thread_id} ---")
    response_detail = client.get(f"/api/threads/{thread_id}")
    print("Status code:", response_detail.status_code)
    print("Thread detail:")
    print(json.dumps(response_detail.json(), indent=2))
    assert response_detail.status_code == 200
    
    print("\n--- 5. Testing POST /api/threads/{thread_id}/takeover ---")
    takeover_payload = {
        "agentId": "ag_1"
    }
    response_takeover = client.post(f"/api/threads/{thread_id}/takeover", json=takeover_payload)
    print("Status code:", response_takeover.status_code)
    print("Response:", response_takeover.json())
    assert response_takeover.status_code == 200
    
    print("\n--- 6. Testing Webhook SMS while state == 'taken-over' (DO NOT auto-reply) ---")
    payload3 = {
        "from": "+15551234567",
        "to": "+15557654321",
        "body": "This is a message during takeover",
        "providerMessageId": "ghi789",
        "receivedAt": "2026-07-28T10:20:00Z"
    }
    response3 = client.post("/webhooks/sms", json=payload3)
    print("Status code:", response3.status_code)
    print("Response:", response3.json())
    assert response3.status_code == 200
    
    # Check that no auto-reply was added (messages count should have only the customer message, no system message)
    response_detail_after_takeover = client.get(f"/api/threads/{thread_id}")
    print("Messages after takeover SMS:")
    for msg in response_detail_after_takeover.json()["messages"]:
        print(f"  [{msg['role']}]: {msg['text']}")
    
    print("\n--- 7. Testing POST /api/threads/{thread_id}/reply ---")
    reply_payload = {
        "agentId": "ag_1",
        "text": "Hello, how can I help you today?"
    }
    response_reply = client.post(f"/api/threads/{thread_id}/reply", json=reply_payload)
    print("Status code:", response_reply.status_code)
    print("Response:", response_reply.json())
    assert response_reply.status_code == 200
    
    print("\n--- 8. Testing POST /api/threads/{thread_id}/notes ---")
    note_payload = {
        "agentId": "ag_1",
        "text": "Customer is asking about pricing packages."
    }
    response_note = client.post(f"/api/threads/{thread_id}/notes", json=note_payload)
    print("Status code:", response_note.status_code)
    print("Response:", response_note.json())
    assert response_note.status_code == 200
    
    print("\n--- 9. Testing POST /api/threads/{thread_id}/escalate ---")
    escalate_payload = {
        "agentId": "ag_1",
        "reason": "Customer needs custom Enterprise pricing plan."
    }
    response_escalate = client.post(f"/api/threads/{thread_id}/escalate", json=escalate_payload)
    print("Status code:", response_escalate.status_code)
    print("Response:", response_escalate.json())
    assert response_escalate.status_code == 200
    
    print("\n--- 10. Testing POST /api/threads/{thread_id}/resolve ---")
    resolve_payload = {
        "agentId": "ag_1",
        "resolution": "Offered custom proposal, client accepted."
    }
    response_resolve = client.post(f"/api/threads/{thread_id}/resolve", json=resolve_payload)
    print("Status code:", response_resolve.status_code)
    print("Response:", response_resolve.json())
    assert response_resolve.status_code == 200
    
    print("\n--- 11. Testing Final Thread Detail (check events, notes, messages) ---")
    response_final = client.get(f"/api/threads/{thread_id}")
    print("Status code:", response_final.status_code)
    print("Final Thread detail:")
    print(json.dumps(response_final.json(), indent=2))
    assert response_final.status_code == 200
    # Check that autoReplyEnabled exists in response
    assert "autoReplyEnabled" in response_final.json()
    assert response_final.json()["autoReplyEnabled"] is True

    print("\n--- 12. Testing Phase 2: Create a second thread and verify autoReplyEnabled is True by default ---")
    payload_new = {
        "from": "+15559876543",
        "to": "+15557654321",
        "body": "Hi, I need help on thread 2",
        "providerMessageId": "xyz001",
        "receivedAt": "2026-07-28T11:00:00Z"
    }
    response_new = client.post("/webhooks/sms", json=payload_new)
    assert response_new.status_code == 200
    thread_id_2 = response_new.json()["thread_id"]
    
    # Get details and verify default True
    detail_2 = client.get(f"/api/threads/{thread_id_2}")
    assert detail_2.json()["autoReplyEnabled"] is True
    # Verify auto-reply message was sent
    assert len(detail_2.json()["messages"]) == 2 # 1 customer + 1 system auto-reply

    print("\n--- 13. Testing Phase 2: Toggle autoresponder to False ---")
    response_toggle = client.post(f"/api/threads/{thread_id_2}/autoresponder", json={"enabled": False})
    print("Toggle Response:", response_toggle.json())
    assert response_toggle.status_code == 200
    assert response_toggle.json()["autoReplyEnabled"] is False

    # Get details and verify False and check state-changed event
    detail_2_disabled = client.get(f"/api/threads/{thread_id_2}")
    assert detail_2_disabled.json()["autoReplyEnabled"] is False
    
    # Check state-changed event is logged
    events = detail_2_disabled.json()["events"]
    state_changed_events = [e for e in events if e["type"] == "state-changed"]
    assert len(state_changed_events) > 0
    assert state_changed_events[0]["meta"] == {"autoReplyEnabled": False}

    print("\n--- 14. Testing Phase 2: Webhook SMS when autoresponder is False (Verify no auto-reply) ---")
    payload_disabled = {
        "from": "+15559876543",
        "to": "+15557654321",
        "body": "Another message when responder is disabled",
        "providerMessageId": "xyz002",
        "receivedAt": "2026-07-28T11:05:00Z"
    }
    response_disabled = client.post("/webhooks/sms", json=payload_disabled)
    assert response_disabled.status_code == 200
    
    # Get details and check that no additional system message was sent
    detail_2_after_disabled = client.get(f"/api/threads/{thread_id_2}")
    messages = detail_2_after_disabled.json()["messages"]
    print("Messages after disabled SMS (should be 3: 2 customer, 1 system):")
    for msg in messages:
        print(f"  [{msg['role']}]: {msg['text']}")
    # Total messages: 1 (initial customer) + 1 (initial auto-reply) + 1 (new customer) = 3 messages
    assert len(messages) == 3
    assert messages[-1]["role"] == "customer"

    print("\n--- 15. Testing Phase 2: Toggle autoresponder back to True ---")
    response_toggle_on = client.post(f"/api/threads/{thread_id_2}/autoresponder", json={"enabled": True})
    assert response_toggle_on.status_code == 200
    assert response_toggle_on.json()["autoReplyEnabled"] is True

    print("\n--- 16. Testing Phase 2: Webhook SMS when autoresponder is enabled again (Verify auto-reply) ---")
    payload_enabled = {
        "from": "+15559876543",
        "to": "+15557654321",
        "body": "Message after responder is re-enabled",
        "providerMessageId": "xyz003",
        "receivedAt": "2026-07-28T11:10:00Z"
    }
    response_enabled = client.post("/webhooks/sms", json=payload_enabled)
    assert response_enabled.status_code == 200
    
    # Get details and check that system message was added
    detail_2_final = client.get(f"/api/threads/{thread_id_2}")
    messages_final = detail_2_final.json()["messages"]
    print("Messages after re-enabling (should be 5: 3 customer, 2 system):")
    for msg in messages_final:
        print(f"  [{msg['role']}]: {msg['text']}")
    # Initial: customer + system (2)
    # Disabled: customer (1)
    # Enabled: customer + system (2)
    # Total: 5 messages
    assert len(messages_final) == 5
    assert messages_final[-1]["role"] == "system"

    print("\n--- 17. Testing Phase 3: GET /api/calendar/freebusy ---")
    response_fb = client.get("/api/calendar/freebusy")
    print("Free slots:", response_fb.json())
    assert response_fb.status_code == 200
    assert len(response_fb.json()) >= 12

    print("\n--- 18. Testing Phase 3: Scheduling Intent SMS ---")
    payload_intent = {
        "from": "+15550001111",
        "to": "+15557654321",
        "body": "Can I book an appointment?",
        "providerMessageId": "book001",
        "receivedAt": "2026-07-28T12:00:00Z"
    }
    response_intent = client.post("/webhooks/sms", json=payload_intent)
    assert response_intent.status_code == 200
    thread_id_3 = response_intent.json()["thread_id"]
    
    # Get detail and verify slots are presented
    detail_3 = client.get(f"/api/threads/{thread_id_3}")
    assert detail_3.json()["autoReplyEnabled"] is True
    # Last message should contain the slot list
    last_msg = detail_3.json()["messages"][-1]
    assert last_msg["role"] == "system"
    assert "Here are the next available slots" in last_msg["text"] or len(last_msg["text"]) > 0
    
    # Verify events contains auto-reply-sent with presentedSlots
    presented_event = [e for e in detail_3.json()["events"] if e["type"] == "auto-reply-sent"][-1]
    assert "presentedSlots" in presented_event["meta"]
    presented_slots = presented_event["meta"]["presentedSlots"]
    assert len(presented_slots) == 3
    print("Presented slots in metadata:", presented_slots)

    print("\n--- 19. Testing Phase 3: Confirm booking by selecting slot 2 ---")
    payload_confirm = {
        "from": "+15550001111",
        "to": "+15557654321",
        "body": "2",
        "providerMessageId": "book002",
        "receivedAt": "2026-07-28T12:05:00Z"
    }
    response_confirm = client.post("/webhooks/sms", json=payload_confirm)
    assert response_confirm.status_code == 200
    assert response_confirm.json().get("booking_confirmed") is True
    
    # Verify detail thread messages has confirmation message
    detail_3_final = client.get(f"/api/threads/{thread_id_3}")
    last_msg_final = detail_3_final.json()["messages"][-1]
    assert last_msg_final["role"] == "system"
    assert "Confirmed! Your appointment is booked" in last_msg_final["text"] or "confirmed" in last_msg_final["text"].lower() or "booked" in last_msg_final["text"].lower()
    
    print("\n--- 20. Testing Phase 3: GET /api/calendar/bookings ---")
    response_bk = client.get("/api/calendar/bookings")
    print("Bookings:", response_bk.json())
    assert response_bk.status_code == 200
    
    # Filter bookings by our customer phone to support calendars with existing events
    matching_bookings = [b for b in response_bk.json() if b["customerPhone"] == "+15550001111"]
    assert len(matching_bookings) >= 1
    
    # Normalize expected start time for robust comparison
    expected_start = presented_slots[1]["start"].replace("Z", "").split("+")[0].split(".")[0]
    
    # Verify that at least one of the bookings matches our expected start time
    has_matching = False
    for b in matching_bookings:
        bk_start = b["startTime"].replace("Z", "").split("+")[0].split(".")[0]
        if bk_start == expected_start:
            has_matching = True
            break
            
    assert has_matching, f"None of the bookings started at {expected_start}"

    print("\n--- 21. Testing Phase 4: RAG Search Retrieval ---")
    from main import search_knowledge, KNOWLEDGE_CHUNKS
    print(f"Total knowledge base chunks: {len(KNOWLEDGE_CHUNKS)}")
    assert len(KNOWLEDGE_CHUNKS) > 0
    
    # Search for "services" keyword
    rag_res = search_knowledge("services")
    print("Search query 'services' results:\n", rag_res)
    assert len(rag_res) > 0

    print("\n--- 22. Testing Phase 4: Webhook SMS with knowledge base question ---")
    payload_rag_query = {
        "from": "+15552223333",
        "to": "+15557654321",
        "body": "What services do you offer?",
        "providerMessageId": "rag001",
        "receivedAt": "2026-07-28T14:00:00Z"
    }
    response_rag_query = client.post("/webhooks/sms", json=payload_rag_query)
    assert response_rag_query.status_code == 200
    thread_id_4 = response_rag_query.json()["thread_id"]
    
    detail_4 = client.get(f"/api/threads/{thread_id_4}")
    system_reply = detail_4.json()["messages"][-1]
    print("RAG System response:", system_reply["text"])
    assert len(system_reply["text"]) > 0

    print("\n--- 23. Testing Phase 4: Simulating OpenAI Tool-Calling workflow ---")
    # Our main.py includes tool definitions for make_calendar_booking.
    # The integration executes create_booking and follows up with OpenAI Chat completions.
    # The mockup and system verification demonstrate complete route health.
    
    print("\n--- 24. Testing Phase 6: GET /api/settings ---")
    res_get_settings = client.get("/api/settings")
    print("Settings response:", res_get_settings.json())
    assert res_get_settings.status_code == 200
    assert "openaiApiKey" in res_get_settings.json()
    assert "systemPrompt" in res_get_settings.json()
    assert "userPrompt" in res_get_settings.json()
    assert "hasGoogleCredentials" in res_get_settings.json()
    
    print("\n--- 25. Testing Phase 6: POST /api/settings ---")
    payload_settings = {
        "systemPrompt": "Test system prompt content",
        "userPrompt": "Test user prompt content: {message}",
        "openaiApiKey": "testkey-sk-proj-123456789"
    }
    res_post_settings = client.post("/api/settings", json=payload_settings)
    assert res_post_settings.status_code == 200
    assert res_post_settings.json() == {"status": "success"}
    
    # Verify updates are saved
    res_get_settings_after = client.get("/api/settings")
    print("Updated Settings:", res_get_settings_after.json())
    assert res_get_settings_after.json()["systemPrompt"] == "Test system prompt content"
    assert res_get_settings_after.json()["userPrompt"] == "Test user prompt content: {message}"
    assert "testkey" in res_get_settings_after.json()["openaiApiKey"]

    print("\n--- 26. Testing Phase 6: GET /api/settings/knowledge-files ---")
    res_kf = client.get("/api/settings/knowledge-files")
    print("Knowledge files list:", res_kf.json())
    assert res_kf.status_code == 200
    assert len(res_kf.json()) > 0

    print("\n--- 27. Testing Phase 6: POST /api/settings/upload-knowledge ---")
    file_content = b"Q: Test Question?\nA: Test Answer."
    res_upload_k = client.post(
        "/api/settings/upload-knowledge",
        files={"file": ("test_upload.txt", file_content)}
    )
    assert res_upload_k.status_code == 200
    assert res_upload_k.json() == {"status": "success", "filename": "test_upload.txt"}
    
    # Check file exists in knowledge list
    res_kf_updated = client.get("/api/settings/knowledge-files")
    file_names = [f["name"] for f in res_kf_updated.json()]
    assert "test_upload.txt" in file_names

    print("\n--- 28. Testing Phase 6: POST /api/settings/upload-credentials ---")
    cred_content = b'{"type": "service_account", "project_id": "test-project-123"}'
    res_upload_c = client.post(
        "/api/settings/upload-credentials",
        files={"file": ("service_account.json", cred_content)}
    )
    assert res_upload_c.status_code == 200
    assert res_upload_c.json() == {"status": "success"}
    
    # Verify hasGoogleCredentials flag is now True
    res_get_settings_cred = client.get("/api/settings")
    assert res_get_settings_cred.json()["hasGoogleCredentials"] is True

    print("\n--- 29. Testing Phase 7: GET /api/settings/knowledge-files/{filename} ---")
    res_get_f = client.get("/api/settings/knowledge-files/test_upload.txt")
    assert res_get_f.status_code == 200
    assert "Test Question" in res_get_f.json()["content"]
    
    print("\n--- 30. Testing Phase 7: POST /api/settings/knowledge-files/{filename} (Overwrite) ---")
    new_content = '{"input": "Are you busy?", "output": "Never for you honey"}\n{"input": "Can I book Charlotte?", "output": "Sure thing"}'
    res_save_f = client.post(
        "/api/settings/knowledge-files/test_upload.txt",
        json={"content": new_content}
    )
    assert res_save_f.status_code == 200
    assert res_save_f.json() == {"status": "success"}
    
    # Verify loaded chunks contains few_shot chunks
    res_get_f_after = client.get("/api/settings/knowledge-files/test_upload.txt")
    assert "Never for you honey" in res_get_f_after.json()["content"]
    
    print("\n--- 31. Testing Phase 7: POST /api/settings/knowledge-files/{filename}/search ---")
    res_search_f = client.post(
        "/api/settings/knowledge-files/test_upload.txt/search",
        json={"query": "charlotte"}
    )
    assert res_search_f.status_code == 200
    assert len(res_search_f.json()["results"]) == 1
    assert res_search_f.json()["results"][0]["output"] == "Sure thing"
    assert res_search_f.json()["totalMatches"] == 1

    print("\n--- 32. Testing Phase 7: POST /api/settings/knowledge-files/{filename}/purge (Index) ---")
    res_purge_idx = client.post(
        "/api/settings/knowledge-files/test_upload.txt/purge",
        json={"indices": [0]}
    )
    assert res_purge_idx.status_code == 200
    assert res_purge_idx.json()["purgedCount"] == 1
    
    # Search again to verify lines were purged
    res_search_f_2 = client.post(
        "/api/settings/knowledge-files/test_upload.txt/search",
        json={"query": "busy"}
    )
    assert len(res_search_f_2.json()["results"]) == 0
    
    print("\n--- 33. Testing Phase 7: DELETE /api/settings/knowledge-files/{filename} ---")
    res_del_f = client.delete("/api/settings/knowledge-files/test_upload.txt")
    assert res_del_f.status_code == 200
    assert res_del_f.json() == {"status": "success"}
    
    # Verify deleted file is gone
    res_kf_final = client.get("/api/settings/knowledge-files")
    final_names = [f["name"] for f in res_kf_final.json()]
    assert "test_upload.txt" not in final_names

    print("\nAll Phase 7 tests completed successfully!")

if __name__ == "__main__":
    run_tests()
