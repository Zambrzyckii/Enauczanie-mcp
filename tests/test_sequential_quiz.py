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

    def test_last_page_leaves_sending_to_submit(self):
        self.start()
        writing.next_quiz_page(COURSE, MODULE, {1: 2})
        writing.next_quiz_page(COURSE, MODULE, {2: "answer"})
        with self.assertRaisesRegex(ToolError, "last page"):
            writing.next_quiz_page(COURSE, MODULE, {3: 1})
        self.assertEqual(self.moodle.attempt["currentpage"], 2)
        self.assertNotIn("q7:3_answer", self.moodle.saved)

    def test_unsupported_question_stops_until_the_user_skips_it(self):
        self.moodle = FakeMoodle([["one", "dragdrop"], ["one"]])
        self.start()
        with self.assertRaisesRegex(ToolError, "Nothing was saved"):
            writing.next_quiz_page(COURSE, MODULE, {1: 1})
        self.assertEqual((self.moodle.saved, self.moodle.attempt["currentpage"]), ({}, 0))
        writing.next_quiz_page(COURSE, MODULE, {1: 1}, skip_unsupported=True)
        self.assertEqual(self.moodle.attempt["currentpage"], 1)

    def test_answer_moodle_did_not_keep_stays_on_the_page(self):
        self.moodle.ignored.add("q7:1_answer")
        self.start()
        with self.assertRaisesRegex(ToolError, "did not accept the answers to questions 1"):
            writing.next_quiz_page(COURSE, MODULE, {1: 2})
        self.assertEqual(self.moodle.attempt["currentpage"], 0)

    def test_submit_before_the_last_page_names_the_unseen_pages(self):
        self.start()
        with self.assertRaisesRegex(ToolError, r"the 2 page\(s\) after this one were not shown"):
            writing.submit_quiz_survey(COURSE, MODULE, {1: 2})
        self.assertNotIn("q7:1_answer", self.moodle.saved)

    def test_free_navigation_shows_and_sends_every_page(self):
        self.moodle = FakeMoodle([["one"], ["text"], ["one"]], navmethod="free")
        self.assertEqual(numbers(self.start()), [1, 2, 3])
        with self.assertRaisesRegex(ToolError, "free navigation"):
            writing.next_quiz_page(COURSE, MODULE, {1: 2})
        result = writing.submit_quiz_survey(COURSE, MODULE, {1: 2, 2: "answer", 3: 1}, confirm=True)
        self.assertTrue(result["submitted"])


if __name__ == "__main__":
    unittest.main()
