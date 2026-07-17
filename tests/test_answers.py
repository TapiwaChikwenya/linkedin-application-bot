from linkedin import Linkedin


def test_experience_question_mapping():
    assert Linkedin.yesNoAnswerForQuestion(
        "Are you legally authorized to work in the United States?"
    ) == "Yes"
    assert Linkedin.yesNoAnswerForQuestion(
        "Will you now or in the future require sponsorship?"
    ) == "No"


def test_unknown_yes_no_question_is_not_guessed():
    assert Linkedin.yesNoAnswerForQuestion("What is your favorite color?") is None


def test_application_action_semantics():
    class Button:
        text = "Review"

        @staticmethod
        def get_attribute(name):
            return "" if name == "aria-label" else None

    assert Linkedin.applicationAction(Button()) == "review"
