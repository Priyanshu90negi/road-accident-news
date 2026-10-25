"""Optional adapter for a user-installed, local spaCy NER model.

No model is downloaded and no external service is contacted. Set
ACCIDENT_NER_MODEL to an already installed model name to enable it.
"""

from __future__ import annotations

import os
from functools import lru_cache


@lru_cache(maxsize=2)
def _load_model(model_name: str):
    try:
        import spacy

        return spacy.load(model_name)
    except (ImportError, OSError):
        return None


def extract_named_entities(text: str) -> list[dict[str, str | int]]:
    model_name = os.environ.get("ACCIDENT_NER_MODEL", "").strip()
    if not model_name or not text:
        return []
    model = _load_model(model_name)
    if model is None:
        return []
    document = model(text)
    return [
        {"text": entity.text, "label": entity.label_, "start": entity.start_char, "end": entity.end_char}
        for entity in document.ents
    ]
