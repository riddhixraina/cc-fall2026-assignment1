"""LF0: chat API. Forwards the user message to Amazon Lex and returns the reply."""

import json
import os
import re
from datetime import datetime, timezone

import boto3

_lex = None

CORS_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token",
    "Access-Control-Allow-Methods": "OPTIONS,POST",
}


def _lex_client():
    global _lex
    if _lex is None:
        _lex = boto3.client("lexv2-runtime")
    return _lex


def _response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": CORS_HEADERS,
        "body": json.dumps(body),
    }


def _parse_event(event):
    if not isinstance(event, dict):
        return {}, True
    if event.get("httpMethod") == "OPTIONS":
        return {"_options": True}, True
    body = event.get("body", event)
    if isinstance(body, str):
        body = json.loads(body or "{}")
    proxy = "httpMethod" in event or "requestContext" in event or isinstance(event.get("body"), str)
    return body or {}, proxy


def _session_id(body):
    messages = body.get("messages") or []
    raw = ""
    if messages:
        unstructured = messages[0].get("unstructured") or {}
        raw = unstructured.get("id") or ""
    cleaned = re.sub(r"[^0-9a-zA-Z._:-]", "", raw)[:100]
    return cleaned or "dining-user"


def _user_text(body):
    messages = body.get("messages") or []
    if not messages:
        return ""
    unstructured = messages[0].get("unstructured") or {}
    return (unstructured.get("text") or "").strip()


def build_api_body(lex_response, session_id):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    messages = []
    for message in lex_response.get("messages") or []:
        text = message.get("content") or ""
        if not text:
            continue
        messages.append(
            {
                "type": "unstructured",
                "unstructured": {"id": session_id, "text": text, "timestamp": now},
            }
        )
    if not messages:
        messages.append(
            {
                "type": "unstructured",
                "unstructured": {
                    "id": session_id,
                    "text": "Sorry, I didn't catch that. Could you say it again?",
                    "timestamp": now,
                },
            }
        )
    return {"messages": messages}


def call_lex(text, session_id, client=None):
    runtime = client or _lex_client()
    return runtime.recognize_text(
        botId=os.environ["LEX_BOT_ID"],
        botAliasId=os.environ["LEX_BOT_ALIAS_ID"],
        localeId=os.environ.get("LEX_LOCALE_ID", "en_US"),
        sessionId=session_id,
        text=text,
    )


def lambda_handler(event, context, lex_client=None):
    body, proxy = _parse_event(event)
    if body.get("_options"):
        return _response(200, {})

    text = _user_text(body)
    session_id = _session_id(body)
    if not text:
        error = {"code": 400, "message": "A message is required."}
        return _response(400, error) if proxy else error

    try:
        lex_response = call_lex(text, session_id, lex_client)
    except Exception as exc:
        print(f"Lex RecognizeText failed: {exc}")
        error = {"code": 500, "message": "The concierge is unavailable. Please try again."}
        return _response(500, error) if proxy else error

    payload = build_api_body(lex_response, session_id)
    return _response(200, payload) if proxy else payload
