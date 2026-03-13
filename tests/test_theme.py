import unittest
from types import SimpleNamespace

from generator.phases.theme import generate_title_from_words, generate_title_from_words_and_definitions


class _FakeClient:
    def __init__(self, content):
        self.last_user_content = ""

        def _create(**kwargs):
            self.last_user_content = kwargs["messages"][-1]["content"]
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
            )

        self.chat = SimpleNamespace(
            completions=SimpleNamespace(
                create=_create
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

    def test_final_title_generation_uses_definitions_context(self):
        client = _FakeClient("Ecouri de Toamnă")

        title = generate_title_from_words_and_definitions(
            ["NATURA", "FRUNZA"],
            ["Frunză uscată de toamnă", "Ce ține de lumea vie"],
            client=client,
        )

        self.assertEqual("Ecouri de Toamnă", title)
        self.assertIn("Definițiile finale sunt", client.last_user_content)
        self.assertIn("Frunză uscată de toamnă", client.last_user_content)


if __name__ == "__main__":
    unittest.main()
