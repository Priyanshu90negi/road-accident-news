from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
import trafilatura
from bs4 import BeautifulSoup

from .discovery import USER_AGENT


@dataclass
class ExtractionResult:
    text: str
    resolved_url: str
    status: str
    error: str = ""


class ArticleExtractor:
    def __init__(self, timeout: int = 20, minimum_interval: float = 1.0) -> None:
        self.timeout = timeout
        self.minimum_interval = minimum_interval
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self._robots: dict[str, tuple[RobotFileParser | None, str]] = {}
        self._last_request: dict[str, float] = {}

    def _request(self, url: str, *, allow_redirects: bool = False) -> requests.Response:
        host = urlparse(url).netloc.casefold()
        elapsed = time.monotonic() - self._last_request.get(host, 0.0)
        if elapsed < self.minimum_interval:
            time.sleep(self.minimum_interval - elapsed)
        response = self.session.get(url, timeout=self.timeout, allow_redirects=allow_redirects)
        self._last_request[host] = time.monotonic()
        return response

    def _robots_parser(self, url: str) -> tuple[RobotFileParser | None, str]:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin in self._robots:
            return self._robots[origin]
        robots_url = f"{origin}/robots.txt"
        try:
            response = self._request(robots_url, allow_redirects=True)
        except requests.RequestException as error:
            result = (None, f"Could not retrieve robots.txt: {type(error).__name__}: {error}")
            self._robots[origin] = result
            return result
        if response.status_code == 404:
            parser = RobotFileParser()
            parser.parse([])
            result = (parser, "")
            self._robots[origin] = result
            return result
        if not response.ok:
            result = (None, f"robots.txt returned HTTP {response.status_code}")
            self._robots[origin] = result
            return result
        parser = RobotFileParser()
        parser.set_url(robots_url)
        parser.parse(response.text.splitlines())
        result = (parser, "")
        self._robots[origin] = result
        return result

    @staticmethod
    def _extract_text(document: str, url: str) -> tuple[str, str]:
        soup = BeautifulSoup(document, "html.parser")
        candidates: list[tuple[str, str]] = []

        for favor_precision in (True, False):
            extracted = trafilatura.extract(
                document,
                url=url,
                include_comments=False,
                include_tables=False,
                favor_precision=favor_precision,
                favor_recall=not favor_precision,
            )
            if extracted and extracted.strip():
                candidates.append((extracted.strip(), "extracted"))

        # Some publishers expose the story body in Article/NewsArticle JSON-LD
        # even when the visible markup is difficult for generic extractors.
        def add_jsonld_body(value: object) -> None:
            if isinstance(value, list):
                for child in value:
                    add_jsonld_body(child)
            elif isinstance(value, dict):
                body = value.get("articleBody")
                if isinstance(body, str) and body.strip():
                    body_text = BeautifulSoup(body, "html.parser").get_text("\n", strip=True)
                    if body_text:
                        candidates.append((body_text, "partial"))
                for key in ("@graph", "mainEntity", "mainEntityOfPage"):
                    if key in value:
                        add_jsonld_body(value[key])

        for script in soup.find_all("script", attrs={"type": re.compile(r"application/ld\+json", re.I)}):
            try:
                add_jsonld_body(json.loads(script.string or script.get_text()))
            except (json.JSONDecodeError, TypeError):
                continue

        # Prefer semantically marked article bodies, then <article>/<main>,
        # before falling back to every paragraph on the page.
        body_nodes = soup.select('[itemprop="articleBody"], article, main')
        for node in body_nodes:
            paragraphs = node.find_all(["p", "h2", "h3"])
            body_text = "\n".join(
                paragraph.get_text(" ", strip=True)
                for paragraph in paragraphs
                if paragraph.get_text(" ", strip=True)
            )
            if body_text:
                candidates.append((body_text, "partial"))
        if not candidates:
            fallback = "\n".join(
                paragraph.get_text(" ", strip=True)
                for paragraph in soup.find_all("p")
                if paragraph.get_text(" ", strip=True)
            )
            if fallback.strip():
                candidates.append((fallback.strip(), "partial"))
        if candidates:
            # Choose the longest useful body; feeds and publishers often have
            # short teaser text alongside a complete structured articleBody.
            return max(candidates, key=lambda candidate: len(candidate[0]))
        return "", "no_article_text"

    def extract(self, url: str, maximum_redirects: int = 5) -> ExtractionResult:
        current_url = url
        for _ in range(maximum_redirects + 1):
            robots_parser, robots_error = self._robots_parser(current_url)
            if robots_parser is None:
                return ExtractionResult("", current_url, "robots_unavailable", robots_error)
            if not robots_parser.can_fetch(USER_AGENT, current_url):
                return ExtractionResult("", current_url, "blocked_by_robots", "robots.txt disallows this URL")
            try:
                response = self._request(current_url)
            except requests.RequestException as error:
                return ExtractionResult("", current_url, "http_error", f"{type(error).__name__}: {error}")
            if 300 <= response.status_code < 400:
                location = response.headers.get("Location")
                if not location:
                    return ExtractionResult("", current_url, "http_error", "Redirect response has no Location header")
                current_url = urljoin(current_url, location)
                continue
            try:
                response.raise_for_status()
            except requests.HTTPError as error:
                if response.status_code in {401, 403}:
                    return ExtractionResult(
                        "", current_url, "access_denied",
                        f"Publisher returned HTTP {response.status_code}; use an authorized feed/API or request access.",
                    )
                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After", "")
                    detail = f" Retry-After: {retry_after}." if retry_after else ""
                    return ExtractionResult(
                        "", current_url, "rate_limited",
                        f"Publisher returned HTTP 429; the request was not retried.{detail}",
                    )
                return ExtractionResult("", current_url, "http_error", f"HTTP {response.status_code}: {error}")
            if "html" not in response.headers.get("Content-Type", "").casefold():
                return ExtractionResult("", current_url, "no_article_text", "Response is not HTML")
            content_type = response.headers.get("Content-Type", "")
            has_declared_charset = re.search(r"charset\s*=", content_type, re.IGNORECASE)
            if not has_declared_charset:
                response.encoding = response.apparent_encoding or "utf-8"
            text, status = self._extract_text(response.text, current_url)
            return ExtractionResult(text, response.url or current_url, status)
        return ExtractionResult("", current_url, "http_error", "Redirect limit exceeded")
