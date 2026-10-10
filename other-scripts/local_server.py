"""Run the chat UI and the Lex hook locally, without AWS.

  python other-scripts/local_server.py
  open http://127.0.0.1:8080/chat.html

Suggestion emails are printed here and stored at /last-email.
"""

import json
import os
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lambda-functions" / "LF1"))
sys.path.insert(0, str(ROOT / "lambda-functions" / "LF2"))

from dining_logic import handle_lex_event, put_slot
from suggestions import choose_restaurant_ids, email_subject, format_suggestions_email

FRONTEND = ROOT / "frontend"
SAMPLE_PATH = Path(__file__).resolve().parent / "sample_restaurants.json"
RESTAURANTS = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
SESSIONS = {}
LAST_EMAILS = {}


class MemoryDeps:
    def __init__(self):
        self.last_by_user = {}
        self.queue = []

    def get_last_search(self, user_id):
        return self.last_by_user.get(user_id)

    def enqueue(self, message):
        self.queue.append(message)


DEPS = MemoryDeps()


def classify(text, state):
    lowered = text.lower().strip(" !.?")
    if state.get("active"):
        return "DiningSuggestionsIntent"
    if "thank" in lowered:
        return "ThankYouIntent"
    if lowered in {"hi", "hello", "hey", "good morning", "good evening", "hi there"}:
        return "GreetingIntent"
    if any(word in lowered for word in ["restaurant", "eat", "food", "dinner", "suggestion", "hungry", "cuisine", "dine"]):
        return "DiningSuggestionsIntent"
    return "FallbackIntent"


def fulfill_queue():
    while DEPS.queue:
        request = DEPS.queue.pop(0)
        if request.get("usePrevious") and request.get("previousRestaurantIds"):
            selected = list(request["previousRestaurantIds"])[:3]
        else:
            candidates = [
                item["BusinessID"] for item in RESTAURANTS if item["Cuisine"] == (request.get("cuisine") or "").lower()
            ]
            selected = choose_restaurant_ids(candidates)
        by_id = {item["BusinessID"]: item for item in RESTAURANTS}
        chosen = [by_id[item] for item in selected if item in by_id]
        body = format_suggestions_email(request, chosen)
        subject = email_subject(request)
        LAST_EMAILS[request["userId"]] = {"subject": subject, "body": body, "to": request.get("email")}
        if chosen:
            DEPS.last_by_user[request["userId"]] = {
                "location": request.get("location"),
                "cuisine": (request.get("cuisine") or "").lower(),
                "restaurantIds": [item["BusinessID"] for item in chosen],
            }
        print("\n----- SES email (local) -----")
        print(f"To: {request.get('email')}")
        print(f"Subject: {subject}")
        print(body)
        print("-----------------------------\n")


def converse(session_id, text):
    state = SESSIONS.setdefault(session_id, {"slots": {}, "session": {}, "elicit": None, "active": False})
    intent = classify(text, state)
    slots = state["slots"]
    if intent == "DiningSuggestionsIntent" and state.get("elicit"):
        slots = put_slot(slots, state["elicit"], text)
    elif intent != "DiningSuggestionsIntent":
        slots = {}
    response = handle_lex_event(
        {
            "sessionId": session_id,
            "inputTranscript": text,
            "invocationSource": "DialogCodeHook",
            "sessionState": {
                "intent": {"name": intent, "slots": slots, "state": "InProgress", "confirmationState": "None"},
                "sessionAttributes": state.get("session") or {},
            },
        },
        DEPS,
    )
    action = response["sessionState"]["dialogAction"]
    if action["type"] == "Delegate":
        response = handle_lex_event(
            {
                "sessionId": session_id,
                "inputTranscript": text,
                "invocationSource": "FulfillmentCodeHook",
                "sessionState": response["sessionState"],
            },
            DEPS,
        )
        action = response["sessionState"]["dialogAction"]
        fulfill_queue()
    state["slots"] = response["sessionState"]["intent"].get("slots") or {}
    state["session"] = response["sessionState"].get("sessionAttributes") or {}
    state["elicit"] = action.get("slotToElicit")
    state["active"] = action["type"] == "ElicitSlot"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    messages = []
    for message in response.get("messages") or []:
        if message.get("content"):
            messages.append(
                {
                    "type": "unstructured",
                    "unstructured": {"id": session_id, "text": message["content"], "timestamp": now},
                }
            )
    return {"messages": messages}


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, body, content_type):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self._send(200, b"", "text/plain")

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/last-email":
            session = "user"
            if "session=" in self.path:
                session = self.path.split("session=", 1)[1].split("&", 1)[0]
            self._send(200, json.dumps(LAST_EMAILS.get(session, {})), "application/json")
            return
        if path in {"/", "/chat.html"}:
            path = "/chat.html"
        relative = path.lstrip("/")
        target = (FRONTEND / relative).resolve()
        if not str(target).startswith(str(FRONTEND.resolve())) or not target.is_file():
            self._send(404, "Not found", "text/plain")
            return
        kind = "text/plain"
        if target.suffix == ".html":
            kind = "text/html"
        elif target.suffix == ".css":
            kind = "text/css"
        elif target.suffix == ".js":
            kind = "application/javascript"
        self._send(200, target.read_bytes(), kind)

    def do_POST(self):
        if self.path.split("?", 1)[0] != "/v1/chatbot":
            self._send(404, "Not found", "text/plain")
            return
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        message = ((payload.get("messages") or [{}])[0].get("unstructured") or {})
        text = message.get("text") or ""
        session_id = message.get("id") or "dining-user"
        self._send(200, json.dumps(converse(session_id, text)), "application/json")

    def log_message(self, fmt, *args):
        print("[http]", fmt % args)


def main():
    port = int(os.environ.get("PORT", "8080"))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Dining Concierge local chat: http://127.0.0.1:{port}/chat.html")
    server.serve_forever()


if __name__ == "__main__":
    main()
