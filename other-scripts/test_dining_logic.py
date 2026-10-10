"""Conversation and suggestion tests. Run: python other-scripts/test_dining_logic.py"""

import json
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lambda-functions", "LF1"))
sys.path.insert(0, os.path.join(ROOT, "lambda-functions", "LF2"))
sys.path.insert(0, os.path.join(ROOT, "lambda-functions", "LF0"))

from dining_logic import get_slot, handle_lex_event, put_slot
from suggestions import choose_restaurant_ids, format_suggestions_email
import lambda_function as lf0


class MemoryDeps:
    def __init__(self, last=None):
        self.last = last
        self.queue = []

    def get_last_search(self, user_id):
        return self.last

    def enqueue(self, message):
        self.queue.append(message)


def event(intent, slots, text, source="DialogCodeHook", session=None, session_id="user-1"):
    return {
        "sessionId": session_id,
        "inputTranscript": text,
        "invocationSource": source,
        "sessionState": {
            "intent": {"name": intent, "slots": slots, "state": "InProgress", "confirmationState": "None"},
            "sessionAttributes": session or {},
        },
    }


def say(deps, slots, session, text, slot_name=None, slot_value=None):
    if slot_name:
        slots = put_slot(slots, slot_name, slot_value if slot_value is not None else text)
    response = handle_lex_event(
        event("DiningSuggestionsIntent", slots, text, "DialogCodeHook", session),
        deps,
    )
    slots = response["sessionState"]["intent"]["slots"] or {}
    session = response["sessionState"].get("sessionAttributes") or {}
    messages = [item["content"] for item in response.get("messages") or []]
    if response["sessionState"]["dialogAction"]["type"] == "Delegate":
        fulfilled = handle_lex_event(
            event("DiningSuggestionsIntent", slots, text, "FulfillmentCodeHook", session),
            deps,
        )
        messages = [item["content"] for item in fulfilled.get("messages") or []]
        slots = fulfilled["sessionState"]["intent"]["slots"] or {}
        session = fulfilled["sessionState"].get("sessionAttributes") or {}
    return messages, slots, session


def test_sample_conversation():
    deps = MemoryDeps()
    slots, session = {}, {}
    messages, slots, session = say(deps, slots, session, "I need some restaurant suggestions.")
    assert messages == ["Great. I can help you with that. What city or city area are you looking to dine in?"]
    messages, slots, session = say(deps, slots, session, "New Delhi", "Location", "New Delhi")
    assert messages == ["Sorry, I can't fulfill requests for New Delhi. Please enter a valid location."]
    assert get_slot(slots, "Location") is None
    messages, slots, session = say(deps, slots, session, "Can you try Manhattan", "Location", "Can you try Manhattan")
    assert messages == ["Got it, Manhattan. What cuisine would you like to try?"]
    messages, slots, session = say(deps, slots, session, "Japanese", "Cuisine")
    assert messages == ["Ok, how many people are in your party?"]
    messages, slots, session = say(deps, slots, session, "Two", "NumberOfPeople")
    assert messages == ["A few more to go. What date?"]
    messages, slots, session = say(deps, slots, session, "Today", "DiningDate")
    assert messages == ["What time?"]
    messages, slots, session = say(deps, slots, session, "7 pm, please", "DiningTime", "7 pm, please")
    assert messages == ["Great. Lastly, I need your email so I can send you my findings."]
    messages, slots, session = say(deps, slots, session, "diner@nyu.edu", "Email")
    assert messages == ["You're all set. Expect my suggestions shortly! Have a good day."]
    assert len(deps.queue) == 1
    queued = deps.queue[0]
    assert queued["location"] == "Manhattan"
    assert queued["cuisine"] == "japanese"
    assert queued["numberOfPeople"] == "2"
    assert queued["diningDate"] == "today"
    assert queued["diningTime"] == "7pm"
    assert queued["email"] == "diner@nyu.edu"
    assert queued["usePrevious"] is False


def test_greeting_and_thanks():
    deps = MemoryDeps()
    greeting = handle_lex_event(event("GreetingIntent", {}, "Hello"), deps)
    thanks = handle_lex_event(event("ThankYouIntent", {}, "Thank you"), deps)
    assert greeting["messages"][0]["content"] == "Hi there, how can I help?"
    assert thanks["messages"][0]["content"] == "You're welcome."
    assert deps.queue == []


