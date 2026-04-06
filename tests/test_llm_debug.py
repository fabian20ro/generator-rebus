import unittest
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from generator.assessment.run_assessment import main as assessment_main
from generator.batch_publish import build_parser as build_batch_publish_parser
from generator.clue_canon import build_parser as build_clue_canon_parser
from generator.core.llm_client import _chat_completion_create
from generator.core.runtime_logging import set_llm_debug_enabled
from generator.loop_controller import build_batch_command, build_parser as build_loop_parser
from generator.rebus import build_parser as build_rebus_parser
from generator.redefine import build_parser as build_redefine_parser
from generator.repair_puzzles import build_parser as build_repair_parser
from generator.retitle import build_parser as build_retitle_parser


class _FakeStreamingClient:
    def __init__(self, chunks):
        self._chunks = chunks
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        if kwargs.get("stream"):
            return iter(self._chunks)
        raise AssertionError("expected streaming request")


class _FakeFallbackClient:
    def __init__(self, response):
        self._response = response
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("stream"):
            raise RuntimeError("stream unsupported")
        return self._response


class _FakeRetryStreamingClient:
    def __init__(self, chunk_batches):
        self._chunk_batches = [list(batch) for batch in chunk_batches]
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not kwargs.get("stream"):
            raise AssertionError("expected streaming request")
        return iter(self._chunk_batches.pop(0))


class LlmDebugTests(unittest.TestCase):
    def tearDown(self):
        set_llm_debug_enabled(False)

    def test_streaming_debug_logs_reasoning_then_output(self):
        chunks = [
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(reasoning_content="gand ", content=None),
                        finish_reason=None,
                    )
                ]
            ),
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(reasoning_content="pas", content=None),
                        finish_reason=None,
                    )
                ]
            ),
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(reasoning_content=None, content="raspuns"),
                        finish_reason=None,
                    )
                ]
            ),
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(reasoning_content=None, content=None),
                        finish_reason="stop",
                    )
                ]
            ),
        ]
        client = _FakeStreamingClient(chunks)
        set_llm_debug_enabled(True)

        with patch("sys.stdout", new=StringIO()) as captured:
            response = _chat_completion_create(
                client,
                model="google/gemma-4-26b-a4b",
                messages=[{"role": "user", "content": "test"}],
                temperature=0.0,
                max_tokens=32,
                purpose="definition_generate",
            )

        output = captured.getvalue()
        self.assertIn("[LLM thinking] gand pas", output)
        self.assertIn("[LLM output] raspuns", output)
        self.assertLess(output.index("[LLM thinking]"), output.index("[LLM output]"))
        self.assertEqual("gand pas", response.choices[0].message.reasoning_content)
        self.assertEqual("raspuns", response.choices[0].message.content)

    def test_debug_falls_back_to_non_stream_and_logs_final_message(self):
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(
                        reasoning_content="plan final",
                        content="rezultat final",
                    ),
                )
            ],
            usage=None,
        )
        client = _FakeFallbackClient(response)
        set_llm_debug_enabled(True)

        with patch("sys.stdout", new=StringIO()) as captured:
            actual = _chat_completion_create(
                client,
                model="google/gemma-4-26b-a4b",
                messages=[{"role": "user", "content": "test"}],
                temperature=0.0,
                max_tokens=32,
                purpose="definition_generate",
            )

        output = captured.getvalue()
        self.assertIn("LLM debug stream fallback", output)
        self.assertIn("[LLM thinking] plan final", output)
        self.assertIn("[LLM output] rezultat final", output)
        self.assertEqual(response, actual)
        self.assertTrue(client.calls[0]["stream"])
        self.assertNotIn("stream", client.calls[1])

    def test_debug_streaming_retry_without_thinking_uses_short_budget(self):
        client = _FakeRetryStreamingClient([
            [
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(reasoning_content="gand lung", content=None),
                            finish_reason=None,
                        )
                    ]
                ),
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(reasoning_content=None, content=None),
                            finish_reason="length",
                        )
                    ]
                ),
            ],
            [
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(reasoning_content=None, content="raspuns scurt"),
                            finish_reason=None,
                        )
                    ]
                ),
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(reasoning_content=None, content=None),
                            finish_reason="stop",
                        )
                    ]
                ),
            ],
        ])
        set_llm_debug_enabled(True)

        with patch("sys.stdout", new=StringIO()) as captured:
            response = _chat_completion_create(
                client,
                model="google/gemma-4-26b-a4b",
                messages=[{"role": "user", "content": "test"}],
                temperature=0.0,
                max_tokens=4000,
                purpose="definition_generate",
            )

        output = captured.getvalue()
        self.assertIn("retry without_thinking", output)
        self.assertEqual(2, len(client.calls))
        self.assertEqual("low", client.calls[0]["reasoning_effort"])
        self.assertEqual(4000, client.calls[0]["max_tokens"])
        self.assertEqual("none", client.calls[1]["reasoning_effort"])
        self.assertEqual(200, client.calls[1]["max_tokens"])
        self.assertEqual("raspuns scurt", response.choices[0].message.content)


class DebugParserTests(unittest.TestCase):
    def tearDown(self):
        set_llm_debug_enabled(False)

    def test_loop_and_batch_publish_accept_debug(self):
        loop_args = build_loop_parser().parse_args(["--debug"])
        batch_args = build_batch_publish_parser().parse_args(["--debug"])

        self.assertTrue(loop_args.debug)
        self.assertTrue(batch_args.debug)

    def test_retitle_redefine_repair_and_rebus_accept_debug(self):
        self.assertTrue(build_retitle_parser().parse_args(["--debug"]).debug)
        self.assertTrue(build_redefine_parser().parse_args(["--debug"]).debug)
        self.assertTrue(build_repair_parser().parse_args(["--debug"]).debug)
        self.assertTrue(build_rebus_parser().parse_args(["theme", "-", "-", "--debug"]).debug)

    def test_clue_canon_subcommands_accept_debug(self):
        parser = build_clue_canon_parser()

        self.assertTrue(parser.parse_args(["simplify-fanout", "--apply", "--debug"]).debug)
        with self.assertRaises(SystemExit):
            parser.parse_args(["backfill", "--apply", "--debug"])

    def test_assessment_accepts_debug(self):
        with patch("generator.assessment.run_assessment.run_assessment") as mock_run:
            mock_run.return_value = SimpleNamespace(
                summary="",
                rows=[],
                total_words=0,
                verified_count=0,
                first_pass_verified_count=0,
                final_verified_count=0,
                avg_semantic=0.0,
                avg_rebus=0.0,
                avg_creativity=0.0,
                avg_first_pass_semantic=0.0,
                avg_first_pass_rebus=0.0,
                avg_first_pass_creativity=0.0,
            )
            with patch("generator.assessment.run_assessment._print_report"):
                with patch("generator.assessment.run_assessment._append_results_tsv"):
                    with patch("sys.argv", ["run_assessment", "--debug", "--no-append-tsv"]):
                        assessment_main()

        self.assertTrue(mock_run.called)

    def test_build_batch_command_forwards_debug(self):
        command = build_batch_command(
            size=11,
            words="generator/output/words.json",
            output_root="generator/output/batch",
            rewrite_rounds=4,
            preparation_attempts=5,
            seed=1234,
            debug=True,
        )

        self.assertIn("--debug", command)


if __name__ == "__main__":
    unittest.main()
