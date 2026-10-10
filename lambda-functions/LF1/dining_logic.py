"""Dialog and fulfillment logic for the Dining Concierge Lex bot.

Lex V2 invokes this module from LF1 before it replies. The hook validates
each slot, remembers the diner's last location and cuisine, and queues a
suggestion request once the conversation is complete.
"""

import re

CUISINES = ["chinese", "italian", "japanese", "mexican", "indian", "thai", "korean"]

CUISINE_SYNONYMS = {
    "chinese": ["chinese", "dim sum", "dumplings", "dumpling", "szechuan", "sichuan", "cantonese"],
    "italian": ["italian", "pizza", "pasta"],
    "japanese": ["japanese", "sushi", "ramen", "izakaya"],
    "mexican": ["mexican", "tacos", "taco", "tex mex", "tex-mex"],
    "indian": ["indian", "biryani", "curry"],
    "thai": ["thai", "pad thai"],
    "korean": ["korean", "kbbq", "korean bbq"],
}

NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "couple": 2,
    "solo": 1,
}

DATE_WORDS = {
    "today": "today",
    "tonight": "today",
    "tomorrow": "tomorrow",
    "monday": "Monday",
    "tuesday": "Tuesday",
    "wednesday": "Wednesday",
    "thursday": "Thursday",
    "friday": "Friday",
    "saturday": "Saturday",
    "sunday": "Sunday",
}

YES_WORDS = {"yes", "yeah", "yep", "y", "sure", "ok", "okay", "please", "same", "yup"}
NO_WORDS = {"no", "nope", "nah", "n", "different", "new", "other"}

SLOT_ORDER = ["Location", "Cuisine", "NumberOfPeople", "DiningDate", "DiningTime", "Email"]

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
TIME_RE = re.compile(
    r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?\b",
    re.IGNORECASE,
)
DATE_RE = re.compile(
    r"\b(\d{4}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}(?:/\d{2,4})?|"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+\d{1,2}(?:,\s*\d{4})?)\b",
    re.IGNORECASE,
)


def cuisine_label(cuisine):
    return (cuisine or "").strip().title()


def get_slot(slots, name):
    slot = (slots or {}).get(name)
    if not isinstance(slot, dict):
        return None
    value = slot.get("value") or {}
    text = value.get("interpretedValue")
    if text is None:
        text = value.get("originalValue")
    if text is None:
        return None
    text = str(text).strip()
    return text or None


def put_slot(slots, name, text):
    slots = dict(slots or {})
    if text is None:
        slots[name] = None
        return slots
    slots[name] = {
        "value": {
            "originalValue": text,
            "interpretedValue": text,
            "resolvedValues": [text],
        }
    }
    return slots


