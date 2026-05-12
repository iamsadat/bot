from jobhunt.agents.tracking import TrackingAgent, TrackingInputs, classify
from jobhunt.models import Application, ApplicationStatus


def test_classify_rejection_and_interview_and_offer():
    assert classify("Unfortunately, we have moved forward with other candidates.") == "rejection"
    assert classify("Can we schedule a call next week via Calendly?") == "interview"
    assert classify("We are excited to extend you an offer of employment") == "offer"
    assert classify("Please complete the take-home coding challenge") == "assessment"
    assert classify("Thanks for the recipe!") == "other"


def test_tracking_moves_pipeline_state(profile, store, bus):
    app = Application(application_id="a1", user_id="u", job_id="acme-1",
                      status=ApplicationStatus.APPLIED)
    inbox = [{
        "company": "Acme",
        "subject": "Interview at Acme",
        "body": "Hi — can we schedule a call?",
    }]
    agent = TrackingAgent(store, bus)
    res = agent.run(
        TrackingInputs(profile=profile, inbox=inbox, applications=[app]),
        task_id="t",
    )
    assert res.output is not None
    assert app.status == ApplicationStatus.INTERVIEW
    assert res.output.transitions
