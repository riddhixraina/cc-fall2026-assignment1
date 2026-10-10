# Dining Concierge — Cloud Computing Fall 2026, Assignment 1

Serverless restaurant chatbot. A static site on S3 talks to API Gateway, which calls Lex through Lambda. When the diner finishes, the bot queues the request. A worker runs every minute, picks restaurants from OpenSearch and DynamoDB, and emails them with SES.

Individual submission. NetID: rrp4822.

## Layout

```
frontend/           S3 website, adapted from the course starter
lambda-functions/   LF0 chat API, LF1 Lex hook, LF2 suggestion worker
other-scripts/      Swagger, Yelp scraper, OpenSearch loader, deploy, teardown
```

## What the bot collects

Location (Manhattan), cuisine, party size, date, time, and email. Cuisines: Chinese, Italian, Japanese, Mexican, Indian, Thai, and Korean.

Invalid cities are rejected. After a successful search, a later request for the same location and cuisine asks whether to reuse the last recommendations. That state is stored in the DynamoDB table `user-last-search`.

## Try the conversation locally

This uses a small sample catalog so the chat and the email text can be checked without AWS or a Yelp key. It is not the 1,000-restaurant dataset.

```bash
python3 other-scripts/local_server.py
```

Open http://127.0.0.1:8080/chat.html. The formatted email is printed in the terminal.

```bash
python3 other-scripts/test_dining_logic.py
```

## Deploy

The script expects credentials that can manage IAM, Lambda, API Gateway, Lex, DynamoDB, SQS, SES, S3, EventBridge, and OpenSearch. Use the course lab role, not a restricted IAM user.

```bash
pip install -r requirements.txt
python3 other-scripts/deploy.py --sender-email YOU@nyu.edu
```

Then scrape and, only while you are ready to demo, create the search domain:

```bash
export YELP_API_KEY=your-fusion-key
python3 other-scripts/scrape_yelp.py --output other-scripts/data/restaurants.json
python3 other-scripts/deploy.py --sender-email YOU@nyu.edu --with-opensearch
set -a && source other-scripts/.env && set +a
python3 other-scripts/load_opensearch.py --source file --input other-scripts/data/restaurants.json
```

`deploy.py` prints the S3 website URL and the API URL. Confirm the SES verification email before suggestions can be delivered. In the SES sandbox, the diner's address must be verified too.

OpenSearch is a `t3.small.search` node in one availability zone, without standby, with a master user. It is not serverless. Delete it as soon as the demo is recorded:

```bash
python3 other-scripts/teardown.py
```

Confirm in the console that domain `dining-restaurants` is deleting. Also confirm the Lambdas, API, Lex bot, SQS queue `Q1`, both DynamoDB tables, the EventBridge rule, and the S3 bucket are gone.

## Assignment notes

- Lex is implemented with Lex V2. The writeup links the older Lex V1 API, which is no longer the API to build new bots against.
- OpenSearch 2.x has no Elasticsearch mapping types. Documents in the `restaurants` index carry `RestaurantID`, `Cuisine`, and `type: Restaurant`, which is the Restaurant record the assignment describes.
- Suggestions are filtered by cuisine only.
- The Yelp script requests 200 restaurants for each of 7 cuisines in Manhattan and drops duplicate business ids. Each DynamoDB item includes BusinessID, Name, Address, Coordinates, NumberOfReviews, Rating, ZipCode, Cuisine, and insertedAtTimestamp.

## Demo video

Record this yourself. The video has to start on your NYU Albert profile with your username visible.

1. Albert profile.
2. The S3 chat: hello, a restaurant request, an invalid city, then Manhattan, a cuisine, party size, date, time, and email.
3. The SES email that arrives.
4. The resources: S3 bucket, API Gateway, LF0, Lex bot and its three intents, LF1, queue Q1, LF2, the one-minute EventBridge rule, `yelp-restaurants`, `user-last-search`, the OpenSearch domain and `restaurants` index, and the verified SES identity.
5. Say that resources will be deleted after the demo, then run teardown if you are done.

## Current AWS credentials

The credentials in this environment (`riddhi-user1` in account `251246747541`) can call STS, but they are denied for Lambda, API Gateway, Lex, DynamoDB, SES, EventBridge, OpenSearch, and IAM. Deploy from a principal that has the course permissions. Do not commit `.env` or `.deploy-state.json`.
