from autopilot.apply.answers import AnswerBook, Question, choose_option, skill_years_from_resume
from conftest import make_job


def test_choose_option():
    assert choose_option("Yes", ["Select...", "Yes", "No"]) == "Yes"
    assert choose_option("No", ["I agree", "No, I do not"]) == "No, I do not"
    assert choose_option("3", ["Less than 1 year", "2-3 Years", "3-5 Years", "More than 5 Years"]) == "3-5 Years"
    assert choose_option("13 LPA", ["Less than 4 LPA", "8-10 LPA", "More than 12 LPA"]) == "More than 10 LPA"
    assert choose_option("Immediate", ["Immediate Joiner", "15 Days", "30 Days"]) == "Immediate Joiner"
    assert choose_option("Prefer not to say", ["Male", "Female", "Decline to self-identify"]) == "Decline to self-identify"
    assert choose_option("Yes", ["I Agree", "I Disagree"]) == "I Agree"
    assert choose_option("Blue", ["Yes", "No"]) is None


def test_skill_years_are_evidence_based(master):
    years = skill_years_from_resume(master)
    assert years["kubernetes"] > years["terraform"]   # Kubernetes in two roles, Terraform only in the current one
    assert years["aws"] >= 3                          # EKS work at Immersive counts as AWS work


def test_profile_and_rule_answers(db, profile, master):
    book = AnswerBook(db, profile, master, interactive=False)
    india = make_job(country="India")
    us = make_job(country="United States", location="Austin, TX")
    ask = lambda label, kind="text", options=(), job=india: book.answer(Question(label, kind, list(options)), job)  # noqa: E731
    assert ask("First Name*") == "Saurabh"
    assert ask("Phone ✱") == "+91 7030126522"
    assert ask("How many years of work experience do you have with Kubernetes?", "number") == "3"
    assert ask("How many years of work experience do you have with Terraform?", "number") == "3"
    assert ask("How many years of experience do you have with Pulumi?", "number") == "3"
    assert ask("Do you have experience with Snowflake?", "radio", ["Yes", "No"]) == "No"
    assert ask("Are you legally authorized to work in this country?", job=india) == "Yes"
    assert ask("Will you now or in the future require visa sponsorship?", job=india) == "No"
    assert ask("Will you now or in the future require visa sponsorship?", job=us) == "Yes"
    assert ask("Notice period (in days)", "number") == "0"
    assert ask("Expected CTC", job=india) == "13 LPA"
    assert ask("Gender", "select", ["Male", "Female", "Decline to self-identify"]) == "Prefer not to say"
    assert ask("Date of Birth") is None           # never guessed


def test_unknown_question_is_asked_once(db, profile, master):
    book = AnswerBook(db, profile, master, interactive=False)
    job = make_job()
    q = Question("Are you comfortable with rotational night shifts?", "radio", ["Yes", "No"], required=True)
    assert book.answer(q, job) is None
    assert [p["question"] for p in db.pending()] == ["Are you comfortable with rotational night shifts?"]
    db.set_answer("Are you comfortable with rotational night shifts?*", "Yes")   # what `answers --pending` does
    assert book.answer(q, job) == "Yes" and db.pending() == []
