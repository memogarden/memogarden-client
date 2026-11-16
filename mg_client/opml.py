"""OPML (outline processor markup language) subscription-list parsing.

Handles the real-world zoo: Feedly exports (text+title attributes, nested
folders), trailing whitespace in xmlUrl (the trailing-space 404 loop is a
documented fetcher failure mode — rachelbythebay 2024-05-27), outlines with
no xmlUrl (pure folders), and missing titles. Categories are the folder
path, outermost first.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field


@dataclass
class OPMLFeed:
    """One feed outline with an xmlUrl."""

    xml_url: str
    title: str | None = None
    html_url: str | None = None
    categories: list[str] = field(default_factory=list)


class OPMLParseError(Exception):
    """The file is not parseable OPML."""


def parse_opml(text: str) -> list[OPMLFeed]:
    """Parse OPML text into feeds. Invalid XML or a missing <body> raises
    OPMLParseError; outlines without an xmlUrl are folder markers and are
    folded into their children's categories."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        raise OPMLParseError(f"invalid XML: {e}") from e

    body = root.find("body")
    if body is None:
        raise OPMLParseError("no <body> element — not an OPML document")

    feeds: list[OPMLFeed] = []
    _walk(body, [], feeds)
    return _dedupe(feeds)


def _walk(element, categories: list[str], feeds: list[OPMLFeed]) -> None:
    for outline in element.findall("outline"):
        xml_url = (outline.get("xmlUrl") or outline.get("xmlurl") or "").strip()
        title = (outline.get("title") or outline.get("text") or "").strip() or None

        if not xml_url:
            # a pure folder — descend with its title appended to the path
            child_categories = categories + ([title] if title else [])
            _walk(outline, child_categories, feeds)
            continue

        feeds.append(
            OPMLFeed(
                xml_url=xml_url,
                title=title,
                html_url=(outline.get("htmlUrl") or "").strip() or None,
                categories=list(categories),
            )
        )
        # nested outlines under a feed outline (rare but seen) still walk,
        # inheriting the feed's title as a folder
        _walk(outline, categories + ([title] if title else []), feeds)


def _dedupe(feeds: list[OPMLFeed]) -> list[OPMLFeed]:
    """Same xml_url listed twice (folders overlap in real exports): first
    sighting wins, categories merged."""
    seen: dict[str, OPMLFeed] = {}
    for feed in feeds:
        if feed.xml_url in seen:
            existing = seen[feed.xml_url]
            for category in feed.categories:
                if category not in existing.categories:
                    existing.categories.append(category)
        else:
            seen[feed.xml_url] = feed
    return list(seen.values())
