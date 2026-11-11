import unittest

from delegation_options import build


class BuildTest(unittest.TestCase):
    def names(self, codex, gemini):
        return [option["name"] for option in build(codex, gemini)]

    def test_claude_only(self):
        self.assertEqual(self.names(False, False), ["fast", "standard", "deep", "fable"])

    def test_codex_before_design(self):
        names = self.names(True, False)
        self.assertLess(names.index("codex"), names.index("design"))
        self.assertNotIn("research", names)

    def test_gemini_research(self):
        research = [o for o in build(False, True) if o["name"] == "research"][0]
        self.assertEqual(research["provider"], "antigravity-oauth")

    def test_every_option_complete(self):
        for option in build(True, True):
            self.assertTrue({"name", "description", "provider", "model", "reasoning_effort"} <= option.keys())


if __name__ == "__main__":
    unittest.main()
