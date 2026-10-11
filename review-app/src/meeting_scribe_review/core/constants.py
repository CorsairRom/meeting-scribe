"""D-Bus names the app talks to.

Duplicated from the service on purpose (the app cannot import ``meeting_scribe``); a drift test in
the venv asserts they are equal (tests/unit/review_app/test_constants_drift.py).
"""

BUS_NAME = "dev.meetingscribe.Service"
OBJECT_PATH = "/dev/meetingscribe/Service"
MEETINGS_INTERFACE = "dev.meetingscribe.Meetings1"
SUMMARIES_INTERFACE = "dev.meetingscribe.Summaries1"
ERROR_PREFIX = "dev.meetingscribe.Error"

METHODS = (
    "List",
    "GetSpeakers",
    "GetTranscript",
    "GetSpeakerSample",
    "RenameSpeaker",
    "MergeSpeakers",
    "UnmergeSpeaker",
)
SIGNALS = ("ProcessingStateChanged", "ProcessingFinished", "SpeakersChanged")

SUMMARY_METHODS = (
    "Providers",
    "Summarize",
    "CancelSummary",
    "GetSummaryStatus",
    "GetSummary",
)
SUMMARY_SIGNALS = ("SummaryProgress", "SummaryFinished")
