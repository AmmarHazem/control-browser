from collections import Counter

from app.browser.snapshot import parse_nodes, render_snapshot


def collected(nodes, **overrides):
    payload = {
        "url": "http://site.test/form.html",
        "title": "Contact form",
        "nodes": nodes,
        "scrollPercent": 0,
    }
    payload.update(overrides)
    return payload


def render(nodes, previous=None, budget=8000, **overrides):
    payload = collected(nodes, **overrides)
    return render_snapshot(
        url=payload["url"],
        title=payload["title"],
        nodes=parse_nodes(payload["nodes"]),
        tab_index=0,
        tab_count=1,
        scroll_percent=payload["scrollPercent"],
        previous_signatures=previous,
        token_budget=budget,
    )


def test_header_carries_url_title_tabs_and_scroll():
    out = render([], scrollPercent=42)
    header = out.text.splitlines()[0]
    assert "url=http://site.test/form.html" in header
    assert 'title="Contact form"' in header
    assert "tab=1/1" in header
    assert "scroll=42%" in header


def test_interactive_elements_carry_a_ref():
    out = render(
        [
            {"role": "heading", "name": "Contact form", "level": 1},
            {"role": "textbox", "name": "Email", "ref": "e3"},
            {"role": "button", "name": "Send message", "ref": "e4"},
        ]
    )
    assert '- heading "Contact form" [level=1]' in out.text
    assert '- textbox "Email" [ref=e3]' in out.text
    assert '- button "Send message" [ref=e4]' in out.text


def test_states_and_values_are_rendered():
    out = render(
        [
            {"role": "textbox", "name": "Email", "ref": "e1", "value": "a@b.test"},
            {"role": "checkbox", "name": "Subscribe", "ref": "e2", "states": ["unchecked"]},
            {"role": "button", "name": "Send", "ref": "e3", "states": ["disabled"]},
            {
                "role": "combobox",
                "name": "Topic",
                "ref": "e4",
                "value": "Sales",
                "options": ["sales", "support"],
            },
        ]
    )
    assert '- textbox "Email" [ref=e1] [value="a@b.test"]' in out.text
    assert '- checkbox "Subscribe" [ref=e2] [unchecked]' in out.text
    assert '- button "Send" [ref=e3] [disabled]' in out.text
    assert '- combobox "Topic" [ref=e4] [value="Sales"] [options=sales,support]' in out.text


def test_children_are_nested_under_a_trailing_colon():
    out = render(
        [
            {
                "role": "navigation",
                "name": "Main",
                "children": [
                    {
                        "role": "list",
                        "children": [{"role": "link", "name": "Home", "ref": "e1"}],
                    }
                ],
            }
        ]
    )
    lines = out.text.splitlines()
    assert '- navigation "Main":' in lines
    assert "  - list:" in lines
    assert '    - link "Home" [ref=e1]' in lines


def test_plain_text_is_rendered_as_a_single_text_line():
    out = render([{"role": "paragraph", "text": "The quick brown fox."}])
    assert "- text: The quick brown fox." in out.text.splitlines()


def test_a_named_container_keeps_its_role_and_nests_its_text():
    out = render([{"role": "dialog", "name": "Cookie consent", "text": "We use cookies."}])
    lines = out.text.splitlines()
    assert '- dialog "Cookie consent":' in lines
    assert "  - text: We use cookies." in lines


def test_elements_absent_from_the_previous_snapshot_are_marked_new():
    first = render([{"role": "button", "name": "Open modal", "ref": "e1"}])
    second = render(
        [
            {"role": "button", "name": "Open modal", "ref": "e1"},
            {"role": "button", "name": "Close modal", "ref": "e2"},
        ],
        previous=first.signatures,
    )
    assert '- button "Open modal" [ref=e1]' in second.text
    assert '- button "Close modal" [ref=e2] [new]' in second.text


def test_nothing_is_marked_new_without_a_previous_snapshot():
    out = render([{"role": "button", "name": "Open modal", "ref": "e1"}])
    assert "[new]" not in out.text


def test_repeated_identical_elements_only_mark_the_surplus_as_new():
    first = render([{"role": "link", "name": "Next", "ref": "e1"}])
    second = render(
        [
            {"role": "link", "name": "Next", "ref": "e1"},
            {"role": "link", "name": "Next", "ref": "e2"},
        ],
        previous=first.signatures,
    )
    assert second.text.count("[new]") == 1


def test_snapshot_is_truncated_at_the_token_budget_with_a_trailer():
    many = [{"role": "link", "name": f"Result number {i}", "ref": f"e{i}"} for i in range(500)]
    out = render(many, budget=200)
    assert out.truncated is True
    assert "truncated" in out.text.splitlines()[-1]
    assert len(out.text) // 4 <= 260


def test_an_untruncated_snapshot_says_so():
    out = render([{"role": "link", "name": "Home", "ref": "e1"}], budget=8000)
    assert out.truncated is False
    assert "truncated" not in out.text


def test_signatures_ignore_ref_identity():
    first = render([{"role": "button", "name": "Send", "ref": "e1"}])
    second = render([{"role": "button", "name": "Send", "ref": "e9"}], previous=first.signatures)
    assert "[new]" not in second.text
    assert first.signatures == Counter({"button|Send": 1})


def test_only_referenced_elements_are_tracked_for_newness():
    out = render([{"role": "paragraph", "text": "hello"}])
    assert out.signatures == Counter()
