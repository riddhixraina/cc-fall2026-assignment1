"""Pick restaurant suggestions and format the SES email body."""

import random


def choose_restaurant_ids(candidates, count=3, rng=None):
    """Return up to `count` restaurant ids. Selection is random and cuisine is pre-filtered."""
    unique = []
    seen = set()
    for candidate in candidates or []:
        if candidate and candidate not in seen:
            seen.add(candidate)
            unique.append(candidate)
    if not unique:
        return []
    picker = rng or random
    size = min(count, len(unique))
    return picker.sample(unique, size)


def short_address(restaurant):
    street = (restaurant or {}).get("Street") or ""
    if street:
        return street
    address = (restaurant or {}).get("Address") or "address unavailable"
    return address.split(",")[0].strip() or address


def format_suggestions_email(request, restaurants):
    cuisine = request.get("cuisineDisplay") or str(request.get("cuisine") or "restaurant").title()
    people = str(request.get("numberOfPeople") or "")
    party = "person" if people == "1" else "people"
    date = request.get("diningDate") or "the requested date"
    time = request.get("diningTime") or "the requested time"
    lines = [
        f"Hello! Here are my {cuisine} restaurant suggestions for {people} {party}, for {date} at {time}:",
        "",
    ]
    if not restaurants:
        lines.append("I couldn't find any matching restaurants just yet. Please try another cuisine.")
    else:
        for index, restaurant in enumerate(restaurants, start=1):
            name = restaurant.get("Name") or "Unknown restaurant"
            lines.append(f"{index}. {name}, located at {short_address(restaurant)}")
    lines.append("Enjoy your meal!")
    return "\n".join(lines)


def email_subject(request):
    cuisine = request.get("cuisineDisplay") or str(request.get("cuisine") or "restaurant").title()
    return f"Your {cuisine} restaurant suggestions"
