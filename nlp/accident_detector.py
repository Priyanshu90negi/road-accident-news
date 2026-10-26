"""Conservative road-accident relevance rules for English and Hindi."""

from __future__ import annotations

import re

from .preprocess import clean_text, split_sentences


ACCIDENT_PATTERN = re.compile(
    r"\b(?:road\s+)?(?:accident|crash(?:ed|es|ing)?|collision|collid(?:ed|ing|es)?|"
    r"rollover|overturn(?:ed)?|hit[- ]and[- ]run|vehicle\s+mishap)\b|"
    r"दुर्घटना|सड़क\s+हादसा|सड़क\s+दुर्घटना|हादसा|हादसे|टक्कर|पलट(?:ा|ी|े)?",
    re.IGNORECASE,
)
VEHICLE_PATTERN = re.compile(
    r"\b(?:car|cars|vehicle|vehicles|motorcycle|motorbike|bike|scooter|scooty|"
    r"bus|truck|lorry|van|tempo|jeep|suv|auto[- ]?rickshaw)\b",
    re.IGNORECASE,
)
VEHICLE_FALL_PATTERN = re.compile(
    r"\b(?:fell|plunged|tumbled|slid|rolled|dropped)\b"
    r".{0,90}\b(?:gorge|ravine|cliff|valley|river|canal|ditch|slope|embankment)\b",
    re.IGNORECASE,
)
VEHICLE_OVERTURN_PATTERN = re.compile(r"\b(?:overturn(?:ed|ing|s)?|roll(?:ed|ing)?\s+over)\b", re.I)
VEHICLE_IMPACT_PATTERN = re.compile(
    r"\b(?:ram(?:med|s|ming)?|hit|struck)\b.{0,90}\b"
    r"(?:car|cars|vehicle|vehicles|motorcycle|motorbike|bike|scooter|bus|truck|lorry|van|jeep|suv)\b|"
    r"\b(?:car|cars|vehicle|vehicles|motorcycle|motorbike|bike|scooter|bus|truck|lorry|van|jeep|suv)\b"
    r".{0,90}\b(?:ram(?:med|s|ming)?|hit|struck)\b",
    re.I,
)


NEGATED_PATTERN = re.compile(
    r"\bno\s+(?:road\s+)?(?:accident|crash|collision)\b|"
    r"\bwithout\s+(?:any\s+)?(?:road\s+)?(?:accident|crash)\b|"
    r"(?:कोई|किसी)\s+(?:सड़क\s+)?(?:दुर्घटना|हादसा)\s+नहीं",
    re.IGNORECASE,
)


def is_accident_sentence(sentence: str) -> bool:
    sentence = clean_text(sentence)
    return bool(
        sentence
        and not NEGATED_PATTERN.search(sentence)
        and (
            ACCIDENT_PATTERN.search(sentence)
            or (VEHICLE_PATTERN.search(sentence) and VEHICLE_FALL_PATTERN.search(sentence))
            or (VEHICLE_PATTERN.search(sentence) and VEHICLE_OVERTURN_PATTERN.search(sentence))
            or VEHICLE_IMPACT_PATTERN.search(sentence)
        )
    )


def is_accident_relevant(text: str) -> bool:
    return any(is_accident_sentence(sentence) for sentence in split_sentences(clean_text(text)))
