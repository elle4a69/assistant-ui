# SMS Triage Dashboard & Calendar Booking Agent

A full-stack, end-to-end SMS Support Console and Customer SMS Client simulator integrated with Google Calendar and an AI Booking Assistant.

## Project Structure
- `/frontend`: Vite + React + TypeScript + `@assistant-ui/react` + Tailwind CSS.
- `/backend`: FastAPI + SQLAlchemy + SQLite + Google Calendar API integration.

---

## Prerequisites
- **Python**: 3.10+
- **NodeJS**: 18+
- **Google Cloud Platform Project** (Optional): A service account key saved as `service_account.json` in the `/backend` directory with access to the Google Calendar API. If not present, the application automatically falls back to a local SQLite-backed mock calendar so the entire booking workflow can be tested immediately.

---

## How to Run Backend

1. Navigate to the backend directory:
   ```bash
   cd backend
   ```
2. Create and activate a virtual environment:
   ```bash
   python -m venv .venv
   # Windows:
   .venv\Scripts\activate
   # macOS/Linux:
   source .venv/bin/activate
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Start the server (runs on port 8000 by default):
   ```bash
   uvicorn main:app --reload --port 8000
   ```

---

## How to Run Frontend

1. Navigate to the frontend directory:
   ```bash
   cd frontend
   ```
2. Configure Environment Variables:
   - Verify `frontend/.env` is set up. Example values are in `.env.example`:
     ```env
     VITE_API_BASE=http://localhost:8000
     ```
3. Install dependencies and run (Note: If your system has strict disk constraints on the `C:\` drive, redirect npm's cache and temp folders to `F:\` as shown below):
   ```powershell
   # Windows PowerShell:
   New-Item -ItemType Directory -Force -Path f:\Projects\assistant-ui\.npm-tmp
   $env:TMP="f:\Projects\assistant-ui\.npm-tmp"
   $env:TEMP="f:\Projects\assistant-ui\.npm-tmp"
   npm install --cache f:\Projects\assistant-ui\.npm-cache
   npm run dev
   ```
   Otherwise, standard install:
   ```bash
   npm install
   npm run dev
   ```
   The frontend development server will launch on port **`5190`** (at http://localhost:5190).

---

## Testing the System

### 1. Simulate an Inbound Customer SMS
You can simulate a customer sending an SMS by making a POST request to the webhook endpoint. Run the following command:

```bash
curl -X POST http://localhost:8000/webhooks/sms \
  -H "Content-Type: application/json" \
  -d '{
    "from": "+15551234567",
    "to": "+15557654321",
    "body": "Need help scheduling an appointment",
    "providerMessageId": "abc123",
    "receivedAt": "2026-07-28T10:11:12Z"
  }'
```

### 2. Verify AI Auto-Replies
If the autoresponder is enabled, the webhook response will register a system message.
- If the text contains scheduling intent keywords (`book`, `schedule`, `appointment`, `free`, `busy`, `slot`, `when`), the AI assistant will automatically query the Google Calendar (or local SQLite mock) and reply listing the next 3 available 30-minute slots.
- The customer can then reply with `1`, `2`, or `3` to book a slot.

### 3. Verify on UI
- Open http://localhost:5190 in your browser.
- Switch to the **Customer SMS Sim** tab to view the customer chat bubbles and use the quick-action chips to test booking.
- Switch to the **Agent Console** tab to triage conversations (Take Over, Escalate, Resolve, view events) or switch to the **Calendar Manager** workspace panel to see scheduled appointments and availability.
