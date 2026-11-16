"""Tests for OPML parsing and the `memogarden import opml` verb."""

import argparse
import asyncio
import json
from unittest.mock import Mock, patch

import pytest

from mg_client.cli import (
    CliError,
    _feed_key,
    _import_opml,
    build_parser,
    main,
)
from mg_client.opml import OPMLParseError, parse_opml


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

FEEDLY_STYLE = """<?xml version="1.0" encoding="UTF-8"?>
<opml version="1.0">
  <head><title>JS subscriptions in feedly Cloud</title></head>
  <body>
    <outline text="News-like" title="News-like">
      <outline type="rss" text="Feed A" title="Feed A" xmlUrl="https://a.example/feed/" htmlUrl="https://a.example"/>
      <outline type="rss" text="Feed B" title="Feed B" xmlUrl="  https://b.example/rss  "/>
    </outline>
    <outline text="Tech" title="Tech">
      <outline text="Sub" title="Sub">
        <outline type="rss" text="Deep" title="Deep" xmlUrl="https://deep.example/atom" htmlUrl="https://deep.example"/>
      </outline>
      <outline type="rss" text="Feed A again" title="Feed A again" xmlUrl="https://a.example/feed/"/>
    </outline>
    <outline text="Bare" title="Bare">
      <outline text="just a folder, no feeds"/>
    </outline>
  </body>
</opml>
"""


class TestParseOpml:
    def test_folders_become_categories(self):
        feeds = parse_opml(FEEDLY_STYLE)
        deep = next(f for f in feeds if f.xml_url == "https://deep.example/atom")
        assert deep.categories == ["Tech", "Sub"]

    def test_xmlurl_whitespace_stripped(self):
        """Trailing-space feed URLs are the documented 404-loop failure mode
        (rachelbythebay 2024-05-27) — stripped at import."""
        feeds = parse_opml(FEEDLY_STYLE)
        assert "https://b.example/rss" in {f.xml_url for f in feeds}

    def test_duplicate_xmlurl_merged_with_categories(self):
        feeds = parse_opml(FEEDLY_STYLE)
        a = [f for f in feeds if f.xml_url == "https://a.example/feed/"]
        assert len(a) == 1
        assert a[0].categories == ["News-like", "Tech"]

    def test_folder_only_outlines_yield_nothing(self):
        # the Bare folder contributes no feeds
        assert not any(f.xml_url.startswith("bare") for f in parse_opml(FEEDLY_STYLE))
        assert len(parse_opml(FEEDLY_STYLE)) == 3

    def test_html_url_captured(self):
        feeds = parse_opml(FEEDLY_STYLE)
        a = next(f for f in feeds if f.xml_url == "https://a.example/feed/")
        assert a.html_url == "https://a.example"

    def test_invalid_xml_raises(self):
        with pytest.raises(OPMLParseError):
            parse_opml("<not closed")

    def test_no_body_raises(self):
        with pytest.raises(OPMLParseError):
            parse_opml("<?xml version=\"1.0\"?><opml version=\"1.0\"></opml>")


# ---------------------------------------------------------------------------
# CLI verb
# ---------------------------------------------------------------------------


def _env(monkeypatch):
    monkeypatch.setenv("MEMOGARDEN_URL", "http://localhost:8600")
    monkeypatch.setenv("MEMOGARDEN_API_KEY", "mg_sk_agent_test123456")


def _mock_response(status=200, body=None):
    response = Mock()
    response.status_code = status
    response.json.return_value = body if body is not None else {}
    response.raise_for_status = Mock()
    return response


OPML_SIMPLE = (
    "<?xml version=\"1.0\"?><opml version=\"1.0\"><body>"
    "<outline type=\"rss\" text=\"One\" xmlUrl=\"https://one.example/feed\"/>"
    "<outline type=\"rss\" text=\"Two\" xmlUrl=\"https://two.example/rss\"/>"
    "</body></opml>"
)


class TestFeedKey:
    def test_stable_short_hex(self):
        assert _feed_key("https://x.example/f") == _feed_key("https://x.example/f")
        assert len(_feed_key("https://x.example/f")) == 8