def _clean(text):
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def validate_location(text):
    cleaned = _clean(text).replace(",", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if "manhattan" in cleaned:
        return True, "Manhattan", None
    aliases = {"nyc", "new york", "new york city", "new york ny"}
    if cleaned in aliases:
        return True, "Manhattan", None
    shown = (text or "").strip().rstrip(".")
    return False, None, f"Sorry, I can't fulfill requests for {shown}. Please enter a valid location."


def validate_cuisine(text):
    cleaned = _clean(text)
    best = None
    best_at = None
    for cuisine, synonyms in CUISINE_SYNONYMS.items():
        for synonym in synonyms:
            index = cleaned.find(synonym)
            if index == -1:
                continue
            if best is None or index < best_at or (index == best_at and len(synonym) > len(best[1])):
                best = (cuisine, synonym)
                best_at = index
    if not best:
        shown = (text or "").strip()
        options = ", ".join(cuisine_label(item) for item in CUISINES)
        return False, None, f"Sorry, I don't have suggestions for {shown}. Please choose one of: {options}."
    cuisine = best[0]
    return True, cuisine, None


def validate_people(text):
    cleaned = _clean(text)
    for word, number in NUMBER_WORDS.items():
        if re.search(rf"\b{re.escape(word)}\b", cleaned):
            return True, str(number), None
    match = re.search(r"\b(\d{1,2})\b", cleaned)
    if match:
        number = int(match.group(1))
        if 1 <= number <= 20:
            return True, str(number), None
    return False, None, "Sorry, I need a party size from 1 to 20. How many people are in your party?"


def validate_date(text):
    cleaned = _clean(text)
    for word, normalized in DATE_WORDS.items():
        if re.search(rf"\b{word}\b", cleaned):
            return True, normalized, None
    if "weekend" in cleaned:
        return True, "this weekend", None
    match = DATE_RE.search(text or "")
    if match:
        return True, match.group(1), None
    return False, None, "I didn't catch the date. Please try today, tomorrow, or a date like 10/09."


def validate_time(text):
    cleaned = _clean(text)
    if "noon" in cleaned:
        return True, "12pm", None
    if "midnight" in cleaned:
        return True, "12am", None
    match = TIME_RE.search(cleaned)
    if not match:
        return False, None, "I didn't catch the time. Please try something like 7 pm."
    hour = int(match.group(1))
    minute = match.group(2)
    ampm = match.group(3)
    if minute is not None and not (0 <= int(minute) <= 59):
        return False, None, "I didn't catch the time. Please try something like 7 pm."
    if ampm:
        if not 1 <= hour <= 12:
            return False, None, "I didn't catch the time. Please try something like 7 pm."
        suffix = "am" if ampm.lower().startswith("a") else "pm"
    else:
        if not 0 <= hour <= 23:
            return False, None, "I didn't catch the time. Please try something like 7 pm."
        suffix = ""
    clock = f"{hour}:{minute}" if minute else str(hour)
    return True, f"{clock}{suffix}", None


def validate_email(text):
    candidate = (text or "").strip().strip(".,;")
    if EMAIL_RE.match(candidate):
        return True, candidate, None
    return False, None, "That doesn't look like an email address. Please enter a valid email so I can send you my findings."


def interpret_yes_no(text):
    cleaned = _clean(text).strip(" .!?")
    first = cleaned.split(" ", 1)[0] if cleaned else ""
    if cleaned in YES_WORDS or first in YES_WORDS:
        return True
    if cleaned in NO_WORDS or first in NO_WORDS:
        return False
    return None


VALIDATORS = {
    "Location": validate_location,
    "Cuisine": validate_cuisine,
    "NumberOfPeople": validate_people,
    "DiningDate": validate_date,
    "DiningTime": validate_time,
    "Email": validate_email,
}


def missing_prompt(name, slots):
    if name == "Location":
        return "Great. I can help you with that. What city or city area are you looking to dine in?"
    if name == "Cuisine":
        location = get_slot(slots, "Location") or "Manhattan"
        return f"Got it, {location}. What cuisine would you like to try?"
    if name == "NumberOfPeople":
        return "Ok, how many people are in your party?"
    if name == "DiningDate":
        return "A few more to go. What date?"
    if name == "DiningTime":
        return "What time?"
    if name == "Email":
        return "Great. Lastly, I need your email so I can send you my findings."
    return "Could you tell me a bit more?"


def _intent_name(event):
    intent = ((event.get("sessionState") or {}).get("intent") or {})
    return intent.get("name") or "FallbackIntent"


def _slots(event):
    intent = ((event.get("sessionState") or {}).get("intent") or {})
    return dict(intent.get("slots") or {})


def _session_attributes(event):
    return dict((event.get("sessionState") or {}).get("sessionAttributes") or {})


def _response(intent_name, slots, action, message, state, session_attributes, slot_to_elicit=None):
    dialog_action = {"type": action}
    if slot_to_elicit:
        dialog_action["slotToElicit"] = slot_to_elicit
    body = {
        "sessionState": {
            "dialogAction": dialog_action,
            "intent": {
                "name": intent_name,
                "slots": slots,
                "state": state,
                "confirmationState": "None",
            },
            "sessionAttributes": session_attributes or {},
        }
    }
    if message:
        body["messages"] = [{"contentType": "PlainText", "content": message}]
    else:
        body["messages"] = []
    return body


def _close(intent_name, slots, message, session_attributes, state="Fulfilled"):
    return _response(intent_name, slots, "Close", message, state, session_attributes)


def _elicit(intent_name, slots, slot_name, message, session_attributes):
    return _response(intent_name, slots, "ElicitSlot", message, "InProgress", session_attributes, slot_name)


def _delegate(intent_name, slots, session_attributes):
    return _response(intent_name, slots, "Delegate", None, "ReadyForFulfillment", session_attributes)


def _queue_payload(user_id, slots, use_previous, previous_ids):
    cuisine = get_slot(slots, "Cuisine")
    return {
        "userId": user_id,
        "location": get_slot(slots, "Location"),
        "cuisine": cuisine,
        "cuisineDisplay": cuisine_label(cuisine),
        "numberOfPeople": get_slot(slots, "NumberOfPeople"),
        "diningDate": get_slot(slots, "DiningDate"),
        "diningTime": get_slot(slots, "DiningTime"),
        "email": get_slot(slots, "Email"),
        "usePrevious": bool(use_previous),
        "previousRestaurantIds": list(previous_ids or []),
    }


def _same_as_last(last, slots):
    if not last:
        return False
    previous_ids = last.get("restaurantIds") or last.get("previousRestaurantIds") or []
    if not previous_ids:
        return False
    last_location = (last.get("location") or "").strip().lower()
    last_cuisine = (last.get("cuisine") or "").strip().lower()
    return last_location == (get_slot(slots, "Location") or "").strip().lower() and last_cuisine == (
        get_slot(slots, "Cuisine") or ""
    ).strip().lower()


def handle_lex_event(event, deps):
    """Return a Lex V2 dialog response. `deps` supplies queue and search state."""
    intent_name = _intent_name(event)
    slots = _slots(event)
    session_attributes = _session_attributes(event)
    transcript = event.get("inputTranscript") or ""

    if intent_name == "GreetingIntent":
        return _close(intent_name, slots, "Hi there, how can I help?", session_attributes)
    if intent_name == "ThankYouIntent":
        return _close(intent_name, slots, "You're welcome.", session_attributes)
    if intent_name != "DiningSuggestionsIntent":
        return _close(
            intent_name,
            slots,
            "I can help you find restaurants in Manhattan. Say hello, or tell me you need restaurant suggestions.",
            session_attributes,
            state="Failed",
        )

    for name in SLOT_ORDER:
        raw = get_slot(slots, name)
        if not raw:
            return _elicit(intent_name, slots, name, missing_prompt(name, slots), session_attributes)
        ok, normalized, error = VALIDATORS[name](raw)
        if not ok:
            slots = put_slot(slots, name, None)
            return _elicit(intent_name, slots, name, error, session_attributes)
        slots = put_slot(slots, name, normalized)

    user_id = event.get("sessionId") or session_attributes.get("userId") or "dining-user"
    session_attributes["userId"] = user_id
    use_previous = False
    previous_ids = []
    try:
        last = deps.get_last_search(user_id) if deps else None
    except Exception as exc:
        print(f"last-search lookup failed: {exc}")
        last = None

    if _same_as_last(last, slots):
        answer = get_slot(slots, "SameRecommendation")
        decision = interpret_yes_no(answer) if answer else None
        if decision is None and session_attributes.get("awaitingSame") == "true":
            decision = interpret_yes_no(transcript)
        if decision is None:
            session_attributes["awaitingSame"] = "true"
            location = get_slot(slots, "Location")
            cuisine = cuisine_label(get_slot(slots, "Cuisine"))
            return _elicit(
                intent_name,
                slots,
                "SameRecommendation",
                f"I see you last searched for {cuisine} in {location}. Would you like the same recommendations as last time?",
                session_attributes,
            )
        use_previous = decision
        previous_ids = list(last.get("restaurantIds") or [])
        slots = put_slot(slots, "SameRecommendation", "yes" if decision else "no")
        session_attributes["awaitingSame"] = "false"

    invocation = event.get("invocationSource") or "DialogCodeHook"
    if invocation == "DialogCodeHook":
        return _delegate(intent_name, slots, session_attributes)

    if session_attributes.get("requestQueued") != "true":
        deps.enqueue(_queue_payload(user_id, slots, use_previous, previous_ids if use_previous else []))
        session_attributes["requestQueued"] = "true"
    return _close(
        intent_name,
        slots,
        "You're all set. Expect my suggestions shortly! Have a good day.",
        session_attributes,
    )
