from meeting_scribe.summaries.infrastructure import secret_service_store as store


def test_the_secret_tool_hint_is_derived_from_the_label_and_attributes() -> None:
    hint = store.missing_message()

    assert f'--label="{store.LABEL}"' in hint
    for name, value in store.ATTRIBUTES.items():
        assert f"{name} {value}" in hint
