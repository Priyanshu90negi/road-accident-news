from __future__ import annotations

import hashlib
import re
from datetime import date, datetime
from typing import Iterable

from .discovery import parse_datetime


YEAR = 2025
ACCIDENT_TERMS = (
    "accident", "crash", "collision", "overturn", "overturned", "road mishap",
    "road accident", "road crash", "दुर्घटना", "हादसा", "हादसे", "टक्कर", "भिड़ंत", "पलट",
)
DEHRADUN_TERMS = ("dehradun", "देहरादून")
LOCATION_NAMES = (
    "Vikasnagar", "Vikas Nagar", "विकासनगर", "Mussoorie", "मसूरी", "Doiwala", "डोईवाला",
    "Chakrata", "चकराता", "Sahaspur", "सहसपुर", "Raipur", "रायपुर", "Premnagar",
    "प्रेमनगर", "ISBT", "Clement Town", "क्लेमेंट टाउन",
)
STRUCTURED_FIELDS = (
    "accident_id", "headline", "article_url", "publication_date", "accident_date",
    "accident_date_status", "area", "area_status", "cause", "cause_status",
    "injured", "injured_status", "deaths", "deaths_status", "source",
    "extraction_status", "review_flag",
)


def is_relevant(article: dict[str, str]) -> bool:
    text = f'{article["headline"]} {article.get("rss_text", "")} {article.get("article_text", "")}'.casefold()
    has_location = any(term.casefold() in text for term in DEHRADUN_TERMS + LOCATION_NAMES)
    has_accident = any(term.casefold() in text for term in ACCIDENT_TERMS)
    publication_date = parse_datetime(article.get("publication_date", ""))
    return bool(publication_date and publication_date.year == YEAR and has_location and has_accident)


def extract_area(text: str) -> tuple[str, str]:
    for name in LOCATION_NAMES:
        if re.search(re.escape(name), text, flags=re.IGNORECASE):
            return name, "confirmed"
    if any(re.search(re.escape(name), text, flags=re.IGNORECASE) for name in DEHRADUN_TERMS):
        return "Dehradun (area unspecified)", "unknown"
    return "unknown", "unknown"


def extract_accident_date(text: str) -> tuple[str, str]:
    patterns = (
        r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b",
        r"\b(\d{1,2})[-/](\d{1,2})[-/](20\d{2})\b",
        r"\b(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(20\d{2})\b",
        r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+(20\d{2})\b",
        r"(?<![\w\u0900-\u097f])(\d{1,2})\s+(जनवरी|फ़रवरी|फरवरी|मार्च|अप्रैल|मई|जून|जुलाई|अगस्त|सितंबर|अक्टूबर|नवंबर|दिसंबर)\s+(20\d{2})(?!\d)",
    )
    hindi_months = {
        "जनवरी": 1, "फ़रवरी": 2, "फरवरी": 2, "मार्च": 3, "अप्रैल": 4,
        "मई": 5, "जून": 6, "जुलाई": 7, "अगस्त": 8, "सितंबर": 9,
        "अक्टूबर": 10, "नवंबर": 11, "दिसंबर": 12,
    }
    for index, pattern in enumerate(patterns):
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        try:
            if index == 0:
                parsed = date(int(match[1]), int(match[2]), int(match[3]))
            elif index == 1:
                parsed = date(int(match[3]), int(match[2]), int(match[1]))
            elif index == 2:
                parsed = datetime.strptime(match[0], "%d %B %Y").date()
            elif index == 3:
                parsed = datetime.strptime(f"{match[2]} {match[1]} {match[3]}", "%d %B %Y").date()
            else:
                parsed = date(int(match[3]), hindi_months[match[2]], int(match[1]))
            return parsed.isoformat(), "confirmed"
        except ValueError:
            continue
    return "unknown", "unknown"


