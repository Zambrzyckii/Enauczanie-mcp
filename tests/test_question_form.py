"""parse_question_form on question HTML recorded from an attempt on the Moodle 5.3 sandbox, sesskey replaced."""
import unittest
from pathlib import Path

from enauczanie_mcp.quizzes import parse_question_form
from enauczanie_mcp.writing import _answer_kept

FIXTURES = Path(__file__).parent / "fixtures"


def form(name: str) -> dict:
    return parse_question_form((FIXTURES / f"{name}.html").read_text(encoding="utf-8"))


def labels(parsed: dict) -> list[str]:
    return [option["label"] for option in parsed["options"]]


class QuestionFormTest(unittest.TestCase):
    def test_multichoice_with_one_answer(self):
        parsed = form("multichoice")
        self.assertEqual((parsed["kind"], parsed["text"]), ("one", "która to liczba pierwsza"))
        # Moodle shuffles the options of each attempt, so this is the order of the recorded one.
        self.assertEqual(labels(parsed), ["5", "2", "4", "3", "7"])
        self.assertEqual(list(parsed["hidden"]), ["q2:1_:sequencecheck"])
        self.assertFalse(any(option["checked"] for option in parsed["options"]))

    def test_saved_choice_reads_as_kept(self):
        parsed = form("multichoice_answered")
        chosen, other = parsed["options"][4], parsed["options"][0]
        self.assertTrue(chosen["checked"])
        self.assertTrue(_answer_kept(parsed, {parsed["name"]: chosen["value"]}))
        self.assertFalse(_answer_kept(parsed, {parsed["name"]: other["value"]}))

    def test_true_false(self):
        parsed = form("truefalse")
        self.assertEqual((parsed["kind"], labels(parsed)), ("one", ["True", "False"]))

    def test_short_answer(self):
        parsed = form("shortanswer")
        self.assertEqual((parsed["kind"], parsed["name"], parsed["current"]), ("text", "q1:3_answer", ""))


if __name__ == "__main__":
    unittest.main()
