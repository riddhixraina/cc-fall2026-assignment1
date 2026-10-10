"""Collect Manhattan restaurants from the Yelp Fusion API and store them in DynamoDB.

The search follows the assignment: each cuisine is queried with a term such as
"Japanese restaurants" in Manhattan. Business ids are deduplicated. Every item
includes BusinessID, Name, Address, Coordinates, NumberOfReviews, Rating,
ZipCode, and insertedAtTimestamp.

Usage:
  export YELP_API_KEY=...
  python other-scripts/scrape_yelp.py
  python other-scripts/scrape_yelp.py --output other-scripts/data/restaurants.json
"""

import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal

import boto3

CUISINES = ["chinese", "italian", "japanese", "mexican", "indian", "thai", "korean"]
SEARCH_URL = "https://api.yelp.com/v3/businesses/search"
TARGET_PER_CUISINE = 200
PAGE_SIZE = 50


def yelp_search(api_key, cuisine, offset):
    params = urllib.parse.urlencode(
        {
            "term": f"{cuisine} restaurants",
            "location": "Manhattan, NY",
            "limit": PAGE_SIZE,
            "offset": offset,
        }
    )
    request = urllib.request.Request(
        f"{SEARCH_URL}?{params}",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def to_item(business, cuisine):
    location = business.get("location") or {}
    coordinates = business.get("coordinates") or {}
    address_lines = location.get("display_address") or []
    inserted = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    item = {
        "BusinessID": business["id"],
        "Name": business.get("name") or "",
        "Address": ", ".join(address_lines),
        "Street": location.get("address1") or (address_lines[0] if address_lines else ""),
        "Coordinates": {
            "latitude": Decimal(str(coordinates.get("latitude") or 0)),
            "longitude": Decimal(str(coordinates.get("longitude") or 0)),
        },
        "NumberOfReviews": int(business.get("review_count") or 0),
        "Rating": Decimal(str(business.get("rating") or 0)),
        "ZipCode": location.get("zip_code") or "",
        "Cuisine": cuisine,
        "insertedAtTimestamp": inserted,
    }
    return item


def scrape(api_key):
    seen = set()
    items = []
    for cuisine in CUISINES:
        collected = 0
        offset = 0
        while collected < TARGET_PER_CUISINE and offset <= 190:
            try:
                payload = yelp_search(api_key, cuisine, offset)
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "ignore")
                raise SystemExit(f"Yelp search failed for {cuisine} offset {offset}: {exc.code} {detail}") from exc
            businesses = payload.get("businesses") or []
            if not businesses:
                break
            for business in businesses:
                business_id = business.get("id")
                if not business_id or business_id in seen:
                    continue
                seen.add(business_id)
                items.append(to_item(business, cuisine))
                collected += 1
                if collected >= TARGET_PER_CUISINE:
                    break
            offset += PAGE_SIZE
            if len(businesses) < PAGE_SIZE:
                break
            time.sleep(0.25)
        print(f"{cuisine}: {collected} new restaurants")
    return items


def json_ready(item):
    coordinates = item["Coordinates"]
    return {
        **item,
        "Coordinates": {
            "latitude": float(coordinates["latitude"]),
            "longitude": float(coordinates["longitude"]),
        },
        "Rating": float(item["Rating"]),
    }


def store_dynamodb(items, table_name):
    table = boto3.resource("dynamodb").Table(table_name)
    with table.batch_writer() as batch:
        for item in items:
            batch.put_item(Item=item)
    print(f"Wrote {len(items)} restaurants to DynamoDB table {table_name}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--table", default="yelp-restaurants")
    parser.add_argument("--output", help="Also write a JSON copy for the OpenSearch loader")
    parser.add_argument("--skip-dynamodb", action="store_true")
    args = parser.parse_args()
    api_key = os.environ.get("YELP_API_KEY")
    if not api_key:
        raise SystemExit("Set YELP_API_KEY to a Yelp Fusion API key before scraping.")
    items = scrape(api_key)
    if len(items) < 1000:
        print(f"Warning: collected {len(items)} unique restaurants, which is under 1000.")
    else:
        print(f"Collected {len(items)} unique restaurants.")
    if args.output:
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as handle:
            json.dump([json_ready(item) for item in items], handle)
        print(f"Wrote {args.output}")
    if not args.skip_dynamodb:
        store_dynamodb(items, args.table)


if __name__ == "__main__":
    main()
