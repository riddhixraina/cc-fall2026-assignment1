"""LF2: queue worker. Polls SQS, looks up restaurants, and emails suggestions."""

import base64
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

from suggestions import choose_restaurant_ids, format_suggestions_email, email_subject

_sqs = None
_dynamodb = None
_ses = None


def _clients():
    global _sqs, _dynamodb, _ses
    if _sqs is None:
        _sqs = boto3.client("sqs")
        _dynamodb = boto3.resource("dynamodb")
        _ses = boto3.client("ses")
    return _sqs, _dynamodb, _ses


def _search_opensearch(cuisine):
    endpoint = os.environ["OPENSEARCH_ENDPOINT"].rstrip("/")
    url = f"{endpoint}/restaurants/_search"
    body = json.dumps(
        {
            "size": 200,
            "_source": ["RestaurantID", "Cuisine"],
            "query": {"term": {"Cuisine": cuisine.lower()}},
        }
    )
    data = body.encode("utf-8")
    headers = {"Content-Type": "application/json"}
    user = os.environ.get("OPENSEARCH_USER")
    password = os.environ.get("OPENSEARCH_PASSWORD")
    if user and password:
        token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {token}"
        request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    else:
        credentials = boto3.Session().get_credentials().get_frozen_credentials()
        aws_request = AWSRequest(method="POST", url=url, data=data, headers=headers)
        SigV4Auth(credentials, "es", os.environ.get("AWS_REGION", "us-east-1")).add_auth(aws_request)
        request = urllib.request.Request(url, data=data, headers=dict(aws_request.headers), method="POST")
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    hits = ((payload.get("hits") or {}).get("hits")) or []
    return [hit.get("_source", {}).get("RestaurantID") for hit in hits if hit.get("_source")]


def _restaurant(table, business_id):
    response = table.get_item(Key={"BusinessID": business_id})
    return response.get("Item")


def _save_last_search(table, request, restaurant_ids):
    table.put_item(
        Item={
            "userId": request["userId"],
            "location": request.get("location") or "",
            "cuisine": (request.get("cuisine") or "").lower(),
            "restaurantIds": restaurant_ids,
            "updatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    )


def process_request(request, search_ids, fetch_restaurant, send_email, save_last_search):
    """Resolve ids, format the email, send it, and remember the search."""
    if request.get("usePrevious") and request.get("previousRestaurantIds"):
        selected = list(request["previousRestaurantIds"])[:3]
    else:
        selected = choose_restaurant_ids(search_ids((request.get("cuisine") or "").lower()))
    restaurants = []
    for business_id in selected:
        item = fetch_restaurant(business_id)
        if item:
            restaurants.append(item)
    body = format_suggestions_email(request, restaurants)
    send_email(request["email"], email_subject(request), body)
    if request.get("userId") and restaurants:
        save_last_search(request, [item["BusinessID"] for item in restaurants if item.get("BusinessID")])
    return {"email": request.get("email"), "count": len(restaurants)}


def _handle_message(body):
    request = json.loads(body)
    sqs, dynamodb, ses = _clients()
    restaurants_table = dynamodb.Table(os.environ.get("RESTAURANTS_TABLE", "yelp-restaurants"))
    state_table = dynamodb.Table(os.environ.get("STATE_TABLE", "user-last-search"))
    sender = os.environ["SENDER_EMAIL"]

    def send_email(recipient, subject, text):
        ses.send_email(
            Source=sender,
            Destination={"ToAddresses": [recipient]},
            Message={"Subject": {"Data": subject}, "Body": {"Text": {"Data": text}}},
        )

    return process_request(
        request,
        _search_opensearch,
        lambda business_id: _restaurant(restaurants_table, business_id),
        send_email,
        lambda req, ids: _save_last_search(state_table, req, ids),
    )


def _pull_messages():
    sqs, _, _ = _clients()
    queue_url = os.environ["SQS_QUEUE_URL"]
    response = sqs.receive_message(
        QueueUrl=queue_url,
        MaxNumberOfMessages=5,
        WaitTimeSeconds=0,
        VisibilityTimeout=30,
    )
    messages = response.get("Messages") or []
    results = []
    for message in messages:
        try:
            results.append(_handle_message(message["Body"]))
            sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=message["ReceiptHandle"])
        except Exception as exc:
            print(f"Failed to process message {message.get('MessageId')}: {exc}")
    return results


def lambda_handler(event, context):
    if not os.environ.get("OPENSEARCH_ENDPOINT"):
        print("OPENSEARCH_ENDPOINT is not set, so there is nothing to recommend yet.")
        return {"processed": []}
    records = (event or {}).get("Records") or []
    if records and records[0].get("eventSource") == "aws:sqs":
        results = []
        for record in records:
            results.append(_handle_message(record["body"]))
        return {"processed": results}
    try:
        processed = _pull_messages()
    except urllib.error.HTTPError as exc:
        print(f"OpenSearch HTTP error: {exc.code} {exc.read().decode('utf-8', 'ignore')}")
        raise
    return {"processed": processed}
