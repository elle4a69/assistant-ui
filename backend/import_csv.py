import os
import csv
import uuid
import sqlite3
from datetime import datetime, timedelta

# Path resolution
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PERSIST_DIR = "/data" if os.path.exists("/data") else BASE_DIR
DB_FILE = os.path.join(PERSIST_DIR, "assistant.db")
CSV_FILE = os.path.join(BASE_DIR, "messages.csv")

SERVER_PHONE = "+61480807786"

def clean_phone(phone_str):
    phone_str = phone_str.strip().replace(" ", "").replace("-", "")
    if not phone_str:
        return None
    # Normalize to international plus format
    if phone_str.startswith("61") and len(phone_str) >= 11:
        return f"+{phone_str}"
    elif not phone_str.startswith("+"):
        return f"+{phone_str}"
    return phone_str

def main():
    if not os.path.exists(CSV_FILE):
        print(f"CSV file not found at {CSV_FILE}")
        return

    print(f"Connecting to database: {DB_FILE}")
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # Create tables if not exist (just in case)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS threads (
        id TEXT PRIMARY KEY,
        customer_phone TEXT UNIQUE,
        state TEXT,
        priority TEXT,
        sla_due_at TIMESTAMP,
        unread_count INTEGER,
        pending_slots TEXT,
        auto_reply_enabled BOOLEAN,
        created_at TIMESTAMP,
        updated_at TIMESTAMP
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS messages (
        id TEXT PRIMARY KEY,
        thread_id TEXT,
        role TEXT,
        text TEXT,
        at TIMESTAMP,
        FOREIGN KEY(thread_id) REFERENCES threads(id) ON DELETE CASCADE
    )
    """)
    conn.commit()

    # Read CSV records
    with open(CSV_FILE, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    # Sort rows chronologically (Date ascending) so they are imported in order
    def get_date(row):
        try:
            return datetime.strptime(row["Date"].strip(), "%Y-%m-%d %H:%M:%S")
        except Exception:
            return datetime.min

    rows.sort(key=get_date)

    print(f"Loaded {len(rows)} messages from CSV. Importing...")

    inserted_count = 0
    threads_created = 0

    for row in rows:
        sender_raw = row.get("Sender", "").strip()
        recipient_raw = row.get("Recipient Number", "").strip()
        msg_text = row.get("Message", "").strip()
        date_str = row.get("Date", "").strip()
        direction = row.get("Direction", "").strip().lower()

        if not msg_text or not date_str:
            continue

        try:
            msg_at = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
        except Exception as e:
            print(f"Skipping row with invalid date {date_str}: {e}")
            continue

        # Resolve customer phone & message role
        if direction == "inbound":
            customer_phone = clean_phone(sender_raw)
            role = "customer"
        elif direction == "outbound":
            customer_phone = clean_phone(recipient_raw)
            role = "agent"
        else:
            # Fallback based on sender
            if sender_raw == "61480807786" or sender_raw.endswith("61480807786"):
                customer_phone = clean_phone(recipient_raw)
                role = "agent"
            else:
                customer_phone = clean_phone(sender_raw)
                role = "customer"

        if not customer_phone or customer_phone == SERVER_PHONE:
            continue

        # 1. Get or create Thread
        cursor.execute("SELECT id FROM threads WHERE customer_phone = ?", (customer_phone,))
        thread_row = cursor.fetchone()

        if thread_row:
            thread_id = thread_row[0]
        else:
            thread_id = str(uuid.uuid4())
            sla_val = (msg_at + timedelta(hours=24)).isoformat()
            cursor.execute("""
            INSERT INTO threads (id, customer_phone, state, priority, sla_due_at, unread_count, auto_reply_enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (thread_id, customer_phone, "resolved", "medium", sla_val, 0, True, msg_at.isoformat(), msg_at.isoformat()))
            threads_created += 1

        # 2. Check if message already exists
        # To avoid duplicate imports, check if there's a message with same text and timestamp (+/- 2 seconds) on this thread
        cursor.execute("""
        SELECT id FROM messages 
        WHERE thread_id = ? AND text = ? AND role = ?
        """, (thread_id, msg_text, role))
        
        existing_msgs = cursor.fetchall()
        duplicate = False
        if existing_msgs:
            # Check timestamps
            cursor.execute("SELECT at FROM messages WHERE thread_id = ? AND text = ? AND role = ?", (thread_id, msg_text, role))
            for (db_at_str,) in cursor.fetchall():
                try:
                    # Clean up timezone suffix if present
                    clean_db_at = db_at_str.split(".")[0].replace("T", " ").replace("Z", "")
                    db_at = datetime.strptime(clean_db_at, "%Y-%m-%d %H:%M:%S")
                    if abs((msg_at - db_at).total_seconds()) <= 5:
                        duplicate = True
                        break
                except Exception:
                    pass

        if duplicate:
            continue

        # 3. Insert Message
        msg_id = str(uuid.uuid4())
        cursor.execute("""
        INSERT INTO messages (id, thread_id, role, text, at)
        VALUES (?, ?, ?, ?, ?)
        """, (msg_id, thread_id, role, msg_text, msg_at.isoformat()))
        inserted_count += 1

        # 4. Update Thread updated_at
        cursor.execute("UPDATE threads SET updated_at = ? WHERE id = ?", (msg_at.isoformat(), thread_id))

    # Review all threads to calculate state and unread count based on their last message
    cursor.execute("SELECT id FROM threads")
    all_threads = [r[0] for r in cursor.fetchall()]

    for t_id in all_threads:
        cursor.execute("SELECT role, at FROM messages WHERE thread_id = ? ORDER BY at DESC LIMIT 1", (t_id,))
        last_msg = cursor.fetchone()
        if last_msg:
            last_role, _ = last_msg
            if last_role == "customer":
                # Last message is from customer -> needs review
                cursor.execute("UPDATE threads SET state = 'needs-review', unread_count = 1 WHERE id = ?", (t_id,))
            else:
                # Last message is from agent -> resolved
                cursor.execute("UPDATE threads SET state = 'resolved', unread_count = 0 WHERE id = ?", (t_id,))

    conn.commit()
    conn.close()

    print(f"Migration completed! Created {threads_created} new threads and inserted {inserted_count} new messages.")

    # Rename CSV so it is not processed again
    try:
        os.rename(CSV_FILE, f"{CSV_FILE}.imported")
        print("Renamed CSV file to messages.csv.imported")
    except Exception as e:
        print(f"Could not rename CSV file: {e}")

if __name__ == "__main__":
    main()
