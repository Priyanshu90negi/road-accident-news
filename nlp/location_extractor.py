"""Location extraction using a small English/Hindi Dehradun gazetteer."""

from __future__ import annotations

import re

from .preprocess import clean_text
from .ner import extract_named_entities


# Exact locality names are a rule-based baseline; an optional local NER model
# can be layered in later without changing the pipeline's return format.
LOCALITIES = {
    # Major cities and towns
    "Dehradun": ("Dehradun", "देहरादून"),
    "Vikasnagar": ("Vikasnagar", "विकासनगर"),
    "Mussoorie": ("Mussoorie", "मसूरी"),
    "Doiwala": ("Doiwala", "डोईवाला"),
    "Chakrata": ("Chakrata", "चकराता"),
    "Sahaspur": ("Sahaspur", "सहसपुर"),
    "Raipur": ("Raipur", "रायपुर"),
    "Premnagar": ("Premnagar", "प्रेमनगर"),
    "Selaqui": ("Selaqui", "सेलाकुई"),
    "Herbertpur": ("Herbertpur", "हरबर्टपुर"),
    "Kalsi": ("Kalsi", "कालसी"),

    # Major chowks and intersections
    "ONGC Chowk": ("ONGC Chowk", "ओएनजीसी चौक"),
    "Ballupur Chowk": ("Ballupur Chowk", "बल्लूपुर चौक"),
    "Clock Tower": ("Clock Tower", "घंटाघर"),
    "Ghanta Ghar": ("Ghanta Ghar", "घंटाघर"),
    "Astley Hall": ("Astley Hall", "एस्ले हॉल"),
    "Prince Chowk": ("Prince Chowk", "प्रिंस चौक"),
    "Rispana Bridge": ("Rispana Bridge", "रिस्पना पुल"),
    "ISBT Chowk": ("ISBT Chowk", "आईएसबीटी चौक"),
    "Jogiwala Chowk": ("Jogiwala Chowk", "जोगीवाला चौक"),
    "Survey Chowk": ("Survey Chowk", "सर्वे चौक"),
    "Kanwali Road": ("Kanwali Road", "कांवली रोड"),
    "Lal Pul": ("Lal Pul", "लाल पुल"),
    "Jakhan": ("Jakhan", "जाखन"),
    "Pacific Mall": ("Pacific Mall", "पैसिफिक मॉल"),
    "IT Park": ("IT Park", "आईटी पार्क"),
    "Sahastradhara Crossing": (
        "Sahastradhara Crossing", "सहस्रधारा क्रॉसिंग"
    ),
    "Kargi Chowk": ("Kargi Chowk", "कारगी चौक"),
    "Mothrowala": ("Mothrowala", "मोथरोवाला"),

    # Roads and highways
    "Rajpur Road": ("Rajpur Road", "राजपुर रोड"),
    "Chakrata Road": ("Chakrata Road", "चकराता रोड"),
    "Saharanpur Road": ("Saharanpur Road", "सहारनपुर रोड"),
    "Haridwar Road": ("Haridwar Road", "हरिद्वार रोड"),
    "Delhi Road": ("Delhi Road", "दिल्ली रोड"),
    "Mussoorie Road": ("Mussoorie Road", "मसूरी रोड"),
    "Sahastradhara Road": ("Sahastradhara Road", "सहस्रधारा रोड"),
    "Ring Road": ("Ring Road", "रिंग रोड"),
    "GMS Road": ("GMS Road", "जीएमएस रोड"),
    "EC Road": ("EC Road", "ईसी रोड"),
    "Kaulagarh Road": ("Kaulagarh Road", "कौलागढ़ रोड"),
    "Dharampur Road": ("Dharampur Road", "धर्मपुर रोड"),
    "Raipur Road": ("Raipur Road", "रायपुर रोड"),
    "Doiwala Road": ("Doiwala Road", "डोईवाला रोड"),
    "Jolly Grant Road": ("Jolly Grant Road", "जॉली ग्रांट रोड"),
    "NH 7": ("NH 7", "राष्ट्रीय राजमार्ग 7"),
    "NH 307": ("NH 307", "राष्ट्रीय राजमार्ग 307"),

    # Residential and urban localities
    "Dalanwala": ("Dalanwala", "डालनवाला"),
    "Patel Nagar": ("Patel Nagar", "पटेल नगर"),
    "Ballupur": ("Ballupur", "बल्लूपुर"),
    "Clement Town": ("Clement Town", "क्लेमेंट टाउन"),
    "Nehru Colony": ("Nehru Colony", "नेहरू कॉलोनी"),
    "Dharampur": ("Dharampur", "धर्मपुर"),
    "Vasant Vihar": ("Vasant Vihar", "वसंत विहार"),
    "Indira Nagar": ("Indira Nagar", "इंदिरा नगर"),
    "Vijay Park": ("Vijay Park", "विजय पार्क"),
    "Shanti Vihar": ("Shanti Vihar", "शांति विहार"),
    "Defence Colony": ("Defence Colony", "डिफेंस कॉलोनी"),
    "Brahmanwala": ("Brahmanwala", "ब्रह्मणवाला"),
    "Ajabpur": ("Ajabpur", "अजबपुर"),
    "Jogiwala": ("Jogiwala", "जोगीवाला"),
    "Raipur": ("Raipur", "रायपुर"),
    "Ladpur": ("Ladpur", "लाडपुर"),
    "Nathuwawala": ("Nathuwawala", "नत्थुवावाला"),
    "Badripur": ("Badripur", "बद्रीपुर"),
    "Nehrugram": ("Nehrugram", "नेहरूग्राम"),
    "Kedarpuram": ("Kedarpuram", "केदारपुरम"),
    "Miyawala": ("Miyawala", "मियांवाला"),
    "Bhaniyawala": ("Bhaniyawala", "भानियावाला"),
    "Gularghati": ("Gularghati", "गुलरघाटी"),

    # Transport hubs and landmarks
    "ISBT": ("ISBT", "आईएसबीटी"),
    "Dehradun Railway Station": (
        "Dehradun Railway Station", "देहरादून रेलवे स्टेशन"
    ),
    "Harrawala Railway Station": (
        "Harrawala Railway Station", "हर्रावाला रेलवे स्टेशन"
    ),
    "Jolly Grant Airport": (
        "Jolly Grant Airport", "जॉली ग्रांट एयरपोर्ट"
    ),
    "Parade Ground": ("Parade Ground", "परेड ग्राउंड"),
    "Clock Tower Market": ("Clock Tower Market", "घंटाघर बाजार"),
    "ISBT Kashmiri Road": ("ISBT Kashmiri Road", "आईएसबीटी कश्मीरी रोड"),
    "Transport Nagar": ("Transport Nagar", "ट्रांसपोर्ट नगर"),
    "Prince Chowk Bus Stand": (
        "Prince Chowk Bus Stand", "प्रिंस चौक बस अड्डा"
    ),

    # Educational and institutional areas
    "FRI": ("FRI", "एफआरआई"),
    "Forest Research Institute": (
        "Forest Research Institute", "वन अनुसंधान संस्थान"
    ),
    "IMA": ("IMA", "आईएमए"),
    "Graphic Era": ("Graphic Era", "ग्राफिक एरा"),
    "DIT University": ("DIT University", "डीआईटी यूनिवर्सिटी"),
    "UPES Bidholi": ("UPES Bidholi", "यूपीईएस बिधोली"),
    "Uttarakhand University": (
        "Uttarakhand University", "उत्तराखंड यूनिवर्सिटी"
    ),

    # Existing entries and common spellings
    "Prem Nagar": ("Prem Nagar", "प्रेमनगर"),
    "Jolly Grant": ("Jolly Grant", "जॉली ग्रांट"),
    "Selaqui Industrial Area": (
        "Selaqui Industrial Area", "सेलाकुई औद्योगिक क्षेत्र"
    ),
    "Patelnagar": ("Patelnagar", "पटेलनगर"),
    "Vikas Nagar": ("Vikas Nagar", "विकासनगर"),
    "Clementtown": ("Clementtown", "क्लेमेंट टाउन"),
}


