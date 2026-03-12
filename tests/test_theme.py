import unittest
from types import SimpleNamespace

from generator.phases.theme import generate_title_from_words


class _FakeClient:
    def __init__(self, content):
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kwargs: SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
                )
            )
        )


class ThemeTests(unittest.TestCase):
    def test_generate_title_uses_model_output_when_valid(self):
        title = generate_title_from_words(
            ["AUR", "ARGINT", "BRONZ"],
            client=_FakeClient("Metale și Ecouri"),
        )

        self.assertEqual("Metale și Ecouri", title)

    def test_generate_title_rejects_redundant_rebus_title(self):
        title = generate_title_from_words(
            ["AUR", "ARGINT", "BRONZ"],
            client=_FakeClient("Rebus Românesc"),
        )

        self.assertNotIn("Rebus", title)
        self.assertNotIn("Românesc", title)
        self.assertTrue(title)

    def test_generate_title_truncates_very_long_model_output(self):
        title = generate_title_from_words(
            ["AUR", "ARGINT", "BRONZ"],
            client=_FakeClient("Acesta este foarte lung dar util pentru test"),
        )

        self.assertEqual(4, len(title.split()))


if __name__ == "__main__":
    unittest.main()
