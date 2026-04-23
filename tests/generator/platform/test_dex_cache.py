"""Tests for rebus_generator.platform.io.dex_cache — DexProvider multi-layer cache."""

import json
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

from rebus_generator.platform.io.dex_cache import (
    DexProvider,
    _format_definitions,
    _is_expired,
    _sb_lookup_batch,
    _sb_lookup_single,
    _sb_store,
    fetch_from_dexonline,
    lookup_batch,
    parse_definitions_from_html,
)
from rebus_generator.domain.pipeline_state import WorkingClue, WorkingPuzzle

IT_DEX_FIXTURE = (
    '<div class=" defWrapper "><p>'
    '<span class="def"><b>iț</b>, s.m. Copil neastâmpărat.</span>'
    "</p></div>"
)
IJE_DEX_FIXTURE = (
    '<div class="callout callout-secondary mt-5"><h3>Ortografice DOOM</h3></div>'
    '<div class=" defWrapper "><p>'
    '<span class="def"><b>ije</b> (literă chirilică) s.m.</span>'
    "</p></div>"
)
SEM_DEX_FIXTURE = (
    '<div class="meaningContainer">'
    '<span class="tree-def">Unitate semantică minimală.</span>'
    '<span class="tree-def">Trăsăturile semantice ale unui cuvânt.</span>'
    "</div>"
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

    def test_inline_markup_definition(self):
        html = '<span class="tree-def html">Diminutiv al lui <i>fir</i>.</span>'
        result = parse_definitions_from_html(html)
        self.assertEqual(result, ["Diminutiv al lui fir."])

    def test_usage_category_is_added_to_definition_text(self):
        html = (
            '<div class="callout callout-secondary mt-5"><h3>Arhaisme și regionalisme</h3></div>'
            '<div class=" defWrapper "><p>'
            '<span class="def"><b>clin,</b> <i>clinuri,</i> s.n. unealtă de cizmărie.</span>'
            "</p></div>"
        )
        result = parse_definitions_from_html(html)
        self.assertEqual(
            result,
            ["Arhaisme și regionalisme: clin, clinuri, s.n. unealtă de cizmărie."],
        )

    def test_non_usage_category_is_not_injected(self):
        html = (
            '<div class="callout callout-secondary mt-5"><h3>Sinonime</h3></div>'
            '<div class=" defWrapper "><p>'
            '<span class="def"><b>clin</b> = stan.</span>'
            "</p></div>"
        )
        self.assertEqual(parse_definitions_from_html(html), ["clin = stan."])

    def test_short_word_fixtures_extract_defwrapper_and_tree_defs(self):
        self.assertIn("Copil neastâmpărat", " ".join(parse_definitions_from_html(IT_DEX_FIXTURE)))
        self.assertIn("literă chirilică", " ".join(parse_definitions_from_html(IJE_DEX_FIXTURE)))
        sem_defs = parse_definitions_from_html(SEM_DEX_FIXTURE)
        self.assertIn("Unitate semantică minimală.", sem_defs)
        self.assertIn("Trăsăturile semantice ale unui cuvânt.", sem_defs)


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
# Cache expiration logic
# ---------------------------------------------------------------------------

class IsExpiredTests(unittest.TestCase):
    def test_missing_is_expired(self):
        self.assertTrue(_is_expired(None))
        self.assertTrue(_is_expired(""))

    def test_invalid_is_expired(self):
        self.assertTrue(_is_expired("not-a-date"))

    def test_recent_is_not_expired(self):
        now_str = datetime.now(timezone.utc).isoformat()
        self.assertFalse(_is_expired(now_str))

    def test_old_is_expired(self):
        old_dt = datetime.now(timezone.utc) - timedelta(hours=25)
        self.assertTrue(_is_expired(old_dt.isoformat()))

    def test_offset_naive_parsed_as_utc(self):
        # Even if stored as naive (unlikely), it should be compared as UTC
        naive_str = "2026-01-01T00:00:00"
        self.assertTrue(_is_expired(naive_str))


# ---------------------------------------------------------------------------
# DexProvider — L1 memory-only tests (no Supabase)
# ---------------------------------------------------------------------------

class DexProviderMemoryOnlyTests(unittest.TestCase):
    def setUp(self):
        DexProvider._last_fetch_time = 0.0  # reset class-level state
        DexProvider._short_definition_audit_keys.clear()
        self.dex = DexProvider(local_cache_dir=None)  # no supabase client

    def test_lookup_unknown_returns_none(self):
        self.assertIsNone(self.dex.lookup("NECUNOSCUT"))

    @patch("rebus_generator.platform.io.dex_cache.fetch_from_dexonline", return_value=("", "not_found"))
    def test_get_unknown_fetches_dexonline(self, mock_fetch):
        result = self.dex.get("CASA", "casă")
        mock_fetch.assert_called_once_with("casă")
        self.assertIsNone(result)

    @patch("rebus_generator.platform.io.dex_cache.fetch_from_dexonline", return_value=("", "not_found"))
    def test_get_caches_none_in_memory(self, mock_fetch):
        self.dex.get("CASA", "casă")
        # Second call should NOT trigger another HTTP fetch
        self.dex.get("CASA", "casă")
        mock_fetch.assert_called_once()

    @patch("rebus_generator.platform.io.dex_cache.fetch_from_dexonline")
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

    @patch("rebus_generator.platform.io.dex_cache.fetch_from_dexonline")
    def test_as_dict_includes_found(self, mock_fetch):
        html = '<span class="tree-def">Definiție.</span>'
        mock_fetch.return_value = (html, "ok")
        self.dex.get("MARE", "mare")
        d = self.dex.as_dict()
        self.assertIn("MARE", d)
        self.assertIn("Definiție.", d["MARE"])

    @patch("rebus_generator.platform.io.dex_cache.fetch_from_dexonline", return_value=("", "not_found"))
    def test_as_dict_excludes_missing(self, mock_fetch):
        self.dex.get("NIMIC", "nimic")
        self.assertEqual(self.dex.as_dict(), {})

    @patch("rebus_generator.platform.io.dex_cache.fetch_from_dexonline", return_value=("", "not_found"))
    def test_prefetch_no_supabase_fetch_missing_false(self, mock_fetch):
        result = self.dex.prefetch(["A", "B"], fetch_missing=False)
        self.assertEqual(result, {})
        mock_fetch.assert_not_called()

    @patch("rebus_generator.platform.io.dex_cache.fetch_from_dexonline", return_value=("", "not_found"))
    def test_prefetch_fetch_missing_true(self, mock_fetch):
        self.dex.prefetch(["WORD1", "WORD2"], fetch_missing=True)
        self.assertEqual(mock_fetch.call_count, 2)

    @patch("rebus_generator.platform.io.dex_cache.fetch_from_dexonline", return_value=("", "not_found"))
    def test_prefetch_skips_already_cached(self, mock_fetch):
        self.dex._memory["WORD1"] = "- cached"
        self.dex.prefetch(["WORD1", "WORD2"], fetch_missing=True)
        # Only WORD2 should be fetched
        mock_fetch.assert_called_once()

    @patch("rebus_generator.platform.io.dex_cache.fetch_from_dexonline")
    def test_prefetch_uses_originals(self, mock_fetch):
        mock_fetch.return_value = ("", "not_found")
        self.dex.prefetch(
            ["CASĂ"],
            originals={"CASĂ": "casă"},
            fetch_missing=True,
        )
        mock_fetch.assert_called_once_with("casă")

    @patch("rebus_generator.platform.io.dex_cache.time.sleep")
    @patch("rebus_generator.platform.io.dex_cache.fetch_from_dexonline")
    def test_get_compound_uses_component_lookups(self, mock_fetch, mock_sleep):
        mock_fetch.side_effect = [
            ('<span class="tree-def">Definiție A.</span>', "ok"),
            ('<span class="tree-def">Definiție B.</span>', "ok"),
        ]

        result = self.dex.get("AURI - AMUS", "auri - amus")

        self.assertIn("Definiție A.", result)
        self.assertIn("Definiție B.", result)
        self.assertEqual([call("auri"), call("amus")], mock_fetch.call_args_list)
        mock_sleep.assert_called_once()

    @patch("rebus_generator.platform.io.dex_cache.create_provider")
    def test_for_puzzle_prefetches_split_compounds(self, mock_create_provider):
        dex = MagicMock()
        mock_create_provider.return_value = dex
        puzzle = WorkingPuzzle(
            title="",
            size=11,
            grid=[],
            horizontal_clues=[
                WorkingClue(row_number=1, word_normalized="AURI - AMUS", word_original="auri - amus"),
                WorkingClue(row_number=2, word_normalized="MANGO", word_original="mango"),
            ],
            vertical_clues=[],
        )

        result = DexProvider.for_puzzle(puzzle)

        self.assertIs(result, dex)
        dex.prefetch.assert_called_once_with(
            ["AURI", "AMUS", "MANGO"],
            originals={"AURI": "auri", "AMUS": "amus", "MANGO": "mango"},
        )


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
        dex = DexProvider(sb, local_cache_dir=None)
        result = dex.get("CASĂ", "casă")
        self.assertIn("Din Supabase.", result)
        sb.table.assert_called_with("dex_definitions")

    def test_get_caches_supabase_hit_in_l1(self):
        html = '<span class="tree-def">Definiție L2.</span>'
        sb = _mock_supabase_with_rows([{"html": html, "status": "ok"}])
        dex = DexProvider(sb, local_cache_dir=None)
        dex.get("TEST", "test")
        # Reset mock to verify second call doesn't hit supabase
        sb.reset_mock()
        result = dex.get("TEST", "test")
        self.assertIn("Definiție L2.", result)
        sb.table.assert_not_called()

    def test_get_supabase_not_found_falls_through_to_l3(self):
        sb = _mock_supabase_with_rows([])
        dex = DexProvider(sb, local_cache_dir=None)
        with patch("rebus_generator.platform.io.dex_cache.fetch_from_dexonline", return_value=("", "not_found")) as mock_fetch:
            result = dex.get("NOWORD", "noword")
        self.assertIsNone(result)
        mock_fetch.assert_called_once()

    def test_get_supabase_status_not_ok(self):
        sb = _mock_supabase_with_rows([{"html": "", "status": "not_found"}])
        dex = DexProvider(sb, local_cache_dir=None)
        # Should recognize it's in DB (found=True) but return None
        result = dex.lookup("MISSING")
        self.assertIsNone(result)

    def test_lookup_queries_supabase_no_http(self):
        html = '<span class="tree-def">Lookup only.</span>'
        sb = _mock_supabase_with_rows([{"html": html, "status": "ok"}])
        dex = DexProvider(sb, local_cache_dir=None)
        with patch("rebus_generator.platform.io.dex_cache.fetch_from_dexonline") as mock_fetch:
            result = dex.lookup("WORD")
        self.assertIn("Lookup only.", result)
        mock_fetch.assert_not_called()

    def test_lookup_not_found_returns_none_no_http(self):
        sb = _mock_supabase_with_rows([])
        dex = DexProvider(sb, local_cache_dir=None)
        with patch("rebus_generator.platform.io.dex_cache.fetch_from_dexonline") as mock_fetch:
            result = dex.lookup("WORD")
        self.assertIsNone(result)
        mock_fetch.assert_not_called()

    def test_supabase_exception_returns_none(self):
        sb = MagicMock()
        sb.table.return_value.select.side_effect = RuntimeError("connection error")
        dex = DexProvider(sb, local_cache_dir=None)
        with patch("rebus_generator.platform.io.dex_cache.fetch_from_dexonline", return_value=("", "not_found")):
            result = dex.lookup("WORD")
        self.assertIsNone(result)

    @patch("rebus_generator.platform.io.dex_cache._sb_lookup_single")
    def test_redirect_definition_gets_one_hop_base_sense(self, mock_lookup):
        html_redirect = (
            '<span class="tree-def html">Diminutiv al lui <i>fir</i>.</span>'
            '<span class="tree-def html">Din colțul buzei i se scurge pe bărbie un firișor de sînge.</span>'
            '<span class="tree-def html">Fir + -ișor.</span>'
        )
        html_base = (
            '<span class="tree-def html">'
            'Fiecare dintre elementele lungi și subțiri ale unei fibre textile.'
            "</span>"
        )

        def lookup_side_effect(_client, normalized):
            if normalized == "FIRISOR":
                return html_redirect, True
            if normalized == "FIR":
                return html_base, True
            return None, False

        mock_lookup.side_effect = lookup_side_effect
        dex = DexProvider(MagicMock(), local_cache_dir=None)
        result = dex.get("FIRISOR", "firișor")
        self.assertIn("Definiție directă DEX pentru „FIRISOR”: Diminutiv al lui fir.", result)
        self.assertIn("Sens bază pentru „fir”", result)
        self.assertIn("elementele lungi și subțiri", result)
        self.assertIn("Fir + -ișor.", result)

    def test_uncertain_short_definition_is_logged(self):
        sb = _mock_supabase_with_rows([
            {"html": '<span class="tree-def html">Mic obiect decorativ.</span>', "status": "ok"}
        ])
        dex = DexProvider(sb, local_cache_dir=None)
        result = dex.get("BIBEL", "bibel")
        self.assertIn("Mic obiect decorativ.", result)
        self.assertEqual(
            dex.uncertain_short_definitions(),
            [{"word": "BIBEL", "definition": "Mic obiect decorativ."}],
        )

    @patch("rebus_generator.platform.io.dex_cache._sb_lookup_single")
    def test_single_word_gloss_gets_base_sense(self, mock_lookup):
        html_gloss = '<span class="tree-def html">Corabie.</span>'
        html_base = '<span class="tree-def html">Navă mare pentru transport pe apă.</span>'

        def lookup_side_effect(_client, normalized):
            if normalized == "ARCA":
                return html_gloss, True
            if normalized == "CORABIE":
                return html_base, True
            return None, False

        mock_lookup.side_effect = lookup_side_effect
        dex = DexProvider(MagicMock(), local_cache_dir=None)
        result = dex.get("ARCA", "arcă")
        self.assertIn("Definiție directă DEX pentru „ARCA”: Corabie.", result)
        self.assertIn("Sens bază pentru „Corabie”", result)
        self.assertIn("Navă mare", result)

    @patch("rebus_generator.platform.io.dex_cache._sb_lookup_single")
    def test_action_pattern_gets_base_sense(self, mock_lookup):
        html_action = '<span class="tree-def html">Acțiunea de a (se) abona.</span>'
        html_base = '<span class="tree-def html">A se înscrie pentru a primi periodic o publicație sau un serviciu.</span>'

        def lookup_side_effect(_client, normalized):
            if normalized == "ABONARE":
                return html_action, True
            if normalized == "ABONA":
                return html_base, True
            return None, False

        mock_lookup.side_effect = lookup_side_effect
        dex = DexProvider(MagicMock(), local_cache_dir=None)
        result = dex.get("ABONARE", "abonare")
        self.assertIn("Definiție directă DEX pentru „ABONARE”: Acțiunea de a (se) abona.", result)
        self.assertIn("Sens bază pentru „abona”", result)
        self.assertIn("A se înscrie", result)

    @patch("rebus_generator.platform.io.dex_cache._sb_lookup_single")
    def test_fact_pattern_gets_base_sense(self, mock_lookup):
        html_fact = '<span class="tree-def html">Faptul de a se milostivi.</span>'
        html_base = '<span class="tree-def html">A arăta milă, a se îndura.</span>'

        def lookup_side_effect(_client, normalized):
            if normalized == "MILOSTIVIRE":
                return html_fact, True
            if normalized == "MILOSTIVI":
                return html_base, True
            return None, False

        mock_lookup.side_effect = lookup_side_effect
        dex = DexProvider(MagicMock(), local_cache_dir=None)
        result = dex.get("MILOSTIVIRE", "milostivire")
        self.assertIn("Definiție directă DEX pentru „MILOSTIVIRE”: Faptul de a se milostivi.", result)
        self.assertIn("Sens bază pentru „milostivi”", result)
        self.assertIn("A arăta milă", result)

    @patch("rebus_generator.platform.io.dex_cache._sb_lookup_single")
    def test_property_pattern_gets_base_sense(self, mock_lookup):
        html_property = '<span class="tree-def html">Proprietatea de a fi acru; gust acru, înțepător.</span>'
        html_base = '<span class="tree-def html">Care are gust înțepător, specific oțetului.</span>'

        def lookup_side_effect(_client, normalized):
            if normalized == "ACREALA":
                return html_property, True
            if normalized == "ACRU":
                return html_base, True
            return None, False

        mock_lookup.side_effect = lookup_side_effect
        dex = DexProvider(MagicMock(), local_cache_dir=None)
        result = dex.get("ACREALA", "acreală")
        self.assertIn("Definiție directă DEX pentru „ACREALA”: Proprietatea de a fi acru; gust acru, înțepător.", result)
        self.assertIn("Sens bază pentru „acru”", result)
        self.assertIn("gust înțepător", result)

    @patch("rebus_generator.platform.io.dex_cache._sb_lookup_single")
    def test_unit_fraction_pattern_gets_base_sense(self, mock_lookup):
        html_fraction = '<span class="tree-def html">A zecea parte dintr-un henry.</span>'
        html_base = '<span class="tree-def html">Unitate de măsură a inductanței electrice.</span>'

        def lookup_side_effect(_client, normalized):
            if normalized == "DECIHENRI":
                return html_fraction, True
            if normalized == "HENRY":
                return html_base, True
            return None, False

        mock_lookup.side_effect = lookup_side_effect
        dex = DexProvider(MagicMock(), local_cache_dir=None)
        result = dex.get("DECIHENRI", "decihenri")
        self.assertIn("Definiție directă DEX pentru „DECIHENRI”: A zecea parte dintr-un henry.", result)
        self.assertIn("Sens bază pentru „henry”", result)
        self.assertIn("inductanței electrice", result)


class DexProviderLocalDiskCacheTests(unittest.TestCase):
    def test_get_hits_local_disk_before_supabase(self):
        html = '<span class="tree-def">Din cache local.</span>'
        with tempfile.TemporaryDirectory() as temp_dir:
            (Path(temp_dir) / "CASA.json").write_text(json.dumps({
                "word": "CASA",
                "original": "casă",
                "status": "ok",
                "html": html,
            }, ensure_ascii=False), encoding="utf-8")
            sb = MagicMock()
            dex = DexProvider(sb, local_cache_dir=temp_dir)
            with patch("rebus_generator.platform.io.dex_cache.fetch_from_dexonline") as mock_fetch:
                result = dex.get("CASĂ", "casă")
        self.assertIn("Din cache local.", result)
        sb.table.assert_not_called()
        mock_fetch.assert_not_called()

    def test_lookup_uses_local_negative_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            (Path(temp_dir) / "CASA.json").write_text(json.dumps({
                "word": "CASA",
                "original": "casă",
                "status": "not_found",
                "html": "",
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }, ensure_ascii=False), encoding="utf-8")
            sb = MagicMock()
            dex = DexProvider(sb, local_cache_dir=temp_dir)
            with patch("rebus_generator.platform.io.dex_cache.fetch_from_dexonline") as mock_fetch:
                result = dex.lookup("CASĂ")
        self.assertIsNone(result)
        sb.table.assert_not_called()
        mock_fetch.assert_not_called()

    def test_lookup_reparses_parseable_local_negative_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            (Path(temp_dir) / "IJE.json").write_text(json.dumps({
                "word": "IJE",
                "original": "ije",
                "status": "not_found",
                "html": IJE_DEX_FIXTURE,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }, ensure_ascii=False), encoding="utf-8")
            sb = MagicMock()
            dex = DexProvider(sb, local_cache_dir=temp_dir)
            with patch("rebus_generator.platform.io.dex_cache.fetch_from_dexonline") as mock_fetch:
                result = dex.get("IJE", "ije")
        self.assertIn("literă chirilică", result)
        sb.table.assert_not_called()
        mock_fetch.assert_not_called()

    def test_lookup_local_negative_cache_expired_triggers_refetch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            (Path(temp_dir) / "CASA.json").write_text(json.dumps({
                "word": "CASA",
                "original": "casă",
                "status": "not_found",
                "html": "",
                "fetched_at": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
            }, ensure_ascii=False), encoding="utf-8")
            sb = MagicMock()
            dex = DexProvider(sb, local_cache_dir=temp_dir)
            with patch("rebus_generator.platform.io.dex_cache.fetch_from_dexonline") as mock_fetch:
                mock_fetch.return_value = ("", "not_found")
                result = dex.get("CASĂ", "casă")
        self.assertIsNone(result)
        # Should have called fetch because local was expired
        mock_fetch.assert_called_once()

    def test_prefetch_uses_local_disk_before_supabase(self):
        html = '<span class="tree-def">Local batch.</span>'
        with tempfile.TemporaryDirectory() as temp_dir:
            (Path(temp_dir) / "CASA.json").write_text(json.dumps({
                "word": "CASA",
                "original": "casă",
                "status": "ok",
                "html": html,
            }, ensure_ascii=False), encoding="utf-8")
            sb = MagicMock()
            dex = DexProvider(sb, local_cache_dir=temp_dir)
            result = dex.prefetch(["CASA"], fetch_missing=False)
        self.assertIn("Local batch.", result["CASA"])
        sb.table.assert_not_called()


# ---------------------------------------------------------------------------
# DexProvider — L3 fetch + store tests
# ---------------------------------------------------------------------------

class DexProviderFetchStoreTests(unittest.TestCase):
    def setUp(self):
        DexProvider._last_fetch_time = 0.0  # reset class-level state
        DexProvider._short_definition_audit_keys.clear()

    def test_get_stores_in_supabase_after_fetch(self):
        sb = _mock_supabase_with_rows([])  # nothing cached
        sb.table.return_value.upsert.return_value.execute.return_value = SimpleNamespace(data=[])
        dex = DexProvider(sb, local_cache_dir=None)
        html = '<span class="tree-def">From dexonline.</span>'
        with patch("rebus_generator.platform.io.dex_cache.fetch_from_dexonline", return_value=(html, "ok")):
            result = dex.get("CASA", "casă")
        self.assertIn("From dexonline.", result)
        # Verify upsert was called (store in L2)
        sb.table.return_value.upsert.assert_called_once()
        upsert_arg = sb.table.return_value.upsert.call_args[0][0]
        self.assertEqual(upsert_arg["word"], "CASA")
        self.assertEqual(upsert_arg["status"], "ok")

    def test_get_stores_in_local_disk_after_fetch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dex = DexProvider(local_cache_dir=temp_dir)
            html = '<span class="tree-def">From dexonline.</span>'
            with patch("rebus_generator.platform.io.dex_cache.fetch_from_dexonline", return_value=(html, "ok")):
                result = dex.get("CASA", "casă")
            cached = json.loads((Path(temp_dir) / "CASA.json").read_text(encoding="utf-8"))
        self.assertIn("From dexonline.", result)
        self.assertEqual(cached["status"], "ok")
        self.assertEqual(cached["original"], "casă")
        self.assertIn("tree-def", cached["html"])

    @patch("rebus_generator.platform.io.dex_cache.time.sleep")
    @patch("rebus_generator.platform.io.dex_cache.time.monotonic")
    def test_crawl_delay_respected(self, mock_monotonic, mock_sleep):
        # Flow for _fetch_and_store:
        #   _respect_crawl_delay: skip if _last_fetch_time==0, else monotonic() + maybe sleep
        #   self._last_fetch_time = time.monotonic()
        # So: 1st call uses 1 monotonic (record), 2nd call uses 2 (check + record)
        mock_monotonic.side_effect = [
            1.0,    # 1st _fetch_and_store: record _last_fetch_time
            1.5,    # 2nd _fetch_and_store: _respect_crawl_delay checks elapsed (0.5s < 3s → sleep)
            4.0,    # 2nd _fetch_and_store: record _last_fetch_time
        ]
        dex = DexProvider(local_cache_dir=None)
        with patch("rebus_generator.platform.io.dex_cache.fetch_from_dexonline", return_value=("", "not_found")):
            dex.get("WORD1", "word1")
            dex.get("WORD2", "word2")
        mock_sleep.assert_called_once()
        sleep_arg = mock_sleep.call_args[0][0]
        self.assertAlmostEqual(sleep_arg, 2.5, places=1)  # 3.0 - 0.5 = 2.5


# ---------------------------------------------------------------------------
# fetch_from_dexonline — retry behavior
# ---------------------------------------------------------------------------

class FetchFromDexonlineTests(unittest.TestCase):
    @patch("rebus_generator.platform.io.dex_cache.urllib.request.urlopen")
    def test_ok_response(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'<span class="tree-def">def</span>'
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp
        html, status = fetch_from_dexonline("casă", max_retries=0)
        self.assertEqual(status, "ok")
        self.assertIn("tree-def", html)

    @patch("rebus_generator.platform.io.dex_cache.urllib.request.urlopen")
    def test_defwrapper_response_is_ok(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = IJE_DEX_FIXTURE.encode("utf-8")
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp
        html, status = fetch_from_dexonline("ije", max_retries=0)
        self.assertEqual(status, "ok")
        self.assertIn("defWrapper", html)

    @patch("rebus_generator.platform.io.dex_cache.urllib.request.urlopen")
    def test_404_no_retry(self, mock_urlopen):
        import urllib.error
        mock_urlopen.side_effect = urllib.error.HTTPError(
            "url", 404, "Not Found", {}, None
        )
        html, status = fetch_from_dexonline("nonexistent", max_retries=3)
        self.assertEqual(status, "not_found")
        # Only called once (no retries for 404)
        self.assertEqual(mock_urlopen.call_count, 1)

    @patch("rebus_generator.platform.io.dex_cache.time.sleep")
    @patch("rebus_generator.platform.io.dex_cache.urllib.request.urlopen")
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

    @patch("rebus_generator.platform.io.dex_cache.time.sleep")
    @patch("rebus_generator.platform.io.dex_cache.urllib.request.urlopen")
    def test_timeout_retries(self, mock_urlopen, mock_sleep):
        mock_urlopen.side_effect = TimeoutError("timed out")
        html, status = fetch_from_dexonline("word", max_retries=1)
        self.assertEqual(status, "error")
        self.assertEqual(mock_urlopen.call_count, 2)
        mock_sleep.assert_called_once_with(2)

    @patch("rebus_generator.platform.io.dex_cache.urllib.request.urlopen")
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
        recent = datetime.now(timezone.utc).isoformat()
        sb = _mock_supabase_with_rows([{"html": "", "status": "not_found", "fetched_at": recent}])
        html, found = _sb_lookup_single(sb, "WORD")
        self.assertIsNone(html)
        self.assertTrue(found)  # found in DB, just no usable content

    def test_in_db_but_expired_returns_not_found(self):
        old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        sb = _mock_supabase_with_rows([{"html": "", "status": "not_found", "fetched_at": old}])
        html, found = _sb_lookup_single(sb, "WORD")
        self.assertIsNone(html)
        self.assertFalse(found)  # expired entries count as not found in DB

    def test_exception_returns_none_not_found(self):
        sb = MagicMock()
        sb.table.return_value.select.side_effect = RuntimeError("db error")
        html, found = _sb_lookup_single(sb, "WORD")
        self.assertIsNone(html)
        self.assertFalse(found)


class SbLookupBatchTests(unittest.TestCase):
    def test_batch_returns_ok_words(self):
        recent = datetime.now(timezone.utc).isoformat()
        rows = [
            {"word": "CASA", "html": "<h>casa html</h>", "status": "ok", "fetched_at": recent},
            {"word": "MARE", "html": "", "status": "not_found", "fetched_at": recent},
        ]
        sb = _mock_supabase_with_rows(rows)
        result = _sb_lookup_batch(sb, ["CASA", "MARE"])
        self.assertEqual(result["CASA"], "<h>casa html</h>")
        self.assertIn("MARE", result)
        self.assertIsNone(result["MARE"])

    def test_batch_skips_expired_negative_entries(self):
        recent = datetime.now(timezone.utc).isoformat()
        old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        rows = [
            {"word": "RECENT", "html": "", "status": "not_found", "fetched_at": recent},
            {"word": "OLD", "html": "", "status": "not_found", "fetched_at": old},
        ]
        sb = _mock_supabase_with_rows(rows)
        result = _sb_lookup_batch(sb, ["RECENT", "OLD"])
        self.assertIn("RECENT", result)
        self.assertIsNone(result["RECENT"])
        self.assertNotIn("OLD", result)

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
        with patch("rebus_generator.platform.io.dex_cache._DEFAULT_LOCAL_CACHE_DIR", None):
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
        dex = DexProvider(sb, local_cache_dir=None)
        result = dex.prefetch(["CASA", "MARE"], fetch_missing=False)
        self.assertEqual(len(result), 2)
        self.assertIn("Locuință.", result["CASA"])
        self.assertIn("Apă multă.", result["MARE"])

    def test_prefetch_deduplicates_words(self):
        rows = [
            {"word": "CASA", "html": '<span class="tree-def">Def.</span>', "status": "ok"},
        ]
        sb = _mock_supabase_with_rows(rows)
        dex = DexProvider(sb, local_cache_dir=None)
        result = dex.prefetch(["CASA", "CASA", "CASA"], fetch_missing=False)
        self.assertEqual(len(result), 1)


class DexProviderAuditTests(unittest.TestCase):
    def setUp(self):
        DexProvider._short_definition_audit_keys.clear()

    def test_short_definition_audit_is_deduped_per_run_and_word(self):
        dex1 = DexProvider(local_cache_dir=None)
        dex2 = DexProvider(local_cache_dir=None)
        with (
            patch("rebus_generator.platform.io.dex_cache.current_run_id", return_value="run-1"),
            patch("rebus_generator.platform.io.dex_cache.audit") as mock_audit,
        ):
            dex1._remember_uncertain_short_definition("CASA", "Diminutiv al lui cas")
            dex2._remember_uncertain_short_definition("CASA", "Diminutiv al lui cas")

        self.assertEqual(1, mock_audit.call_count)


if __name__ == "__main__":
    unittest.main()
