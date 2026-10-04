import pytest

pytestmark = pytest.mark.integration


async def test_navigate_returns_a_snapshot_of_the_landing_page(session, site):
    result = await session.navigate(site.url("/index.html"))

    assert result.ok
    assert 'title="Fixture Home"' in result.observation
    assert '- heading "Fixture Site" [level=1]' in result.observation
    assert '- link "Contact form" [ref=' in result.observation


async def test_the_snapshot_header_reports_url_and_tab_count(session, site):
    result = await session.navigate(site.url("/index.html"))

    header = result.observation.splitlines()[0]
    assert site.url("/index.html") in header
    assert "tab=1/1" in header


async def test_clicking_a_link_navigates_and_reports_the_new_page(session, site):
    await session.navigate(site.url("/index.html"))
    ref = _ref_for(session, 'link "Contact form"')

    result = await session.click(ref)

    assert result.ok
    assert "/form.html" in result.observation
    assert '- textbox "Full name" [ref=' in result.observation


async def test_form_fields_are_named_from_their_labels(session, site):
    result = await session.navigate(site.url("/form.html"))

    assert '- textbox "Full name"' in result.observation
    assert '- textbox "Email"' in result.observation
    assert '- combobox "Topic"' in result.observation
    assert '- checkbox "Subscribe"' in result.observation
    assert '- textbox "Message"' in result.observation


async def test_typing_updates_the_field_value_in_the_next_snapshot(session, site):
    await session.navigate(site.url("/form.html"))
    ref = _ref_for(session, 'textbox "Email"')

    result = await session.type_text(ref, "ada@example.test")

    assert result.ok
    assert '[value="ada@example.test"]' in result.observation


async def test_selecting_an_option_updates_the_combobox_value(session, site):
    await session.navigate(site.url("/form.html"))
    ref = _ref_for(session, 'combobox "Topic"')

    result = await session.select_option(ref, "support")

    assert result.ok
    assert '- combobox "Topic" [ref=' in result.observation
    assert '[value="Support"]' in result.observation


async def test_clicking_a_checkbox_flips_its_state(session, site):
    await session.navigate(site.url("/form.html"))
    ref = _ref_for(session, 'checkbox "Subscribe"')
    assert "[unchecked]" in session._last_snapshot

    result = await session.click(ref)

    assert result.ok
    assert '- checkbox "Subscribe" [ref=' in result.observation
    assert "[checked]" in result.observation


async def test_submitting_a_form_reaches_the_result_page(session, site):
    await session.navigate(site.url("/form.html"))
    await session.type_text(_ref_for(session, 'textbox "Full name"'), "Ada")
    ref = _ref_for(session, 'button "Send message"')

    result = await session.submit(ref, "send the contact form")

    assert result.ok
    assert "Submission received" in result.observation
    assert "name: Ada" in result.observation


async def test_new_elements_are_marked_new_after_an_action(session, site):
    await session.navigate(site.url("/modal.html"))
    ref = _ref_for(session, 'button "Open modal"')

    result = await session.click(ref)

    assert '- button "Close modal" [ref=' in result.observation
    assert "[new]" in result.observation
    assert "[new]" not in _line_for(result.observation, 'button "Open modal"')


async def test_an_alert_is_dismissed_and_reported(session, site):
    await session.navigate(site.url("/modal.html"))
    ref = _ref_for(session, 'button "Trigger alert"')

    result = await session.click(ref)

    assert result.ok
    assert "dialog handled" in result.detail
    assert "Something happened" in result.detail


async def test_a_confirm_dialog_is_accepted(session, site):
    await session.navigate(site.url("/modal.html"))

    result = await session.click(_ref_for(session, 'button "Trigger confirm"'))

    assert result.ok
    assert "accepted" in result.observation


async def test_a_link_with_target_blank_becomes_a_second_tab(session, site):
    await session.navigate(site.url("/newtab.html"))

    await session.click(_ref_for(session, 'link "Open form in new tab"'))
    tabs = await session.list_tabs()

    assert len(tabs.data["tabs"]) == 2
    assert any("/form.html" in tab["url"] for tab in tabs.data["tabs"])


async def test_switching_tabs_snapshots_the_other_tab(session, site):
    await session.navigate(site.url("/newtab.html"))
    await session.click(_ref_for(session, 'link "Open form in new tab"'))

    result = await session.switch_tab(1)

    assert result.ok
    assert "tab=2/2" in result.observation
    assert '- heading "Contact form"' in result.observation


async def test_the_last_tab_cannot_be_closed(session, site):
    await session.navigate(site.url("/index.html"))

    result = await session.close_tab(0)

    assert not result.ok
    assert "last tab" in result.error


