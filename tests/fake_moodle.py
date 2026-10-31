"""A fake Moodle web service for the quiz write tools, holding one quiz and its attempt in memory."""
from enauczanie_mcp.moodle_client import MoodleApiError

USAGE = 7


def question_html(slot: int, kind: str, saved: str | None) -> str:
    """Minimal markup of one attempt question, with fields named the way Moodle names them."""
    prefix = f"q{USAGE}:{slot}_"
    head = f'<div class="qtext">Question {slot}</div><input type="hidden" name="{prefix}:sequencecheck" value="1">'
    if kind == "one":
        return head + "".join(
            f'<input type="radio" name="{prefix}answer" value="{value}" id="{prefix}answer{value}"'
            f'{" checked" if saved == str(value) else ""}><label for="{prefix}answer{value}">Option {value}</label>'
            for value in ("0", "1")
        )
    if kind == "text":
        return head + f'<input type="text" name="{prefix}answer" value="{saved or ""}">'
    # Drag-and-drop keeps its answer in a hidden field that the page's script fills.
    return head + f'<input type="hidden" name="{prefix}p1" value="">'


class FakeMoodle:
    """Answers the calls of the quiz tools and keeps Moodle's sequential navigation rules, as in
    quiz_attempt::check_page_access() and set_currentpage() of MOODLE_405_STABLE and MOODLE_500_STABLE."""

    def __init__(self, pages: list[list[str]], navmethod: str = "sequential"):
        self.kinds = [kind for page in pages for kind in page]
        self.pages, slot = [], 1
        for page in pages:
            self.pages.append(list(range(slot, slot + len(page))))
            slot += len(page)
        # Each page lists its slots followed by a 0, as in Moodle's attempt layout.
        self.layout = ",".join(f"{','.join(map(str, slots))},0" for slots in self.pages)
        self.quiz = {"id": 3, "coursemodule": 30, "course": 2, "name": "Quiz", "navmethod": navmethod}
        self.attempt: dict | None = None
        self.saved: dict[str, str] = {}
        self.ignored: set[str] = set()
        self.calls: list[str] = []

    def call(self, function: str, **params):
        self.calls.append(function)
        handler = getattr(self, function, None)
        if handler is None:
            raise AssertionError(f"The fake does not answer {function}")
        return handler(**params)

    def _page_access(self, page: int, allownext: bool) -> bool:
        if not 0 <= page < len(self.pages):
            raise MoodleApiError("Invalid page number", "Invalid page number")
        if self.quiz["navmethod"] != "sequential":
            return True
        current = self.attempt["currentpage"]
        return page == current or (allownext and page == current + 1)

    def mod_quiz_get_quizzes_by_courses(self, **params):
        return {"quizzes": [dict(self.quiz)]}

    def mod_quiz_get_user_attempts(self, quizid: int, status: str):
        wanted = "inprogress" if status == "unfinished" else status
        return {"attempts": [dict(self.attempt)] if self.attempt and self.attempt["state"] == wanted else []}

    def mod_quiz_get_quiz_access_information(self, quizid: int):
        return {"canattempt": True, "preventaccessreasons": []}

    def mod_quiz_start_attempt(self, quizid: int):
        self.attempt = {"id": 5, "attempt": 1, "state": "inprogress", "currentpage": 0, "layout": self.layout}
        return {"attempt": dict(self.attempt), "warnings": []}

    def mod_quiz_get_attempt_data(self, attemptid: int, page: int):
        # get_attempt_questions_data() checks each question's page with allownext false, so only the current page
        # counts as readable here.
        if not self._page_access(page, allownext=False):
            raise MoodleApiError("Out of sequence access", "Out of sequence access")
        questions = [
            {"slot": slot, "number": slot, "type": self.kinds[slot - 1],
             "html": question_html(slot, self.kinds[slot - 1], self.saved.get(f"q{USAGE}:{slot}_answer"))}
            for slot in self.pages[page]
        ]
        return {"questions": questions, "nextpage": -1 if page == len(self.pages) - 1 else page + 1}

    def mod_quiz_view_attempt(self, attemptid: int, page: int):
        if not self._page_access(page, allownext=True):
            raise MoodleApiError("Out of sequence access", "Out of sequence access")
        self.attempt["currentpage"] = page
        return {"status": True, "warnings": []}

    def mod_quiz_process_attempt(self, attemptid: int, finishattempt: int, **data):
        # The web service takes no page and checks none, so it saves answers to any page.
        for key, name in data.items():
            if key.endswith("[name]") and name not in self.ignored:
                self.saved[name] = data[key.replace("[name]", "[value]")]
        if finishattempt:
            self.attempt.update(state="finished", timefinish=1)
        return {"state": self.attempt["state"]}
