"""Load RestaurantID and Cuisine into the OpenSearch index `restaurants`.

OpenSearch 2.x no longer supports Elasticsearch mapping types. Each document
stands in for the assignment's Restaurant type and is stored with
RestaurantID and Cuisine.

Usage:
  python other-scripts/load_opensearch.py --source dynamodb
  python other-scripts/load_opensearch.py --source file --input other-scripts/data/restaurants.json
"""

import argparse
import base64
import json
import os
import urllib.error
import urllib.request

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest


def request(method, url, body=None, region="us-east-1"):
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    user = os.environ.get("OPENSEARCH_USER")
    password = os.environ.get("OPENSEARCH_PASSWORD")
    if user and password:
        token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {token}"
        http_request = urllib.request.Request(url, data=data, headers=headers, method=method)
    else:
        credentials = boto3.Session().get_credentials().get_frozen_credentials()
        aws_request = AWSRequest(method=method, url=url, data=data, headers=headers)
        SigV4Auth(credentials, "es", region).add_auth(aws_request)
        http_request = urllib.request.Request(url, data=data, headers=dict(aws_request.headers), method=method)
    with urllib.request.urlopen(http_request, timeout=60) as response:
        raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def load_records(source, table_name, input_path):
    if source == "file":
        with open(input_path, encoding="utf-8") as handle:
            return json.load(handle)
    table = boto3.resource("dynamodb").Table(table_name)
    records = []
    response = table.scan()
    records.extend(response.get("Items") or [])
    while response.get("LastEvaluatedKey"):
        response = table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
        records.extend(response.get("Items") or [])
    return records


def bulk_body(records):
    lines = []
    for record in records:
        business_id = record.get("BusinessID") or record.get("RestaurantID")
        cuisine = (record.get("Cuisine") or "").lower()
        if not business_id or not cuisine:
            continue
        lines.append(json.dumps({"index": {"_index": "restaurants", "_id": business_id}}))
        lines.append(json.dumps({"RestaurantID": business_id, "Cuisine": cuisine, "type": "Restaurant"}))
    return ("\n".join(lines) + "\n").encode("utf-8") if lines else b""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=["dynamodb", "file"], default="dynamodb")
    parser.add_argument("--table", default="yelp-restaurants")
    parser.add_argument("--input")
    parser.add_argument("--endpoint", default=os.environ.get("OPENSEARCH_ENDPOINT", ""))
    args = parser.parse_args()
    endpoint = args.endpoint.rstrip("/")
    if not endpoint:
        raise SystemExit("Set OPENSEARCH_ENDPOINT or pass --endpoint.")
    records = load_records(args.source, args.table, args.input)
    mapping = {
        "mappings": {
            "properties": {
                "RestaurantID": {"type": "keyword"},
                "Cuisine": {"type": "keyword"},
                "type": {"type": "keyword"},
            }
        }
    }
    try:
        request("PUT", f"{endpoint}/restaurants", mapping)
    except urllib.error.HTTPError as exc:
        if exc.code != 400:
            raise
        print("Index restaurants already exists.")
    payload = bulk_body(records)
    if not payload:
        raise SystemExit("No restaurant records to index.")
    user = os.environ.get("OPENSEARCH_USER")
    password = os.environ.get("OPENSEARCH_PASSWORD")
    headers = {"Content-Type": "application/x-ndjson"}
    if user and password:
        token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {token}"
        http_request = urllib.request.Request(
            f"{endpoint}/_bulk", data=payload, headers=headers, method="POST"
        )
    else:
        credentials = boto3.Session().get_credentials().get_frozen_credentials()
        aws_request = AWSRequest(method="POST", url=f"{endpoint}/_bulk", data=payload, headers=headers)
        SigV4Auth(credentials, "es", os.environ.get("AWS_REGION", "us-east-1")).add_auth(aws_request)
        http_request = urllib.request.Request(
            f"{endpoint}/_bulk", data=payload, headers=dict(aws_request.headers), method="POST"
        )
    with urllib.request.urlopen(http_request, timeout=120) as response:
        result = json.loads(response.read().decode("utf-8"))
    if result.get("errors"):
        raise SystemExit("OpenSearch bulk indexing reported errors.")
    print(f"Indexed {len(records)} restaurants into restaurants.")


if __name__ == "__main__":
    main()
