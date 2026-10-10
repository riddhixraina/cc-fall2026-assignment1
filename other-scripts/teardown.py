"""Delete the Dining Concierge resources created by deploy.py.

Run this after the demo. OpenSearch keeps billing until the domain is gone.
"""

import json
import os
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

REGION = os.environ.get("AWS_REGION", "us-east-1")
STATE_PATH = Path(__file__).resolve().parent / ".deploy-state.json"


def client(name):
    return boto3.client(name, region_name=REGION)


def ignore(func):
    try:
        func()
    except ClientError as exc:
        print(f"  skip: {exc.response['Error']['Code']} {exc.response['Error'].get('Message', '')}")


def main():
    state = {}
    if STATE_PATH.exists():
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    account = state.get("account") or boto3.client("sts").get_caller_identity()["Account"]
    events = client("events")
    ignore(lambda: events.remove_targets(Rule="LF2EveryMinute", Ids=["LF2"]))
    ignore(lambda: events.delete_rule(Name="LF2EveryMinute"))
    lam = client("lambda")
    for name in ("LF0", "LF1", "LF2"):
        ignore(lambda name=name: lam.delete_function(FunctionName=name))
    if state.get("apiId"):
        ignore(lambda: client("apigateway").delete_rest_api(restApiId=state["apiId"]))
    if state.get("botId"):
        ignore(lambda: client("lexv2-models").delete_bot(botId=state["botId"], skipResourceInUseCheck=True))
    if state.get("queueUrl"):
        ignore(lambda: client("sqs").delete_queue(QueueUrl=state["queueUrl"]))
    else:
        ignore(
            lambda: client("sqs").delete_queue(
                QueueUrl=client("sqs").get_queue_url(QueueName="Q1")["QueueUrl"]
            )
        )
    dynamodb = client("dynamodb")
    for table in ("yelp-restaurants", "user-last-search"):
        ignore(lambda table=table: dynamodb.delete_table(TableName=table))
    ignore(lambda: client("opensearch").delete_domain(DomainName="dining-restaurants"))
    bucket = state.get("bucket") or f"dining-concierge-rrp4822-{account}"
    s3 = client("s3")

    def empty_and_delete():
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket):
            objects = [{"Key": item["Key"]} for item in page.get("Contents") or []]
            if objects:
                s3.delete_objects(Bucket=bucket, Delete={"Objects": objects})
        s3.delete_bucket(Bucket=bucket)

    ignore(empty_and_delete)
    iam = client("iam")
    for role in ("DiningConciergeLambdaRole", "DiningConciergeLexRole"):
        ignore(
            lambda role=role: iam.detach_role_policy(
                RoleName=role,
                PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
            )
        )
        ignore(lambda role=role: iam.delete_role_policy(RoleName=role, PolicyName=f"{role}Policy"))
        ignore(lambda role=role: iam.delete_role(RoleName=role))
    if STATE_PATH.exists():
        STATE_PATH.unlink()
    print("Teardown requested. Confirm in the console that the OpenSearch domain is deleting.")


if __name__ == "__main__":
    main()