async def test_elements_inside_a_same_origin_iframe_are_reachable(session, site):
    result = await session.navigate(site.url("/frame.html"))

    assert '- iframe "Embedded form"' in result.observation
    ref = _ref_for(session, 'textbox "Email"')
    typed = await session.type_text(ref, "inside@frame.test")

    assert typed.ok
    assert '[value="inside@frame.test"]' in typed.observation


async def test_waiting_for_text_surfaces_late_content(session, site):
    await session.navigate(site.url("/delayed.html"))

    result = await session.wait_for(text="The late content has arrived.")

    assert result.ok
    assert '- button "Late button" [ref=' in result.observation


async def test_a_stale_ref_is_rejected_with_a_hint(session, site):
    await session.navigate(site.url("/modal.html"))
    ref = _ref_for(session, 'button "Open modal"')
    await session.navigate(site.url("/form.html"))

    result = await session.click(ref)

    assert not result.ok
    assert "older snapshot" in result.error
    assert "no longer on the page" in result.error


async def test_an_unknown_ref_is_rejected_with_a_hint(session, site):
    await session.navigate(site.url("/index.html"))

    result = await session.click("e999")

    assert not result.ok
    assert "unknown ref" in result.error


async def test_a_long_page_snapshot_is_truncated_at_the_budget(session, site):
    session.limits.snapshot_token_budget = 300

    result = await session.navigate(site.url("/long.html"))

    assert "truncated at the snapshot budget" in result.observation


async def test_get_text_returns_the_article_as_markdown(session, site):
    await session.navigate(site.url("/long.html"))

    result = await session.get_text()

    assert result.ok
    assert "Paragraph 1:" in result.observation
    assert "<p>" not in result.observation


async def test_get_text_is_truncated_at_the_text_budget(session, site):
    session.limits.text_token_budget = 100
    await session.navigate(site.url("/long.html"))

    result = await session.get_text()

    assert result.data["truncated"] is True
    assert "truncated at the text budget" in result.observation


async def test_find_returns_matching_snapshot_lines(session, site):
    await session.navigate(site.url("/index.html"))

    result = await session.find("Contact form")

    assert result.ok
    assert result.data["matches"]
    assert all("Contact form" in line for line in result.data["matches"])


async def test_find_reports_no_matches_cleanly(session, site):
    await session.navigate(site.url("/index.html"))

    result = await session.find("nothing like this exists")

    assert result.ok
    assert result.data["matches"] == []


async def test_screenshot_returns_png_bytes(session, site):
    await session.navigate(site.url("/index.html"))

    result = await session.screenshot()

    assert result.ok
    assert result.screenshot[:8] == b"\x89PNG\r\n\x1a\n"


async def test_scrolling_moves_the_viewport(session, site):
    await session.navigate(site.url("/long.html"))

    result = await session.scroll("bottom")

    assert result.ok
    assert "scroll=100%" in result.observation


async def test_going_back_returns_to_the_previous_page(session, site):
    await session.navigate(site.url("/index.html"))
    await session.navigate(site.url("/form.html"))

    result = await session.go_back()

    assert result.ok
    assert "/index.html" in result.observation


async def test_auto_snapshot_can_be_turned_off(session, site):
    session.auto_snapshot = False

    result = await session.navigate(site.url("/index.html"))

    assert result.ok
    assert result.observation is None


async def test_visited_urls_are_collected_as_sources(session, site):
    await session.navigate(site.url("/index.html"))
    await session.navigate(site.url("/form.html"))

    assert session.visited_urls == [site.url("/index.html"), site.url("/form.html")]


def _line_for(snapshot: str, needle: str) -> str:
    for line in snapshot.splitlines():
        if needle in line:
            return line
    raise AssertionError(f"no snapshot line contains {needle!r}:\n{snapshot}")


def _ref_for(session, needle: str) -> str:
    line = _line_for(session._last_snapshot, needle)
    start = line.index("[ref=") + len("[ref=")
    return line[start : line.index("]", start)]


async def test_refs_are_stable_across_snapshots_of_the_same_page(session, site):
    first = await session.navigate(site.url("/form.html"))
    before = _ref_for(session, 'textbox "Email"')

    second = await session.snapshot()
    after = _ref_for(session, 'textbox "Email"')

    assert before == after
    assert first.observation.count("[ref=") == second.observation.count("[ref=")


async def test_a_ref_still_works_after_an_unrelated_action(session, site):
    await session.navigate(site.url("/form.html"))
    email = _ref_for(session, 'textbox "Email"')

    await session.type_text(_ref_for(session, 'textbox "Full name"'), "Ada")
    result = await session.type_text(email, "ada@example.test")

    assert result.ok
    assert '[value="ada@example.test"]' in result.observation


