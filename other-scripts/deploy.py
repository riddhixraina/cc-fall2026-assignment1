"""Create the Dining Concierge resources in us-east-1.

OpenSearch is off unless you pass --with-opensearch. A t3.small.search node
is billed for every hour it exists, so run other-scripts/teardown.py as soon
as the demo is recorded.

Usage:
  python other-scripts/deploy.py --sender-email you@nyu.edu
  python other-scripts/deploy.py --sender-email you@nyu.edu --with-opensearch
"""

import argparse
import io
import json
import os
import secrets
import string
import time
import zipfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
REGION = os.environ.get("AWS_REGION", "us-east-1")
STATE_PATH = Path(__file__).resolve().parent / ".deploy-state.json"

LAMBDA_ROLE = "DiningConciergeLambdaRole"
LEX_ROLE = "DiningConciergeLexRole"
QUEUE_NAME = "Q1"
RESTAURANTS_TABLE = "yelp-restaurants"
STATE_TABLE = "user-last-search"
BOT_NAME = "DiningConcierge"
API_NAME = "DiningConciergeAPI"
RULE_NAME = "LF2EveryMinute"
DOMAIN_NAME = "dining-restaurants"

GREETING_UTTERANCES = ["Hello", "Hi", "Hey", "Good morning", "Good evening", "Hi there"]
THANKS_UTTERANCES = ["Thank you", "Thanks", "Thanks a lot", "Thank you so much", "I appreciate it"]
DINING_UTTERANCES = [
    "I need some restaurant suggestions",
    "I want restaurant recommendations",
    "Find me a place to eat",
    "Can you suggest a restaurant",
    "I am looking for food",
    "Suggest a restaurant for dinner",
    "I need help finding a restaurant",
]
SLOT_PROMPTS = {
    "Location": "What city or city area are you looking to dine in?",
    "Cuisine": "What cuisine would you like to try?",
    "NumberOfPeople": "How many people are in your party?",
    "DiningDate": "What date?",
    "DiningTime": "What time?",
    "Email": "What email should I use for the suggestions?",
    "SameRecommendation": "Would you like the same recommendations as last time?",
}


def client(name):
    return boto3.client(name, region_name=REGION)


def account_id():
    return boto3.client("sts").get_caller_identity()["Account"]


def save_state(state):
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {}


def ignore_exists(func):
    try:
        return func()
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code in {"ResourceConflictException", "ConflictException", "EntityAlreadyExists", "ResourceInUseException"}:
            print(f"Already exists: {code}")
            return None
        raise


def wait_until(describe, key, ready, failed=("Failed",), attempts=80, delay=5):
    for _ in range(attempts):
        described = describe()
        status = described[key]
        print(f"  status {key}={status}")
        if status in ready:
            return described
        if status in failed:
            raise SystemExit(f"Resource failed: {json.dumps(described, default=str)[:1000]}")
        time.sleep(delay)
    raise SystemExit(f"Timed out waiting for {key} in {ready}")


def ensure_role(iam, name, trust_service, policy_document):
    trust = {
        "Version": "2012-10-17",
        "Statement": [
            {"Effect": "Allow", "Principal": {"Service": trust_service}, "Action": "sts:AssumeRole"}
        ],
    }
    try:
        role = iam.create_role(RoleName=name, AssumeRolePolicyDocument=json.dumps(trust))
        created = True
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "EntityAlreadyExists":
            raise
        role = iam.get_role(RoleName=name)
        created = False
    iam.put_role_policy(RoleName=name, PolicyName=f"{name}Policy", PolicyDocument=json.dumps(policy_document))
    if name == LAMBDA_ROLE:
        ignore_exists(
            lambda: iam.attach_role_policy(
                RoleName=name,
                PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
            )
        )
    if created:
        print(f"Waiting for IAM role {name} to become usable")
        time.sleep(12)
    return role["Role"]["Arn"]