LOCALITIES.update({"Chillad": ("Chillad",), "Tyuni": ("Tyuni",)})


def extract_location(text: str) -> tuple[str | None, str | None, str]:
    text = clean_text(text)
    matches: list[tuple[int, int, str]] = []
    for canonical, aliases in LOCALITIES.items():
        for alias in aliases:
            if re.fullmatch(r"[A-Za-z][A-Za-z ]*", alias):
                pattern = re.compile(rf"(?<!\w){re.escape(alias)}(?!\w)", re.IGNORECASE)
            else:
                pattern = re.compile(re.escape(alias), re.IGNORECASE)
            matches.extend((m.start(), -(m.end() - m.start()), canonical) for m in pattern.finditer(text))
    if not matches:
        entities = extract_named_entities(text)
        places = [
            str(entity["text"]) for entity in entities
            if entity["label"] in {"GPE", "LOC", "FAC"}
        ]
        district_mentioned = bool(
            re.search(r"(?<!\w)Dehradun(?!\w)|देहरादून", text, re.IGNORECASE)
            or any(place.casefold() in {"dehradun", "देहरादून"} for place in places)
        )
        if district_mentioned:
            other_place = next(
                (place for place in places if place.casefold() not in {"dehradun", "देहरादून"}),
                None,
            )
            return "Dehradun", other_place, "ner_candidate" if other_place else "district_only"
        return None, None, "unknown"
    matches.sort()
    areas = list(dict.fromkeys(item[2] for item in matches if item[2] != "Dehradun"))
    area = ", ".join(areas) if areas else None
    district_mentioned = bool(re.search(r"(?<!\w)Dehradun(?!\w)|देहरादून", text, re.IGNORECASE))
    if area:
        status = "ambiguous" if len(areas) > 1 else "extracted"
        return "Dehradun", area, status
    if district_mentioned:
        return "Dehradun", None, "district_only"
    return None, None, "unknown"


def extract_state(text: str, district: str | None) -> str | None:
    """Return an explicitly named state or a state mapped from its district."""
    text = clean_text(text)
    if re.search(r"(?<!\w)Uttarakhand(?!\w)|(?<!\w)Uttaranchal(?!\w)", text, re.IGNORECASE):
        return "Uttarakhand"
    if district and district.casefold() == "dehradun":
        return "Uttarakhand"
    return None