async def test_a_new_page_does_not_reuse_the_previous_pages_refs(session, site):
    await session.navigate(site.url("/form.html"))
    before = set(_all_refs(session.last_snapshot))

    await session.navigate(site.url("/modal.html"))
    after = set(_all_refs(session.last_snapshot))

    assert not (before & after)


async def test_content_that_arrives_over_the_network_is_in_the_next_snapshot(session, site):
    result = await session.navigate(site.url("/delayed.html"))

    assert '- button "Late button" [ref=' in result.observation


def _all_refs(snapshot: str) -> list[str]:
    refs = []
    for line in snapshot.splitlines():
        if "[ref=" in line:
            start = line.index("[ref=") + len("[ref=")
            refs.append(line[start : line.index("]", start)])
    return refs


async def test_options_wrapped_in_presentational_divs_are_visible(session, site):
    result = await session.navigate(site.url("/widgets.html"))

    assert '- option "Cairo" [ref=' in result.observation
    assert '- option "Dubai" [ref=' in result.observation
    assert "presentation" not in result.observation


async def test_an_element_inside_a_display_contents_wrapper_is_visible(session, site):
    result = await session.navigate(site.url("/widgets.html"))

    assert '- button "Inside display contents" [ref=' in result.observation


async def test_a_visible_child_of_a_hidden_parent_is_visible(session, site):
    result = await session.navigate(site.url("/widgets.html"))

    assert '- button "Visible inside hidden" [ref=' in result.observation
    assert "Hidden wrapper text" not in result.observation


async def test_a_transparent_custom_styled_checkbox_is_visible(session, site):
    result = await session.navigate(site.url("/widgets.html"))

    assert '- checkbox "Styled checkbox" [ref=' in result.observation


async def test_a_hidden_input_never_appears(session, site):
    result = await session.navigate(site.url("/widgets.html"))

    assert "secret-token" not in result.observation
    assert "csrf" not in result.observation


async def test_a_button_inside_an_open_shadow_root_can_be_seen_and_clicked(session, site):
    await session.navigate(site.url("/widgets.html"))
    ref = _ref_for(session, 'button "Shadow button"')

    result = await session.click(ref)

    assert result.ok
    assert '- button "Shadow clicked"' in result.observation


async def test_an_element_rebuilt_by_the_page_keeps_its_ref(session, site):
    await session.navigate(site.url("/remount.html"))
    destination = _ref_for(session, 'textbox "Destination"')

    await session.click(_ref_for(session, 'button "Choose origin"'))
    result = await session.type_text(destination, "Dubai")

    assert result.ok
    assert _ref_for(session, 'textbox "Destination"') == destination
    assert '[value="Dubai"]' in result.observation


async def test_a_label_stands_in_for_its_hidden_radio(session, site):
    await session.navigate(site.url("/widgets.html"))
    assert '- radio "One Way" [ref=' in session.last_snapshot
    assert "[unchecked]" in _line_for(session.last_snapshot, 'radio "One Way"')

    result = await session.click(_ref_for(session, 'radio "One Way"'))

    assert result.ok
    assert "[checked]" in _line_for(result.observation, 'radio "One Way"')
    assert "[unchecked]" in _line_for(result.observation, 'radio "Round Trip"')


async def test_a_title_that_adds_information_is_shown_as_a_description(session, site):
    result = await session.navigate(site.url("/widgets.html"))

    assert '- button "7" [ref=' in result.observation
    assert '[description="2026-10-07"]' in result.observation
    assert '[description="2026-11-07"]' in result.observation


async def test_every_interactive_element_on_the_fixture_pages_is_reachable(session, site):
    from app.browser.coverage import find_unreachable

    for page in ("index.html", "form.html", "modal.html", "widgets.html", "checkout.html"):
        await session.navigate(site.url(f"/{page}"))
        unreachable = await find_unreachable(session)
        assert unreachable == [], f"{page}: {unreachable}"


async def test_a_combobox_input_shows_its_current_value(session, site):
    await session.navigate(site.url("/widgets.html"))

    result = await session.type_text(_ref_for(session, 'combobox "City"'), "Cai")

    assert '[value="Cai"]' in _line_for(result.observation, 'combobox "City"')


async def test_a_password_is_never_echoed_into_the_snapshot(session, site):
    await session.navigate(site.url("/widgets.html"))

    result = await session.type_text(_ref_for(session, 'textbox "Password"'), "hunter2")

    assert "hunter2" not in result.observation
    assert "[value=" in _line_for(result.observation, 'textbox "Password"')


async def test_a_covered_button_fails_fast_and_says_why(session, site):
    import time

    await session.navigate(site.url("/widgets.html"))
    started = time.monotonic()

    result = await session.click(_ref_for(session, 'button "Under the shield"'))

    assert not result.ok
    assert "intercepts pointer events" in result.error
    assert time.monotonic() - started < 15