def ensure_tables(dynamodb):
    tables = {
        RESTAURANTS_TABLE: "BusinessID",
        STATE_TABLE: "userId",
    }
    for name, key in tables.items():
        try:
            dynamodb.create_table(
                TableName=name,
                AttributeDefinitions=[{"AttributeName": key, "AttributeType": "S"}],
                KeySchema=[{"AttributeName": key, "KeyType": "HASH"}],
                BillingMode="PAY_PER_REQUEST",
            )
            print(f"Creating table {name}")
            waiter = dynamodb.get_waiter("table_exists")
            waiter.wait(TableName=name)
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ResourceInUseException":
                raise
            print(f"Table {name} already exists")


def ensure_queue(sqs):
    response = sqs.create_queue(
        QueueName=QUEUE_NAME,
        Attributes={"VisibilityTimeout": "30", "MessageRetentionPeriod": "86400"},
    )
    return response["QueueUrl"]


def zip_lambda(directory):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in Path(directory).glob("*.py"):
            archive.write(path, path.name)
    return buffer.getvalue()


def ensure_lambda(lam, name, directory, role_arn, environment, timeout):
    code = zip_lambda(directory)
    try:
        response = lam.create_function(
            FunctionName=name,
            Runtime="python3.12",
            Role=role_arn,
            Handler="lambda_function.lambda_handler",
            Code={"ZipFile": code},
            Timeout=timeout,
            MemorySize=256,
            Environment={"Variables": environment},
            Publish=True,
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceConflictException":
            raise
        lam.update_function_code(FunctionName=name, ZipFile=code)
        time.sleep(3)
        lam.update_function_configuration(
            FunctionName=name,
            Timeout=timeout,
            Environment={"Variables": environment},
        )
        response = lam.get_function(FunctionName=name)["Configuration"]
    return response["FunctionArn"]


def allow_invoke(lam, function_name, statement_id, principal, source_arn):
    try:
        lam.add_permission(
            FunctionName=function_name,
            StatementId=statement_id,
            Action="lambda:InvokeFunction",
            Principal=principal,
            SourceArn=source_arn,
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceConflictException":
            raise


def prompt(text):
    return {
        "messageGroups": [{"message": {"plainTextMessage": {"value": text}}}],
        "maxRetries": 4,
        "allowInterrupt": True,
    }


def find_bot(lex, name):
    token = None
    while True:
        kwargs = {"maxResults": 50}
        if token:
            kwargs["nextToken"] = token
        page = lex.list_bots(**kwargs)
        for summary in page.get("botSummaries", []):
            if summary["botName"] == name:
                return summary["botId"]
        token = page.get("nextToken")
        if not token:
            return None


def create_intent(lex, bot_id, name, utterances):
    response = lex.create_intent(
        botId=bot_id,
        botVersion="DRAFT",
        localeId="en_US",
        intentName=name,
        sampleUtterances=[{"utterance": utterance} for utterance in utterances],
        dialogCodeHook={"enabled": True},
        fulfillmentCodeHook={"enabled": True, "active": True},
    )
    return response["intentId"]


def create_slot(lex, bot_id, intent_id, name, required):
    response = lex.create_slot(
        botId=bot_id,
        botVersion="DRAFT",
        localeId="en_US",
        intentId=intent_id,
        slotName=name,
        slotTypeId="AMAZON.FreeFormInput",
        valueElicitationSetting={
            "slotConstraint": "Required" if required else "Optional",
            "promptSpecification": prompt(SLOT_PROMPTS[name]),
        },
    )
    return response["slotId"]


def ensure_bot(lex, lam, lex_role_arn, lf1_arn, account):
    bot_id = find_bot(lex, BOT_NAME)
    if not bot_id:
        created = lex.create_bot(
            botName=BOT_NAME,
            roleArn=lex_role_arn,
            dataPrivacy={"childDirected": False},
            idleSessionTTLInSeconds=300,
            description="Dining Concierge for Cloud Computing Fall 2026",
        )
        bot_id = created["botId"]
        wait_until(lambda: lex.describe_bot(botId=bot_id), "botStatus", {"Available"})
        lex.create_bot_locale(
            botId=bot_id,
            botVersion="DRAFT",
            localeId="en_US",
            nluIntentConfidenceThreshold=0.4,
        )
        wait_until(
            lambda: lex.describe_bot_locale(botId=bot_id, botVersion="DRAFT", localeId="en_US"),
            "botLocaleStatus",
            {"NotBuilt", "Built", "ReadyExpressTesting"},
        )
        create_intent(lex, bot_id, "GreetingIntent", GREETING_UTTERANCES)
        create_intent(lex, bot_id, "ThankYouIntent", THANKS_UTTERANCES)
        try:
            lex.create_intent(
                botId=bot_id,
                botVersion="DRAFT",
                localeId="en_US",
                intentName="FallbackIntent",
                parentIntentSignature="AMAZON.FallbackIntent",
                dialogCodeHook={"enabled": True},
                fulfillmentCodeHook={"enabled": True, "active": True},
            )
        except ClientError as exc:
            print(f"Fallback intent: {exc.response['Error']['Code']}")
        dining_id = create_intent(lex, bot_id, "DiningSuggestionsIntent", DINING_UTTERANCES)
        priorities = []
        for index, name in enumerate(
            ["Location", "Cuisine", "NumberOfPeople", "DiningDate", "DiningTime", "Email", "SameRecommendation"],
            start=1,
        ):
            slot_id = create_slot(lex, bot_id, dining_id, name, required=(name != "SameRecommendation"))
            priorities.append({"priority": index, "slotId": slot_id})
        lex.update_intent(
            botId=bot_id,
            botVersion="DRAFT",
            localeId="en_US",
            intentId=dining_id,
            intentName="DiningSuggestionsIntent",
            sampleUtterances=[{"utterance": utterance} for utterance in DINING_UTTERANCES],
            dialogCodeHook={"enabled": True},
            fulfillmentCodeHook={"enabled": True, "active": True},
            slotPriorities=priorities,
        )
    allow_invoke(
        lam,
        "LF1",
        "lex-invoke",
        "lexv2.amazonaws.com",
        f"arn:aws:lex:{REGION}:{account}:bot-alias/{bot_id}/*",
    )
    print("Building Lex locale")
    lex.build_bot_locale(botId=bot_id, botVersion="DRAFT", localeId="en_US")
    wait_until(
        lambda: lex.describe_bot_locale(botId=bot_id, botVersion="DRAFT", localeId="en_US"),
        "botLocaleStatus",
        {"Built", "ReadyExpressTesting"},
        attempts=90,
    )
    version = lex.create_bot_version(
        botId=bot_id,
        botVersionLocaleSpecification={"en_US": {"sourceBotVersion": "DRAFT"}},
        description="Assignment 1 release",
    )["botVersion"]
    wait_until(lambda: lex.describe_bot_version(botId=bot_id, botVersion=version), "botStatus", {"Available"})
    alias = lex.create_bot_alias(
        botId=bot_id,
        botAliasName="prod",
        botVersion=version,
        botAliasLocaleSettings={
            "en_US": {
                "enabled": True,
                "codeHookSpecification": {
                    "lambdaCodeHook": {"lambdaARN": lf1_arn, "codeHookInterfaceVersion": "1.0"}
                },
            }
        },
    )
    alias_id = alias["botAliasId"]
    wait_until(
        lambda: lex.describe_bot_alias(botId=bot_id, botAliasId=alias_id),
        "botAliasStatus",
        {"Available"},
    )
    return bot_id, alias_id


def ensure_api(apigw, lam, lf0_arn, account):
    swagger = (Path(__file__).resolve().parent / "swagger.yaml").read_bytes()
    api_id = apigw.import_rest_api(body=swagger, failOnWarnings=False)["id"]
    resources = apigw.get_resources(restApiId=api_id, limit=50)["items"]
    resource_id = next(item["id"] for item in resources if item["path"] == "/chatbot")
    uri = f"arn:aws:apigateway:{REGION}:lambda:path/2015-03-31/functions/{lf0_arn}/invocations"
    apigw.put_integration(
        restApiId=api_id,
        resourceId=resource_id,
        httpMethod="POST",
        type="AWS_PROXY",
        integrationHttpMethod="POST",
        uri=uri,
    )
    apigw.put_integration(
        restApiId=api_id,
        resourceId=resource_id,
        httpMethod="OPTIONS",
        type="MOCK",
        requestTemplates={"application/json": '{"statusCode": 200}'},
    )
    apigw.put_integration_response(
        restApiId=api_id,
        resourceId=resource_id,
        httpMethod="OPTIONS",
        statusCode="200",
        responseParameters={
            "method.response.header.Access-Control-Allow-Headers": "'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token'",
            "method.response.header.Access-Control-Allow-Methods": "'OPTIONS,POST'",
            "method.response.header.Access-Control-Allow-Origin": "'*'",
        },
    )
    apigw.create_deployment(restApiId=api_id, stageName="v1")
    allow_invoke(
        lam,
        "LF0",
        "apigateway-invoke",
        "apigateway.amazonaws.com",
        f"arn:aws:execute-api:{REGION}:{account}:{api_id}/*/*/*",
    )
    return api_id


def content_type(path):
    suffix = path.suffix.lower()
    return {
        ".html": "text/html",
        ".css": "text/css",
        ".js": "application/javascript",
        ".json": "application/json",
        ".png": "image/png",
        ".gif": "image/gif",
        ".svg": "image/svg+xml",
    }.get(suffix, "application/octet-stream")


def publish_frontend(s3, api_id, account):
    bucket = f"dining-concierge-rrp4822-{account}"
    try:
        s3.create_bucket(Bucket=bucket)
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
            raise
    s3.delete_public_access_block(Bucket=bucket)
    s3.put_bucket_website(Bucket=bucket, WebsiteConfiguration={"IndexDocument": {"Suffix": "chat.html"}})
    s3.put_bucket_policy(
        Bucket=bucket,
        Policy=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Sid": "PublicRead",
                        "Effect": "Allow",
                        "Principal": "*",
                        "Action": "s3:GetObject",
                        "Resource": f"arn:aws:s3:::{bucket}/*",
                    }
                ],
            }
        ),
    )
    endpoint = f"https://{api_id}.execute-api.{REGION}.amazonaws.com/v1"
    frontend = ROOT / "frontend"
    for path in frontend.rglob("*"):
        if not path.is_file() or path.name == ".DS_Store":
            continue
        body = path.read_bytes()
        if path.name == "config.js":
            text = body.decode("utf-8").replace(
                "https://REPLACE_ME.execute-api.us-east-1.amazonaws.com/v1",
                endpoint,
            )
            body = text.encode("utf-8")
        key = str(path.relative_to(frontend)).replace("\\", "/")
        s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType=content_type(path))
    website = f"http://{bucket}.s3-website-{REGION}.amazonaws.com"
    print(f"Frontend: {website}")
    return bucket, website


