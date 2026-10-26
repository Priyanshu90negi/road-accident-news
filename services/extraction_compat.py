"""Compatibility call for extraction services loaded before diagnostics support."""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from typing import Any


LOGGER = logging.getLogger(__name__)


def invoke_article_extractor(
    extractor: Callable[..., Any], article_id: int, diagnostics: list[str]
) -> Any:
    """Pass diagnostics when supported, while tolerating an older loaded callable."""
    try:
        parameters = inspect.signature(extractor).parameters
    except (TypeError, ValueError):
        parameters = {}

    diagnostics_parameter = parameters.get("diagnostics")
    accepts_diagnostics = bool(
        diagnostics_parameter
        and diagnostics_parameter.kind is not inspect.Parameter.POSITIONAL_ONLY
    ) or any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    if accepts_diagnostics:
        return extractor(article_id, diagnostics=diagnostics)

    message = (
        "The loaded extraction service does not accept diagnostics; extraction is continuing "
        "without that optional argument. Restart Streamlit to load the current service."
    )
    LOGGER.warning("%s article_id=%s", message, article_id)
    diagnostics.append(message)
    return extractor(article_id)