class TestImportOpml:
    def _run(self, monkeypatch, tmp_path, existing, opml_text=OPML_SIMPLE, dry_run=False):
        _env(monkeypatch)
        opml_file = tmp_path / "feeds.opml"
        opml_file.write_text(opml_text)

        async def fake_request(self, method, url, **kwargs):
            json = kwargs.get("json")
            if json and json.get("op") == "query":
                return _mock_response(200, {
                    "ok": True, "actor": "usr_test", "timestamp": "2026-11-16T00:00:00+00:00",
                    "result": {
                        "results": [
                            {"data": {"xml_url": url_}} for url_ in existing
                        ],
                        "total": len(existing),
                        "start_index": 0,
                        "count": len(existing),
                    },
                })
            if json and json.get("op") == "add":
                return _mock_response(200, {
                    "ok": True, "actor": "usr_test", "timestamp": "2026-11-16T00:00:00+00:00",
                    "result": {"uuid": "soil_new", "type": "Subscription"},
                })
            raise AssertionError(f"unexpected request: {method} {url} {json}")

        with patch("httpx.AsyncClient.request", new=fake_request):
            return asyncio.run(
                _import_opml(
                    "http://localhost:8600", "mg_sk_agent_test123456",
                    str(opml_file), dry_run,
                )
            )

    def test_fresh_import_adds_all(self, monkeypatch, tmp_path):
        summary = self._run(monkeypatch, tmp_path, existing=set())
        assert summary["added"] == 2
        assert summary["skipped"] == 0
        assert summary["dry_run"] is False

    def test_reimport_is_idempotent(self, monkeypatch, tmp_path):
        existing = {"https://one.example/feed", "https://two.example/rss"}
        summary = self._run(monkeypatch, tmp_path, existing=existing)
        assert summary["added"] == 0
        assert summary["skipped"] == 2

    def test_partial_existing_skips_only_known(self, monkeypatch, tmp_path):
        summary = self._run(monkeypatch, tmp_path, existing={"https://one.example/feed"})
        assert summary["added"] == 1
        assert summary["skipped"] == 1
        assert summary["skipped_feeds"] == ["https://one.example/feed"]

    def test_dry_run_creates_nothing(self, monkeypatch, tmp_path):
        summary = self._run(monkeypatch, tmp_path, existing=set(), dry_run=True)
        assert summary["dry_run"] is True
        assert summary["would_add"] == 2
        assert summary["already_present"] == 0

    def test_imported_facts_carry_feed_metadata(self, monkeypatch, tmp_path):
        captured_calls = []

        async def fake_request(self, method, url, **kwargs):
            json = kwargs.get("json")
            if json and json.get("op") == "query":
                body = {"ok": True, "actor": "usr_test", "timestamp": "2026-11-16T00:00:00+00:00", "actor": "usr_test", "timestamp": "2026-11-16T00:00:00+00:00", "result": {"results": [], "total": 0, "start_index": 0, "count": 0}}
            else:
                captured_calls.append(json or {})
                body = {"ok": True, "actor": "usr_test", "timestamp": "2026-11-16T00:00:00+00:00", "actor": "usr_test", "timestamp": "2026-11-16T00:00:00+00:00", "result": {"uuid": "soil_x"}}
            return _mock_response(200, body)

        _env(monkeypatch)
        opml_file = tmp_path / "feeds.opml"
        opml_file.write_text(OPML_SIMPLE)
        with patch("httpx.AsyncClient.request", new=fake_request):
            asyncio.run(
                _import_opml(
                    "http://localhost:8600", "mg_sk_agent_test123456",
                    str(opml_file), False,
                )
            )
        assert [call["type"] for call in captured_calls] == ["Subscription", "Subscription"]
        assert {call["data"]["xml_url"] for call in captured_calls} == {
            "https://one.example/feed", "https://two.example/rss",
        }
        for call in captured_calls:
            assert call["metadata"]["source"] == "opml"
            assert call["metadata"]["feed_key"] == _feed_key(call["data"]["xml_url"])
            assert "imported_at" in call["metadata"]

    def test_bad_opml_is_a_cli_error(self, monkeypatch, tmp_path):
        _env(monkeypatch)
        opml_file = tmp_path / "broken.opml"
        opml_file.write_text("<not closed")
        with pytest.raises(CliError):
            asyncio.run(
                _import_opml(
                    "http://localhost:8600", "mg_sk_agent_test123456",
                    str(opml_file), False,
                )
            )


class TestImportVerbParsing:
    def test_verb_registered(self):
        parser = build_parser()
        args = parser.parse_args(["import", "opml", "--file", "x.opml"])
        assert args.command == "import"
        assert args.resource == "opml"
        assert args.file == "x.opml"
        assert args.dry_run is False

    def test_dry_run_flag(self):
        parser = build_parser()
        args = parser.parse_args(["import", "opml", "--file", "x.opml", "--dry-run"])
        assert args.dry_run is True

    def test_main_wires_the_verb(self, monkeypatch, tmp_path, capsys):
        _env(monkeypatch)
        opml_file = tmp_path / "feeds.opml"
        opml_file.write_text(OPML_SIMPLE)
        with patch("httpx.AsyncClient.request") as mock_request:
            mock_request.return_value = _mock_response(200, {
                "ok": True, "actor": "usr_test", "timestamp": "2026-11-16T00:00:00+00:00",
                "result": {"results": [], "total": 0, "start_index": 0, "count": 0},
            })
            code = main(["import", "opml", "--file", str(opml_file)])
        assert code == 0
        out = json.loads(capsys.readouterr().out)
        assert out["added"] == 2
