import unittest
from unittest import mock

from mcp.server.mcpserver.exceptions import ToolError

from enauczanie_mcp import writing

from .fake_moodle import FakeMoodle

COURSE, MODULE = 2, 30


def numbers(result: dict) -> list[int]:
    return [question["question"] for question in result["questions"]]


class SequentialQuizTest(unittest.TestCase):
    def setUp(self):
        self.moodle = FakeMoodle([["one"], ["text"], ["one"]])
        patcher = mock.patch.object(writing, "_client", side_effect=lambda: self.moodle)
        patcher.start()
        self.addCleanup(patcher.stop)

    def start(self) -> dict:
        return writing.get_quiz_survey(COURSE, MODULE, confirm=True)

    def test_preview_starts_no_attempt(self):
        with self.assertRaisesRegex(ToolError, "Sequential navigation"):
            writing.get_quiz_survey(COURSE, MODULE)
        self.assertIsNone(self.moodle.attempt)

    def test_start_shows_only_the_first_page(self):
        result = self.start()
        self.assertEqual(numbers(result), [1])
        self.assertEqual(result["page"], "1 of 3")

    def test_next_page_saves_the_answer_and_moves_on(self):
        self.start()
        result = writing.next_quiz_page(COURSE, MODULE, {1: 2})
        self.assertEqual(self.moodle.saved["q7:1_answer"], "1")
        self.assertEqual(self.moodle.attempt["currentpage"], 1)
        self.assertEqual(numbers(result), [2])

    def test_submit_on_the_last_page_finishes_the_attempt(self):
        self.start()
        writing.next_quiz_page(COURSE, MODULE, {1: 2})
        writing.next_quiz_page(COURSE, MODULE, {2: "answer"})
        result = writing.submit_quiz_survey(COURSE, MODULE, {3: 1}, confirm=True)
        self.assertTrue(result["submitted"])
        self.assertEqual(self.moodle.attempt["state"], "finished")
        self.assertEqual(self.moodle.saved["q7:2_answer"], "answer")


if __name__ == "__main__":
    unittest.main()
