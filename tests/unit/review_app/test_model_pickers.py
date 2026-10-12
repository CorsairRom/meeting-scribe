"""The model pickers of the Preferences presenter (AC-16 to AC-20).

A model is picked from a list the service reads, never typed. Fake gateway and key store only.
"""

from meeting_scribe_review.core.errors import ServiceError
from meeting_scribe_review.core.models import KeyStatus, ModelList, ModelOption
from meeting_scribe_review.core.presenters.preferences import (
    PreferencesPresenter,
    PreferencesState,
)
from meeting_scribe_review.core.result import Err, Ok
from tests.unit.review_app.fakes import FakeApiKeyStore, FakeSettingsGateway, setting

PROVIDER = "summaries.provider"
LOCAL = "summaries.local_model"
CLOUD = "summaries.cloud_model"
CLOUD_PROVIDER = "summaries.cloud_provider"

NOT_SUPPORTED = "NOT_SUPPORTED_YET"


def listing(*options: tuple[str, bool]) -> Ok:
    return Ok(
        ModelList(
            "OK",
            tuple(ModelOption(i, ok, "" if ok else NOT_SUPPORTED) for i, ok in options),
        )
    )


def status(code: str) -> Ok:
    return Ok(ModelList(code))


def wire(
    *,
    provider: str = "local",
    local_model: str = "fake-local-model",
    cloud_model: str = "fake-cloud-model",
    models: dict | None = None,
    keys: FakeApiKeyStore | None = None,
) -> tuple[PreferencesPresenter, FakeSettingsGateway, FakeApiKeyStore]:
    gateway = FakeSettingsGateway()
    gateway.fields[0] = setting(PROVIDER, provider, kind="choice", choices=("local", "cloud"))
    gateway.fields[1] = setting(LOCAL, local_model)
    gateway.fields[3] = setting(CLOUD, cloud_model)
    gateway.model_lists = models or {}
    keys = keys or FakeApiKeyStore()
    presenter = PreferencesPresenter(gateway, keys, lambda _state: None)
    presenter.load()
    return presenter, gateway, keys


def test_wording_and_cloud_provider_row() -> None:
    presenter, _, _ = wire()
    state = presenter.state

    assert state.field(PROVIDER).label == "Run summaries"
    assert state.field(PROVIDER).options == (
        ("local", "On this computer"),
        ("cloud", "In the cloud"),
    )
    assert not state.field(CLOUD_PROVIDER).visible

    presenter.edit(PROVIDER, "cloud")

    provider = presenter.state.field(CLOUD_PROVIDER)
    assert provider.visible
    assert (provider.label, provider.options) == ("Provider", (("opencode-go", "OpenCode Go"),))
    assert not {f.key for f in presenter.state.fields if "base_url" in f.key}


def test_local_models_dropdown() -> None:
    presenter, gateway, _ = wire(
        models={"local": listing(("llama3:8b", True), ("fake-local-model", True))}
    )

    field = presenter.state.field(LOCAL)

    assert gateway.args_of("list_models") == [("local",)]
    assert field.options == (("llama3:8b", "llama3:8b"), ("fake-local-model", "fake-local-model"))
    assert field.selectable
    assert field.note == ""
    assert field.unsupported == ()


def test_unreachable_ollama_keeps_the_saved_value_and_says_so() -> None:
    presenter, _, _ = wire(models={"local": status("SUMMARY_MODELS_UNAVAILABLE")})

    field = presenter.state.field(LOCAL)

    assert field.options == (("fake-local-model", "fake-local-model"),)
    assert not field.selectable
    assert "Ollama is not reachable" in field.note
    assert field.value == "fake-local-model"


def test_a_gateway_error_reads_as_an_unavailable_list() -> None:
    presenter, _, _ = wire(models={"local": Err(ServiceError("SERVICE_NOT_RUNNING"))})

    assert not presenter.state.field(LOCAL).selectable


def test_no_installed_model_says_so() -> None:
    presenter, _, _ = wire(models={"local": listing()})

    assert "No models are installed" in presenter.state.field(LOCAL).note


def test_the_cloud_list_is_not_asked_while_the_provider_is_local() -> None:
    _, gateway, _ = wire(keys=FakeApiKeyStore(stored=["k"]))

    assert gateway.args_of("list_models") == [("local",)]


