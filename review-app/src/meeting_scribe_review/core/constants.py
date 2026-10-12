"""D-Bus names the app talks to.

Duplicated from the service on purpose (the app cannot import ``meeting_scribe``); a drift test in
the venv asserts they are equal (tests/unit/review_app/test_constants_drift.py).
"""

BUS_NAME = "dev.meetingscribe.Service"
OBJECT_PATH = "/dev/meetingscribe/Service"
MEETINGS_INTERFACE = "dev.meetingscribe.Meetings1"
SUMMARIES_INTERFACE = "dev.meetingscribe.Summaries1"
SETTINGS_INTERFACE = "dev.meetingscribe.Settings1"
MODELS_INTERFACE = "dev.meetingscribe.Models1"
PROPERTIES_INTERFACE = "org.freedesktop.DBus.Properties"
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

SETTINGS_METHODS = ("GetAll", "Set", "Restart")
SETTINGS_SIGNALS = ("SettingsChanged",)
SETTINGS_PROPERTIES = ("RestartRequired",)

MODELS_METHODS = ("GetStatus", "Download", "CancelDownload")
MODELS_SIGNALS = ("ModelsProgress", "ModelsFinished")

# The keyring item the service reads (BR-005); a drift test keeps these equal to its adapter's.
KEY_ATTRIBUTES = {"application": "meeting-scribe", "key": "opencode-api-key"}
KEY_LABEL = "Meeting Scribe OpenCode API key"

# The Hugging Face token item the service reads to download the models (ADR-011); same drift test.
HF_ATTRIBUTES = {"application": "meeting-scribe", "key": "huggingface-token"}
HF_LABEL = "Meeting Scribe Hugging Face token"
