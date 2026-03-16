"""Tests for generator.core.dex_cache — DexProvider multi-layer cache."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

from generator.core.dex_cache import (
    DexProvider,
    _format_definitions,
    _sb_lookup_batch,
    _sb_lookup_single,
    _sb_store,
    fetch_from_dexonline,
    lookup_batch,
    parse_definitions_from_html,
)


# ---------------------------------------------------------------------------
# Pure function tests
# ---------------------------------------------------------------------------

class ParseDefinitionsFromHtmlTests(unittest.TestCase):
    def test_empty_html(self):
        self.assertEqual(parse_definitions_from_html(""), [])

    def test_no_tree_def_spans(self):
        html = "<div>No definitions here</div>"
        self.assertEqual(parse_definitions_from_html(html), [])

    def test_single_definition(self):
        html = '<span class="tree-def">A construi ceva.</span>'
        self.assertEqual(parse_definitions_from_html(html), ["A construi ceva."])

    def test_multiple_definitions(self):
        html = (
            '<span class="tree-def">Prima definiție.</span>'
            '<span class="tree-def">A doua definiție.</span>'
        )
        result = parse_definitions_from_html(html)
        self.assertEqual(result, ["Prima definiție.", "A doua definiție."])

    def test_deduplicates(self):
        html = (
            '<span class="tree-def">Aceeași.</span>'
            '<span class="tree-def">Aceeași.</span>'
            '<span class="tree-def">Alta.</span>'
        )
        result = parse_definitions_from_html(html)
        self.assertEqual(result, ["Aceeași.", "Alta."])

    def test_nested_span_tags(self):
        html = '<span class="tree-def"><span>Inner</span> text.</span>'
        result = parse_definitions_from_html(html)
        self.assertEqual(len(result), 1)
        self.assertIn("Inner", result[0])
        self.assertIn("text.", result[0])

    def test_whitespace_normalization(self):
        html = '<span class="tree-def">  Spaced   out   text.  </span>'
        result = parse_definitions_from_html(html)
        self.assertEqual(result, ["Spaced out text."])

    def test_empty_definition_span_skipped(self):
        html = '<span class="tree-def">   </span>'
        self.assertEqual(parse_definitions_from_html(html), [])

    def test_tree_def_html_class(self):
        html = '<span class="tree-def html">Definition with html class.</span>'
        result = parse_definitions_from_html(html)
        self.assertEqual(result, ["Definition with html class."])


class FormatDefinitionsTests(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(_format_definitions(["a", "b"]), "- a\n- b")

    def test_truncation(self):
        defs = [f"def{i}" for i in range(20)]
        result = _format_definitions(defs)
        lines = result.split("\n")
        self.assertEqual(len(lines), 8)

    def test_empty(self):
        self.assertEqual(_format_definitions([]), "")


# ---------------------------------------------------------------------------
# DexProvider — L1 memory-only tests (no Supabase)
# ---------------------------------------------------------------------------

class DexProviderMemoryOnlyTests(unittest.TestCase):
    def setUp(self):
        self.dex = DexProvider()  # no supabase client

    def test_lookup_unknown_returns_none(self):
        self.assertIsNone(self.dex.lookup("NECUNOSCUT"))

    @patch("generator.core.dex_cache.fetch_from_dexonline", return_value=("", "not_found"))
    def test_get_unknown_fetches_dexonline(self, mock_fetch):
        result = self.dex.get("CASA", "casă")
        mock_fetch.assert_called_once_with("casă")
        self.assertIsNone(result)

    @patch("generator.core.dex_cache.fetch_from_dexonline", return_value=("", "not_found"))
    def test_get_caches_none_in_memory(self, mock_fetch):
        self.dex.get("CASA", "casă")
        # Second call should NOT trigger another HTTP fetch
        self.dex.get("CASA", "casă")
        mock_fetch.assert_called_once()

    @patch("generator.core.dex_cache.fetch_from_dexonline")
    def test_get_caches_found_in_memory(self, mock_fetch):
        html = '<span class="tree-def">O locuință.</span>'
        mock_fetch.return_value = (html, "ok")
        result = self.dex.get("CASA", "casă")
        self.assertIn("O locuință.", result)
        # Second call from L1
        result2 = self.dex.get("CASA", "casă")
        self.assertEqual(result, result2)
        mock_fetch.assert_called_once()

    def test_as_dict_empty(self):
        self.assertEqual(self.dex.as_dict(), {})

    @patch("generator.core.dex_cache.fetch_from_dexonline")
    def test_as_dict_includes_found(self, mock_fetch):
        html = '<span class="tree-def">Definiție.</span>'
        mock_fetch.return_value = (html, "ok")
        self.dex.get("MARE", "mare")
        d = self.dex.as_dict()
        self.assertIn("MARE", d)
        self.assertIn("Definiție.", d["MARE"])

    @patch("generator.core.dex_cache.fetch_from_dexonline", return_value=("", "not_found"))
    def test_as_dict_excludes_missing(self, mock_fetch):
        self.dex.get("NIMIC", "nimic")
        self.assertEqual(self.dex.as_dict(), {})

    @patch("generator.core.dex_cache.fetch_from_dexonline", return_value=("", "not_found"))
    def test_prefetch_no_supabase_fetch_missing_false(self, mock_fetch):
        result = self.dex.prefetch(["A", "B"], fetch_missing=False)
        self.assertEqual(result, {})
        mock_fetch.assert_not_called()

    @patch("generator.core.dex_cache.fetch_from_dexonline", return_value=("", "not_found"))
    def test_prefetch_fetch_missing_true(self, mock_fetch):
        self.dex.prefetch(["WORD1", "WORD2"], fetch_missing=True)
        self.assertEqual(mock_fetch.call_count, 2)

    @patch("generator.core.dex_cache.fetch_from_dexonline", return_value=("", "not_found"))
    def test_prefetch_skips_already_cached(self, mock_fetch):
        self.dex._memory["WORD1"] = "- cached"
        self.dex.prefetch(["WORD1", "WORD2"], fetch_missing=True)
        # Only WORD2 should be fetched
        mock_fetch.assert_called_once()

    @patch("generator.core.dex_cache.fetch_from_dexonline")
    def test_prefetch_uses_originals(self, mock_fetch):
        mock_fetch.return_value = ("", "not_found")
        self.dex.prefetch(
            ["CASĂ"],
            originals={"CASĂ": "casă"},
            fetch_missing=True,
        )
        mock_fetch.assert_called_once_with("casă")


# ---------------------------------------------------------------------------
# DexProvider — L2 Supabase tests (mocked)
# ---------------------------------------------------------------------------

def _mock_supabase_with_rows(rows):
    """Create a MagicMock supabase client that returns given rows for select queries."""
    mock = MagicMock()
    query = MagicMock()
    mock.table.return_value.select.return_value = query
    query.eq.return_value = query
    query.in_.return_value = query
    query.limit.return_value = query
    query.execute.return_value = SimpleNamespace(data=rows)
    return mock


class DexProviderSupabaseTests(unittest.TestCase):
    def test_get_hits_supabase_l2(self):
        html = '<span class="tree-def">Din Supabase.</span>'
        sb = _mock_supabase_with_rows([{"html": html, "status": "ok"}])
        dex = DexProvider(sb)
        result = dex.get("CASĂ", "casă")
        self.assertIn("Din Supabase.", result)
        sb.table.assert_called_with("dex_definitions")

    def test_get_caches_supabase_hit_in_l1(self):
        html = '<span class="tree-def">Definiție L2.</span>'
        sb = _mock_supabase_with_rows([{"html": html, "status": "ok"}])
        dex = DexProvider(sb)
        dex.get("TEST", "test")
        # Reset mock to verify second call doesn't hit supabase
        sb.reset_mock()
        result = dex.get("TEST", "test")
        self.assertIn("Definiție L2.", result)
        sb.table.assert_not_called()

    def test_get_supabase_not_found_falls_through_to_l3(self):
        sb = _mock_supabase_with_rows([])
        dex = DexProvider(sb)
        with patch("generator.core.dex_cache.fetch_from_dexonline", return_value=("", "not_found")) as mock_fetch:
            result = dex.get("NOWORD", "noword")
        self.assertIsNone(result)
        mock_fetch.assert_called_once()

    def test_get_supabase_status_not_ok(self):
        sb = _mock_supabase_with_rows([{"html": "", "status": "not_found"}])
        dex = DexProvider(sb)
        # Should recognize it's in DB (found=True) but return None
        result = dex.lookup("MISSING")
        self.assertIsNone(result)

    def test_lookup_queries_supabase_no_http(self):
        html = '<span class="tree-def">Lookup only.</span>'
        sb = _mock_supabase_with_rows([{"html": html, "status": "ok"}])
        dex = DexProvider(sb)
        with patch("generator.core.dex_cache.fetch_from_dexonline") as mock_fetch:
            result = dex.lookup("WORD")
        self.assertIn("Lookup only.", result)
        mock_fetch.assert_not_called()

    def test_lookup_not_found_returns_none_no_http(self):
        sb = _mock_supabase_with_rows([])
        dex = DexProvider(sb)
        with patch("generator.core.dex_cache.fetch_from_dexonline") as mock_fetch:
            result = dex.lookup("WORD")
        self.assertIsNone(result)
        mock_fetch.assert_not_called()

    def test_supabase_exception_returns_none(self):
        sb = MagicMock()
        sb.table.return_value.select.side_effect = RuntimeError("connection error")
        dex = DexProvider(sb)
        with patch("generator.core.dex_cache.fetch_from_dexonline", return_value=("", "not_found")):
            result = dex.lookup("WORD")
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# DexProvider — L3 fetch + store tests
# ---------------------------------------------------------------------------

class DexProviderFetchStoreTests(unittest.TestCase):
    def test_get_stores_in_supabase_after_fetch(self):
        sb = _mock_supabase_with_rows([])  # nothing cached
        sb.table.return_value.upsert.return_value.execute.return_value = SimpleNamespace(data=[])
        dex = DexProvider(sb)
        html = '<span class="tree-def">From dexonline.</span>'
        with patch("generator.core.dex_cache.fetch_from_dexonline", return_value=(html, "ok")):
            result = dex.get("CASA", "casă")
        self.assertIn("From dexonline.", result)
        # Verify upsert was called (store in L2)
        sb.table.return_value.upsert.assert_called_once()
        upsert_arg = sb.table.return_value.upsert.call_args[0][0]
        self.assertEqual(upsert_arg["word"], "CASA")
        self.assertEqual(upsert_arg["status"], "ok")

    @patch("generator.core.dex_cache.time.sleep")
    @patch("generator.core.dex_cache.time.monotonic")
    def test_crawl_delay_respected(self, mock_monotonic, mock_sleep):
        # Flow for _fetch_and_store:
        #   _respect_crawl_delay: skip if _last_fetch_time==0, else monotonic() + maybe sleep
        #   self._last_fetch_time = time.monotonic()
        # So: 1st call uses 1 monotonic (record), 2nd call uses 2 (check + record)
        mock_monotonic.side_effect = [
            1.0,    # 1st _fetch_and_store: record _last_fetch_time
            1.5,    # 2nd _fetch_and_store: _respect_crawl_delay checks elapsed (0.5s < 2s → sleep)
            3.0,    # 2nd _fetch_and_store: record _last_fetch_time
        ]
        dex = DexProvider()
        with patch("generator.core.dex_cache.fetch_from_dexonline", return_value=("", "not_found")):
            dex.get("WORD1", "word1")
            dex.get("WORD2", "word2")
        mock_sleep.assert_called_once()
        sleep_arg = mock_sleep.call_args[0][0]
        self.assertAlmostEqual(sleep_arg, 1.5, places=1)  # 2.0 - 0.5 = 1.5


# ---------------------------------------------------------------------------
# fetch_from_dexonline — retry behavior
# ---------------------------------------------------------------------------

class FetchFromDexonlineTests(unittest.TestCase):
    @patch("generator.core.dex_cache.urllib.request.urlopen")
    def test_ok_response(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'<span class="tree-def">def</span>'
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp
        html, status = fetch_from_dexonline("casă", max_retries=0)
        self.assertEqual(status, "ok")
        self.assertIn("tree-def", html)

    @patch("generator.core.dex_cache.urllib.request.urlopen")
    def test_404_no_retry(self, mock_urlopen):
        import urllib.error
        mock_urlopen.side_effect = urllib.error.HTTPError(
            "url", 404, "Not Found", {}, None
        )
        html, status = fetch_from_dexonline("nonexistent", max_retries=3)
        self.assertEqual(status, "not_found")
        # Only called once (no retries for 404)
        self.assertEqual(mock_urlopen.call_count, 1)

    @patch("generator.core.dex_cache.time.sleep")
    @patch("generator.core.dex_cache.urllib.request.urlopen")
    def test_500_retries_with_backoff(self, mock_urlopen, mock_sleep):
        import urllib.error
        mock_urlopen.side_effect = urllib.error.HTTPError(
            "url", 500, "Server Error", {}, None
        )
        html, status = fetch_from_dexonline("word", max_retries=2)
        self.assertEqual(status, "error")
        # 1 initial + 2 retries = 3 calls
        self.assertEqual(mock_urlopen.call_count, 3)
        # Backoff delays: 2^1=2, 2^2=4
        self.assertEqual(mock_sleep.call_args_list, [call(2), call(4)])

    @patch("generator.core.dex_cache.time.sleep")
    @patch("generator.core.dex_cache.urllib.request.urlopen")
    def test_timeout_retries(self, mock_urlopen, mock_sleep):
        mock_urlopen.side_effect = TimeoutError("timed out")
        html, status = fetch_from_dexonline("word", max_retries=1)
        self.assertEqual(status, "error")
        self.assertEqual(mock_urlopen.call_count, 2)
        mock_sleep.assert_called_once_with(2)

    @patch("generator.core.dex_cache.urllib.request.urlopen")
    def test_page_without_definitions(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"<html><body>No defs</body></html>"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp
        html, status = fetch_from_dexonline("ambiguous", max_retries=0)
        self.assertEqual(status, "not_found")


# ---------------------------------------------------------------------------
# Supabase helper tests
# ---------------------------------------------------------------------------

class SbLookupSingleTests(unittest.TestCase):
    def test_found_ok(self):
        sb = _mock_supabase_with_rows([{"html": "<h>def</h>", "status": "ok"}])
        html, found = _sb_lookup_single(sb, "WORD")
        self.assertEqual(html, "<h>def</h>")
        self.assertTrue(found)

    def test_not_in_db(self):
        sb = _mock_supabase_with_rows([])
        html, found = _sb_lookup_single(sb, "WORD")
        self.assertIsNone(html)
        self.assertFalse(found)

    def test_in_db_but_not_ok(self):
        sb = _mock_supabase_with_rows([{"html": "", "status": "not_found"}])
        html, found = _sb_lookup_single(sb, "WORD")
        self.assertIsNone(html)
        self.assertTrue(found)  # found in DB, just no usable content

    def test_exception_returns_none_not_found(self):
        sb = MagicMock()
        sb.table.return_value.select.side_effect = RuntimeError("db error")
        html, found = _sb_lookup_single(sb, "WORD")
        self.assertIsNone(html)
        self.assertFalse(found)


class SbLookupBatchTests(unittest.TestCase):
    def test_batch_returns_ok_words(self):
        rows = [
            {"word": "CASA", "html": "<h>casa html</h>", "status": "ok"},
            {"word": "MARE", "html": "", "status": "not_found"},
        ]
        sb = _mock_supabase_with_rows(rows)
        result = _sb_lookup_batch(sb, ["CASA", "MARE"])
        self.assertEqual(result["CASA"], "<h>casa html</h>")
        self.assertIsNone(result["MARE"])

    def test_empty_input(self):
        sb = MagicMock()
        result = _sb_lookup_batch(sb, [])
        self.assertEqual(result, {})
        sb.table.assert_not_called()

    def test_exception_skips_chunk(self):
        sb = MagicMock()
        sb.table.return_value.select.return_value.in_.return_value.execute.side_effect = RuntimeError("db error")
        result = _sb_lookup_batch(sb, ["A", "B"])
        self.assertEqual(result, {})


class SbStoreTests(unittest.TestCase):
    def test_store_calls_upsert(self):
        sb = MagicMock()
        sb.table.return_value.upsert.return_value.execute.return_value = SimpleNamespace(data=[])
        _sb_store(sb, "CASA", "casă", "<html>...</html>", "ok")
        sb.table.assert_called_with("dex_definitions")
        sb.table.return_value.upsert.assert_called_once()
        upsert_data = sb.table.return_value.upsert.call_args[0][0]
        self.assertEqual(upsert_data["word"], "CASA")
        self.assertEqual(upsert_data["original"], "casă")
        self.assertEqual(upsert_data["status"], "ok")

    def test_store_exception_does_not_raise(self):
        sb = MagicMock()
        sb.table.return_value.upsert.side_effect = RuntimeError("db error")
        # Should not raise
        _sb_store(sb, "CASA", "casă", "", "error")


# ---------------------------------------------------------------------------
# Legacy lookup_batch wrapper
# ---------------------------------------------------------------------------

class LookupBatchTests(unittest.TestCase):
    def test_delegates_to_prefetch(self):
        rows = [
            {"word": "CASA", "html": '<span class="tree-def">O locuință.</span>', "status": "ok"},
        ]
        sb = _mock_supabase_with_rows(rows)
        result = lookup_batch(sb, ["CASA"])
        self.assertIn("CASA", result)
        self.assertIn("O locuință.", result["CASA"])

    def test_empty_words(self):
        sb = MagicMock()
        result = lookup_batch(sb, [])
        self.assertEqual(result, {})


# ---------------------------------------------------------------------------
# DexProvider — prefetch batch with Supabase
# ---------------------------------------------------------------------------

class DexProviderPrefetchBatchTests(unittest.TestCase):
    def test_prefetch_batch_queries_supabase(self):
        rows = [
            {"word": "CASA", "html": '<span class="tree-def">Locuință.</span>', "status": "ok"},
            {"word": "MARE", "html": '<span class="tree-def">Apă multă.</span>', "status": "ok"},
        ]
        sb = _mock_supabase_with_rows(rows)
        dex = DexProvider(sb)
        result = dex.prefetch(["CASA", "MARE"], fetch_missing=False)
        self.assertEqual(len(result), 2)
        self.assertIn("Locuință.", result["CASA"])
        self.assertIn("Apă multă.", result["MARE"])

    def test_prefetch_deduplicates_words(self):
        rows = [
            {"word": "CASA", "html": '<span class="tree-def">Def.</span>', "status": "ok"},
        ]
        sb = _mock_supabase_with_rows(rows)
        dex = DexProvider(sb)
        result = dex.prefetch(["CASA", "CASA", "CASA"], fetch_missing=False)
        self.assertEqual(len(result), 1)


if __name__ == "__main__":
    unittest.main()
