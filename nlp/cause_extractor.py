"""Cause, accident-type, vehicle, and weather extraction rules."""

from __future__ import annotations

import re

from .preprocess import clean_text


CAUSES = (
    (r"\b(?:overspeed(?:ing)?|speeding|high speed)\b|तेज\s+रफ्तार|तेज\s+गति", "speeding"),
    (r"\b(?:drunk driving|drink[- ]driving|intoxicated|under\s+the\s+influence\s+of\s+alcohol|alcohol[- ]influenced)\b|नशे\s+में", "impaired driving"),
    (r"\b(?:brake failure|brakes failed)\b|ब्रेक\s+फेल", "brake failure"),
    (r"\b(?:wrong[- ]side|wrong[- ]way)\b|गलत\s+दिशा", "wrong-side driving"),
    (r"\b(?:rash|reckless|negligent) driving\b|लापरवाही", "unsafe driving"),
    (r"\b(?:pothole|poor road condition|road damage)\b|गड्ढे|खराब\s+सड़क", "road condition"),
    (r"\b(?:fog|poor visibility|slippery road|wet road|heavy rain)\b|कोहरा|फिसलन|बारिश", "weather/visibility"),
)
VEHICLES = (
    (r"\b(?:motorcycle|motorbike|bike)\b|मोटरसाइकिल|बाइक", "motorcycle"),
    (r"\b(?:scooter|scooty)\b|स्कूटर", "scooter"),
    (r"\b(?:car|suv)\b|कार", "car"),
    (r"\b(?:bus)\b|बस", "bus"),
    (r"\b(?:truck|lorry)\b|ट्रक", "truck"),
    (r"\b(?:auto[- ]?rickshaw|three[- ]wheeler)\b|ऑटो", "auto-rickshaw"),
    (r"\b(?:tempo|van)\b|टेंपो|वैन", "van/tempo"),
    (r"\b(?:tractor)\b|ट्रैक्टर", "tractor"),
    (r"\b(?:pedestrian)\b|पैदल\s+चलने", "pedestrian"),
)
WEATHER = (
    (r"\b(?:fog|mist|poor visibility)\b|कोहरा|धुंध", "fog/poor visibility"),
    (r"\b(?:rain|rainy|wet road|slippery)\b|बारिश|वर्षा|फिसलन", "rain/wet road"),
    (r"\b(?:snow|snowfall)\b|बर्फबारी|हिमपात", "snow"),
)
NEGATION = re.compile(r"\b(?:not|no|never|without|denied|ruled\s+out)\b|नहीं|नही", re.I)


def _positive_match(pattern: str, text: str) -> bool:
    for match in re.finditer(pattern, text, re.IGNORECASE):
        before = text[max(0, match.start() - 35):match.start()]
        after = text[match.end():min(len(text), match.end() + 30)]
        negated_after = re.search(
            r"^\s*(?:(?:was|were|is|are|has\s+been|have\s+been)\s+)?"
            r"(?:not|never|ruled\s+out|disputed|unconfirmed)\b",
            after,
            re.IGNORECASE,
        )
        if not NEGATION.search(before) and not negated_after:
            return True
    return False


def extract_cause(text: str) -> tuple[str | None, str]:
    text = clean_text(text)
    found = [label for pattern, label in CAUSES if _positive_match(pattern, text)]
    if len(found) == 1:
        # News language is not proof. Causes remain explicitly suspected.
        return found[0], "suspected"
    if len(found) > 1:
        return None, "conflict"
    return None, "unknown"


def extract_accident_type(text: str) -> str | None:
    text = clean_text(text)
    vehicle_fall = re.search(
        r"\b(?P<verb>fell|plunged|tumbled|slid|rolled|dropped)\b"
        r".{0,90}\b(?P<terrain>gorge|ravine|cliff|valley|river|canal|ditch|slope|embankment)\b",
        text,
        re.I,
    )
    if vehicle_fall:
        vehicles = extract_vehicles(text)
        vehicle = vehicles[0] if vehicles else "vehicle"
        verb = vehicle_fall.group("verb").casefold()
        return f"{vehicle} {verb} into a {vehicle_fall.group('terrain').casefold()}"
    if re.search(
        r"\b(?:ram(?:med|s|ming)?|hit|struck)\b.{0,90}\b(?:car|cars|vehicle|vehicles|bus|truck)\b|"
        r"\b(?:car|cars|vehicle|vehicles|bus|truck)\b.{0,90}\b(?:ram(?:med|s|ming)?|hit|struck)\b",
        text,
        re.I,
    ):
        return "collision"
    if re.search(r"\b(?:overturn(?:ed|ing|s)?|rollover|roll(?:ed|ing)?\s+over)\b", text, re.I):
        return "rollover"
    if re.search(r"\b(?:collision|collid(?:ed|ing|es)|crashed into|head[- ]on)\b", text, re.I):
        return "collision"
    if re.search(r"\bhit[- ]and[- ]run\b|टक्कर\s+मारकर", text, re.I):
        return "hit-and-run"
    if re.search(r"\b(?:overturn(?:ed)?|rollover|overturned)\b|पलट(?:ा|ी|े)?", text, re.I):
        return "rollover"
    if re.search(r"\b(?:collision|collided|crashed into|head[- ]on)\b|टक्कर|भिड़ंत|टकरा\w*", text, re.I):
        return "collision"
    if re.search(r"\b(?:pedestrian|person)\s+(?:was\s+)?hit\b|पैदल\s+चलने", text, re.I):
        return "pedestrian collision"
    if re.search(r"\b(?:road\s+)?accident|\bcrash\b|दुर्घटना|हादसा|हादसे", text, re.I):
        return "road accident"
    return None


def extract_vehicles(text: str) -> list[str]:
    text = clean_text(text)
    return list(dict.fromkeys(label for pattern, label in VEHICLES if re.search(pattern, text, re.I)))


def extract_weather(text: str) -> str | None:
    text = clean_text(text)
    found = [label for pattern, label in WEATHER if _positive_match(pattern, text)]
    return "; ".join(found) if found else None
