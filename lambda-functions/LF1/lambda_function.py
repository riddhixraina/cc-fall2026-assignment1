"""LF1: Amazon Lex code hook for the Dining Concierge bot."""

import os

import boto3

from dining_logic import handle_lex_event

_sqs = None
_table = None


def _sqs_client():
    global _sqs
    if _sqs is None:
        _sqs = boto3.client("sqs")
    return _sqs


def _state_table():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb").Table(os.environ.get("STATE_TABLE", "user-last-search"))
    return _table


class AwsDeps:
    def get_last_search(self, user_id):
        if not user_id:
            return None
        try:
            response = _state_table().get_item(Key={"userId": user_id})
        except Exception as exc:
            print(f"DynamoDB get_item failed: {exc}")
            return None
        return response.get("Item")

    def enqueue(self, message):
        queue_url = os.environ["SQS_QUEUE_URL"]
        import json

        _sqs_client().send_message(QueueUrl=queue_url, MessageBody=json.dumps(message))
        print(f"Queued suggestion request for {message.get('email')}")


def lambda_handler(event, context):
    return handle_lex_event(event, AwsDeps())