def extract_count(text: str, terms: str) -> tuple[str, str]:
    number_pattern = re.compile(
        r"(?<![\w\u0900-\u097f])(\d+|one|two|three|four|five|six|seven|eight|nine|ten|"
        r"एक|दो|तीन|चार|पाँच|पांच|छह|छः|सात|आठ|नौ|दस)(?![\w\u0900-\u097f])",
        flags=re.IGNORECASE,
    )
    values = []
    for term in re.finditer(rf"\b(?:{terms})\b", text, flags=re.IGNORECASE):
        preceding = [
            match for match in number_pattern.finditer(text, max(0, term.start() - 45), term.start())
            if not re.fullmatch(r"(?:19|20)\d{2}", match[1])
        ]
        if preceding:
            values.append(preceding[-1][1])
    if not values:
        return "unknown", "unknown"
    value = values[-1]
    words = {
        "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
        "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
        "एक": "1", "दो": "2", "तीन": "3", "चार": "4", "पाँच": "5",
        "पांच": "5", "छह": "6", "छः": "6", "सात": "7", "आठ": "8",
        "नौ": "9", "दस": "10",
    }
    return words.get(value.lower(), value.translate(str.maketrans("०१२३४५६७८९", "0123456789"))), "confirmed"


def extract_cause(text: str) -> tuple[str, str]:
    causes = (
        (r"\b(?:overspeed(?:ing)?|high speed|speeding)\b|तेज\s+रफ्तार", "speeding"),
        (r"\b(?:drunk|drink[- ]driving|intoxicated)\b|नशे", "impaired driving"),
        (r"\b(?:brake failure|brakes failed)\b|ब्रेक\s+फेल", "brake failure"),
        (r"\b(?:wrong side|wrong-way)\b|गलत\s+दिशा", "wrong-side driving"),
        (r"\b(?:rash|negligent|reckless) driving\b|लापरवाही", "unsafe driving"),
        (r"\b(?:slippery|wet|foggy|poor visibility)\b|फिसलन|कोहरा", "road/weather conditions"),
    )
    for pattern, cause in causes:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return cause, "suspected"
    return "unknown", "unknown"


def _accident_id(article: dict[str, str], accident_date: str, area: str) -> str:
    seed = "|".join(
        (
            accident_date if accident_date != "unknown" else article["publication_date"],
            area.casefold(),
            re.sub(r"\W+", " ", article["headline"].casefold()).strip(),
        )
    )
    return "dda-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def preprocess(articles: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    records = []
    seen_urls: set[str] = set()
    seen_headlines: set[str] = set()
    for article in articles:
        url_key = article["article_url"].split("#", 1)[0].rstrip("/").casefold()
        title_key = re.sub(r"\W+", " ", article["headline"].casefold()).strip()
        if url_key in seen_urls or title_key in seen_headlines or not is_relevant(article):
            continue
        seen_urls.add(url_key)
        seen_headlines.add(title_key)
        text = " ".join((article["headline"], article.get("rss_text", ""), article.get("article_text", "")))
        accident_date, accident_date_status = extract_accident_date(text)
        area, area_status = extract_area(text)
        cause, cause_status = extract_cause(text)
        injured, injured_status = extract_count(
            text, r"(?:injured|hurt|wounded|hospitali[sz]ed|घायल)"
        )
        deaths, deaths_status = extract_count(
            text, r"(?:killed|dead|deaths?|fatalities|died|मौत|मारे\s+गए|मृत्यु)"
        )
        extraction_status = article.get("extraction_status", "not_attempted")
        review_flag = (
            "true"
            if extraction_status != "extracted"
            or "unknown" in (accident_date, area, cause, injured, deaths)
            or cause_status == "suspected"
            else "false"
        )
        records.append(
            {
                "accident_id": _accident_id(article, accident_date, area),
                "headline": article["headline"],
                "article_url": article["article_url"],
                "publication_date": article["publication_date"],
                "accident_date": accident_date,
                "accident_date_status": accident_date_status,
                "area": area,
                "area_status": area_status,
                "cause": cause,
                "cause_status": cause_status,
                "injured": injured,
                "injured_status": injured_status,
                "deaths": deaths,
                "deaths_status": deaths_status,
                "source": article["source"],
                "extraction_status": extraction_status,
                "review_flag": review_flag,
            }
        )
    return records