def test_invalid_email_and_cuisine():
    deps = MemoryDeps()
    slots = {}
    for name, value in (
        ("Location", "Manhattan"),
        ("Cuisine", "pizza"),
    ):
        slots = put_slot(slots, name, value)
    messages, slots, session = say(deps, slots, {}, "pizza", "Cuisine", "pizza")
    assert get_slot(slots, "Cuisine") == "italian"
    assert "how many people" in messages[0]
    slots = put_slot(slots, "NumberOfPeople", "2")
    slots = put_slot(slots, "DiningDate", "today")
    slots = put_slot(slots, "DiningTime", "7pm")
    messages, slots, session = say(deps, slots, session, "not-an-email", "Email", "not-an-email")
    assert "email" in messages[0].lower()
    assert deps.queue == []


def test_repeat_recommendation():
    last = {"location": "Manhattan", "cuisine": "japanese", "restaurantIds": ["a", "b", "c"]}
    deps = MemoryDeps(last)
    slots = {}
    for name, value in (
        ("Location", "Manhattan"),
        ("Cuisine", "Japanese"),
        ("NumberOfPeople", "2"),
        ("DiningDate", "today"),
        ("DiningTime", "7 pm"),
        ("Email", "diner@nyu.edu"),
    ):
        slots = put_slot(slots, name, value)
    messages, slots, session = say(deps, slots, {}, "diner@nyu.edu")
    assert "same recommendations" in messages[0]
    messages, slots, session = say(deps, slots, session, "yes", "SameRecommendation", "yes")
    assert messages[0].startswith("You're all set")
    assert deps.queue[0]["usePrevious"] is True
    assert deps.queue[0]["previousRestaurantIds"] == ["a", "b", "c"]

    deps = MemoryDeps(last)
    slots = {}
    for name, value in (
        ("Location", "Manhattan"),
        ("Cuisine", "Japanese"),
        ("NumberOfPeople", "2"),
        ("DiningDate", "today"),
        ("DiningTime", "7 pm"),
        ("Email", "diner@nyu.edu"),
    ):
        slots = put_slot(slots, name, value)
    messages, slots, session = say(deps, slots, {}, "diner@nyu.edu")
    messages, slots, session = say(deps, slots, session, "no", "SameRecommendation", "no")
    assert deps.queue[-1]["usePrevious"] is False


def test_email_format_and_random_choice():
    request = {
        "cuisine": "japanese",
        "cuisineDisplay": "Japanese",
        "numberOfPeople": "2",
        "diningDate": "today",
        "diningTime": "7pm",
    }
    restaurants = [
        {"Name": "Sushi Nakazawa", "Street": "23 Commerce St"},
        {"Name": "Jin Ramen", "Address": "3183 Broadway, New York, NY"},
        {"Name": "Nikko", "Street": "1280 Amsterdam Ave"},
    ]
    body = format_suggestions_email(request, restaurants)
    assert body == (
        "Hello! Here are my Japanese restaurant suggestions for 2 people, for today at 7pm:\n"
        "\n"
        "1. Sushi Nakazawa, located at 23 Commerce St\n"
        "2. Jin Ramen, located at 3183 Broadway\n"
        "3. Nikko, located at 1280 Amsterdam Ave\n"
        "Enjoy your meal!"
    )
    rng = random.Random(1)
    picked = choose_restaurant_ids(["j1", "j2", "c1", "j3", "j4"], 3, rng)
    assert len(picked) == 3
    assert len(set(picked)) == 3


def test_lf0_shapes_lex_reply():
    os.environ["LEX_BOT_ID"] = "bot"
    os.environ["LEX_BOT_ALIAS_ID"] = "alias"

    class FakeLex:
        def recognize_text(self, **kwargs):
            assert kwargs["text"] == "Hello"
            assert kwargs["sessionId"] == "user-1"
            return {"messages": [{"content": "Hi there, how can I help?"}]}

    event_body = {
        "httpMethod": "POST",
        "body": json.dumps(
            {"messages": [{"type": "unstructured", "unstructured": {"id": "user-1", "text": "Hello"}}]}
        ),
    }
    response = lf0.lambda_handler(event_body, None, lex_client=FakeLex())
    assert response["statusCode"] == 200
    assert response["headers"]["Access-Control-Allow-Origin"] == "*"
    payload = json.loads(response["body"])
    assert payload["messages"][0]["unstructured"]["text"] == "Hi there, how can I help?"


def main():
    test_sample_conversation()
    test_greeting_and_thanks()
    test_invalid_email_and_cuisine()
    test_repeat_recommendation()
    test_email_format_and_random_choice()
    test_lf0_shapes_lex_reply()
    print("All dining concierge tests passed.")


if __name__ == "__main__":
    main()
