# AI SMS Relay - Upgraded iOS Shortcut Action Guide

This document provides the complete, action-by-action blueprint for the upgraded **AI SMS Relay** iOS Shortcut (`AI_SMS_Relay_Upgraded.shortcut`).

---

## Action-by-Action Workflow Blueprint

```yaml
Action 0: [Get Contents of URL] (Wakeup Telemetry)
  URL: "https://assistant-ui-hub.fly.dev/webhook/log"
  Method: POST
  Headers:
    Content-Type: "application/json"
  Request Body (JSON):
    event: "DOORBELL_WOKE_UP"
    source: "ios_shortcut"

Action 1: [Get Contents of URL] (Dequeue Outbound Relay)
  URL: "https://assistant-ui-hub.fly.dev/webhook/outbound"
  Method: GET

Action 2: [Get Dictionary from Input]
  Input: Contents of URL (Action 1)
  Variable Name: "Relay Dictionary"

Action 3: [Get Dictionary Value]
  Key: "has_message"
  Input: Relay Dictionary
  Variable Name: "Has Message"

Action 4: [If]
  Condition: "Has Message" is "yes"

Action 5: [Get Dictionary Value]
  Key: "messages"
  Input: Relay Dictionary
  Variable Name: "Message Batch List"

Action 6: [Repeat with Each]
  List: Message Batch List (Action 5)

  Action 7: [Get Dictionary Value]
    Key: "phone"
    Input: Repeat Item
    Variable Name: "Customer Phone"

  Action 8: [Get Dictionary Value]
    Key: "reply"
    Input: Repeat Item
    Variable Name: "AI Reply"

  Action 9: [Get Dictionary Value]
    Key: "image_url"
    Input: Repeat Item
    Variable Name: "Image URL"

  Action 10: [Get Dictionary Value]
    Key: "id"
    Input: Repeat Item
    Variable Name: "Message ID"

  Action 11: [If]
    Condition: "Image URL" has any value

    Action 12: [Get Contents of URL] (MMS Image Download)
      URL: Image URL (Action 9)
      Method: GET                # <-- CRITICAL BUGFIX: GET instead of POST
      Request Body: None         # <-- CRITICAL BUGFIX: No JSON body
      Variable Name: "Downloaded Media"

    Action 13: [Send Message] (MMS Dispatch)
      Recipients: Customer Phone (Action 7)
      Message: Downloaded Media + AI Reply
      Show When Run: False

  Action 14: [Otherwise]         # <-- CRITICAL BUGFIX: Clean branch for SMS

    Action 15: [Send Message] (SMS Dispatch)
      Recipients: Customer Phone (Action 7)
      Message: AI Reply (Action 8)
      Show When Run: False

  Action 16: [End If]

  Action 17: [Get Contents of URL] (Message Telemetry Log)
    URL: "https://assistant-ui-hub.fly.dev/webhook/log"
    Method: POST
    Request Body (JSON):
      event: "SENT_HANDOFF"
      phone: Customer Phone
      message_id: Message ID

  Action 18: [Get Contents of URL] (Delivery Receipt)
    URL: "https://assistant-ui-hub.fly.dev/webhook/sent"
    Method: POST
    Request Body (JSON):
      message_id: Message ID

Action 19: [End Repeat]

Action 20: [End If]
```

---

## Key Bugfixes & Upgrades Summary

1. **HTTP Method Fix on Image URL**:
   - **Previous Bug**: Attempted `POST` with a JSON payload to the raw image URL (`https://placehold.co/600x400.png`), causing HTTP 405 crashes.
   - **Resolution**: Changed to standard HTTP `GET` with empty headers and no body.
2. **Proper Conditional Branching**:
   - **Previous Bug**: Missing `Otherwise` branch caused pure SMS to execute unconditionally even when MMS was dispatched.
   - **Resolution**: Separated into `If Image URL has any value` (MMS) and `Otherwise` (standard SMS).
3. **Variable Renaming**:
   - Renamed variables for human readability inside the Shortcuts UI:
     - `Customer Phone`
     - `AI Reply`
     - `Image URL`
     - `Message ID`
     - `Downloaded Media`
4. **Real-time Server Telemetry**:
   - Added startup beacon (`DOORBELL_WOKE_UP`) and per-message dispatch telemetry (`SENT_HANDOFF`) streaming directly into Fly.io server logs.

---

## Download Links

- Binary Shortcut: `https://assistant-ui-hub.fly.dev/AI_SMS_Relay_Upgraded.shortcut`
- XML Plist: `https://assistant-ui-hub.fly.dev/AI_SMS_Relay_Upgraded.plist`