def ensure_schedule(events, lam, lf2_arn, account):
    rule_arn = events.put_rule(Name=RULE_NAME, ScheduleExpression="rate(1 minute)", State="ENABLED")["RuleArn"]
    events.put_targets(Rule=RULE_NAME, Targets=[{"Id": "LF2", "Arn": lf2_arn}])
    allow_invoke(lam, "LF2", "eventbridge-invoke", "events.amazonaws.com", rule_arn)
    return rule_arn


def password():
    symbols = "!@#$%^&*"
    required = [
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.digits),
        secrets.choice(symbols),
    ]
    pool = string.ascii_letters + string.digits + symbols
    required.extend(secrets.choice(pool) for _ in range(12))
    secrets.SystemRandom().shuffle(required)
    return "".join(required)


def ensure_opensearch(opensearch, account, lambda_role_arn):
    env_path = Path(__file__).resolve().parent / ".env"
    try:
        status = opensearch.describe_domain(DomainName=DOMAIN_NAME)["DomainStatus"]
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceNotFoundException":
            raise
        status = None
    if status:
        endpoint = status.get("Endpoint")
        if not endpoint:
            raise SystemExit("OpenSearch domain exists but has no endpoint yet. Wait, then rerun.")
        print("OpenSearch domain already exists. Reusing OPENSEARCH_PASSWORD from the environment or .env.")
        return f"https://{endpoint}", os.environ.get("OPENSEARCH_PASSWORD", "")
    master_password = password()
    access = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"AWS": "*"},
                "Action": "es:*",
                "Resource": f"arn:aws:es:{REGION}:{account}:domain/{DOMAIN_NAME}/*",
            }
        ],
    }
    try:
        opensearch.create_domain(
            DomainName=DOMAIN_NAME,
            EngineVersion="OpenSearch_2.17",
            ClusterConfig={
                "InstanceType": "t3.small.search",
                "InstanceCount": 1,
                "DedicatedMasterEnabled": False,
                "ZoneAwarenessEnabled": False,
            },
            EBSOptions={"EBSEnabled": True, "VolumeType": "gp3", "VolumeSize": 10},
            AccessPolicies=json.dumps(access),
            NodeToNodeEncryptionOptions={"Enabled": True},
            EncryptionAtRestOptions={"Enabled": True},
            DomainEndpointOptions={"EnforceHTTPS": True},
            AdvancedSecurityOptions={
                "Enabled": True,
                "InternalUserDatabaseEnabled": True,
                "MasterUserOptions": {"MasterUserName": "diningadmin", "MasterUserPassword": master_password},
            },
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceAlreadyExistsException":
            raise
        print("OpenSearch domain already exists. Reusing it and keeping the previous master password.")
    print("Waiting for OpenSearch. This often takes 15 to 20 minutes.")
    described = wait_until(
        lambda: opensearch.describe_domain(DomainName=DOMAIN_NAME)["DomainStatus"],
        "Processing",
        {False},
        attempts=120,
        delay=20,
    )
    endpoint = described.get("Endpoint") or described.get("Endpoints", {}).get("vpc")
    if not endpoint:
        status = opensearch.describe_domain(DomainName=DOMAIN_NAME)["DomainStatus"]
        endpoint = status.get("Endpoint")
    endpoint = f"https://{endpoint}"
    env_path.write_text(
        f"OPENSEARCH_ENDPOINT={endpoint}\nOPENSEARCH_USER=diningadmin\nOPENSEARCH_PASSWORD={master_password}\n",
        encoding="utf-8",
    )
    print(f"OpenSearch endpoint saved in {env_path}")
    print("Delete this domain with teardown.py as soon as the demo is done.")
    return endpoint, master_password


def update_lf2_search(lam, endpoint, master_password, base_env):
    variables = dict(base_env)
    variables["OPENSEARCH_ENDPOINT"] = endpoint
    variables["OPENSEARCH_USER"] = "diningadmin"
    variables["OPENSEARCH_PASSWORD"] = master_password
    lam.update_function_configuration(FunctionName="LF2", Environment={"Variables": variables})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sender-email", required=True)
    parser.add_argument("--with-opensearch", action="store_true")
    args = parser.parse_args()
    account = account_id()
    iam = client("iam")
    print(f"Deploying to account {account} in {REGION}")
    lambda_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["sqs:SendMessage", "sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"],
                "Resource": f"arn:aws:sqs:{REGION}:{account}:{QUEUE_NAME}",
            },
            {
                "Effect": "Allow",
                "Action": ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:BatchWriteItem", "dynamodb:Scan"],
                "Resource": [
                    f"arn:aws:dynamodb:{REGION}:{account}:table/{RESTAURANTS_TABLE}",
                    f"arn:aws:dynamodb:{REGION}:{account}:table/{STATE_TABLE}",
                ],
            },
            {"Effect": "Allow", "Action": ["lex:RecognizeText"], "Resource": "*"},
            {"Effect": "Allow", "Action": ["ses:SendEmail", "ses:SendRawEmail"], "Resource": "*"},
            {
                "Effect": "Allow",
                "Action": ["es:ESHttpGet", "es:ESHttpPost", "es:ESHttpPut", "es:ESHttpHead"],
                "Resource": f"arn:aws:es:{REGION}:{account}:domain/{DOMAIN_NAME}/*",
            },
        ],
    }
    lambda_role_arn = ensure_role(iam, LAMBDA_ROLE, "lambda.amazonaws.com", lambda_policy)
    lex_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["lambda:InvokeFunction"],
                "Resource": f"arn:aws:lambda:{REGION}:{account}:function:LF1",
            }
        ],
    }
    lex_role_arn = ensure_role(iam, LEX_ROLE, "lexv2.amazonaws.com", lex_policy)
    ensure_tables(client("dynamodb"))
    queue_url = ensure_queue(client("sqs"))
    common = {
        "SQS_QUEUE_URL": queue_url,
        "RESTAURANTS_TABLE": RESTAURANTS_TABLE,
        "STATE_TABLE": STATE_TABLE,
        "SENDER_EMAIL": args.sender_email,
    }
    lam = client("lambda")
    lf1_arn = ensure_lambda(lam, "LF1", ROOT / "lambda-functions" / "LF1", lambda_role_arn, common, 20)
    lf2_arn = ensure_lambda(lam, "LF2", ROOT / "lambda-functions" / "LF2", lambda_role_arn, common, 30)
    lf0_arn = ensure_lambda(
        lam,
        "LF0",
        ROOT / "lambda-functions" / "LF0",
        lambda_role_arn,
        {"LEX_BOT_ID": "pending", "LEX_BOT_ALIAS_ID": "pending", "LEX_LOCALE_ID": "en_US"},
        15,
    )
    lex = client("lexv2-models")
    bot_id, alias_id = ensure_bot(lex, lam, lex_role_arn, lf1_arn, account)
    lam.update_function_configuration(
        FunctionName="LF0",
        Environment={"Variables": {"LEX_BOT_ID": bot_id, "LEX_BOT_ALIAS_ID": alias_id, "LEX_LOCALE_ID": "en_US"}},
    )
    api_id = ensure_api(client("apigateway"), lam, lf0_arn, account)
    try:
        client("ses").verify_email_identity(EmailAddress=args.sender_email)
        print(f"SES verification sent to {args.sender_email}. Confirm it before emails can be delivered.")
    except ClientError as exc:
        print(f"SES verification was not started: {exc.response['Error']['Message']}")
    rule_arn = ensure_schedule(client("events"), lam, lf2_arn, account)
    bucket, website = publish_frontend(client("s3"), api_id, account)
    endpoint = ""
    if args.with_opensearch:
        endpoint, master_password = ensure_opensearch(client("opensearch"), account, lambda_role_arn)
        update_lf2_search(lam, endpoint, master_password, common)
    state = {
        "account": account,
        "region": REGION,
        "queueUrl": queue_url,
        "botId": bot_id,
        "botAliasId": alias_id,
        "apiId": api_id,
        "apiUrl": f"https://{api_id}.execute-api.{REGION}.amazonaws.com/v1/chatbot",
        "bucket": bucket,
        "website": website,
        "ruleArn": rule_arn,
        "opensearchEndpoint": endpoint,
        "senderEmail": args.sender_email,
        "lambdaRole": lambda_role_arn,
        "lexRole": lex_role_arn,
    }
    save_state(state)
    print(json.dumps(state, indent=2))


if __name__ == "__main__":
    main()