def test_cloud_model_needs_a_key() -> None:
    models = {"cloud": listing(("glm-5", True))}
    presenter, gateway, keys = wire(provider="cloud", models=models)

    field = presenter.state.field(CLOUD)
    assert not field.selectable
    assert "Set the API key first" in field.note
    assert ("cloud",) not in gateway.args_of("list_models")

    keys.stored.append("fake-key")  # what the key editor does through the keyring
    presenter.refresh_key()

    field = presenter.state.field(CLOUD)
    assert gateway.args_of("list_models").count(("cloud",)) == 1
    assert field.selectable
    assert field.options == (("fake-cloud-model", "fake-cloud-model"), ("glm-5", "glm-5"))


def test_switching_to_the_cloud_asks_for_its_list_when_a_key_is_stored() -> None:
    models = {"cloud": listing(("glm-5", True))}
    presenter, gateway, _ = wire(models=models, keys=FakeApiKeyStore(stored=["k"]))

    presenter.edit(PROVIDER, "cloud")

    assert gateway.args_of("list_models") == [("local",), ("cloud",)]
    assert presenter.state.field(CLOUD).selectable


def test_removing_the_key_disables_the_cloud_model_again() -> None:
    models = {"cloud": listing(("glm-5", True))}
    presenter, _, _ = wire(provider="cloud", models=models, keys=FakeApiKeyStore(stored=["k"]))
    assert presenter.state.field(CLOUD).selectable

    presenter.remove_key()

    assert not presenter.state.field(CLOUD).selectable
    assert "Set the API key first" in presenter.state.field(CLOUD).note


def test_a_locked_keyring_and_a_rejected_key_have_their_own_wording() -> None:
    locked, _, _ = wire(provider="cloud", keys=FakeApiKeyStore(status=Ok(KeyStatus.LOCKED)))
    assert "Unlock" in locked.state.field(CLOUD).note

    rejected, _, _ = wire(
        provider="cloud",
        models={"cloud": status("SUMMARY_AUTH_FAILED")},
        keys=FakeApiKeyStore(stored=["k"]),
    )
    assert "rejected" in rejected.state.field(CLOUD).note
    assert not rejected.state.field(CLOUD).selectable


def test_unsupported_models_are_not_selectable() -> None:
    models = {"cloud": listing(("glm-5", True), ("gpt-5", False), ("grok-4", False))}
    presenter, _, _ = wire(provider="cloud", models=models, keys=FakeApiKeyStore(stored=["k"]))

    field = presenter.state.field(CLOUD)

    assert [value for value, _label in field.options] == ["fake-cloud-model", "glm-5"]
    assert field.unsupported == ("gpt-5", "grok-4")
    assert field.unsupported_text == "Not supported yet"

    presenter.edit(CLOUD, "gpt-5")

    assert presenter.state.field(CLOUD).value == "fake-cloud-model"
    assert not presenter.state.can_save

    presenter.edit(CLOUD, "glm-5")

    assert presenter.state.field(CLOUD).value == "glm-5"
    assert presenter.state.can_save


def test_a_saved_model_missing_from_the_list() -> None:
    models = {"cloud": listing(("glm-5", True))}
    presenter, _, _ = wire(
        provider="cloud", cloud_model="old-model", models=models, keys=FakeApiKeyStore(stored=["k"])
    )

    field = presenter.state.field(CLOUD)

    assert field.value == "old-model"  # not silently replaced
    assert field.options[0] == ("old-model", "old-model")
    assert "not in the provider's list" in field.note
    assert not presenter.state.can_save


def test_a_saved_model_that_is_unsupported_is_warned_about() -> None:
    models = {"cloud": listing(("glm-5", True), ("gpt-5", False))}
    presenter, _, _ = wire(
        provider="cloud", cloud_model="gpt-5", models=models, keys=FakeApiKeyStore(stored=["k"])
    )

    field = presenter.state.field(CLOUD)

    assert field.value == "gpt-5"
    assert "not supported yet" in field.note


def test_refresh_asks_for_the_visible_list_again() -> None:
    presenter, gateway, _ = wire(models={"local": listing(("a", True))})

    presenter.refresh_models()

    assert gateway.args_of("list_models") == [("local",), ("local",)]


def test_a_model_pick_is_saved_with_the_set_call() -> None:
    presenter, gateway, _ = wire(models={"local": listing(("llama3:8b", True))})

    presenter.edit(LOCAL, "llama3:8b")
    presenter.save()

    assert gateway.args_of("set_values") == [({LOCAL: "llama3:8b"},)]


def test_a_state_still_holds_no_key() -> None:
    presenter, _, _ = wire(provider="cloud", keys=FakeApiKeyStore(stored=["fake-secret-value"]))

    assert "fake-secret-value" not in repr(presenter.state)
    assert isinstance(presenter.state, PreferencesState)
