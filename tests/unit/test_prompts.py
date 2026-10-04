from datetime import datetime, timedelta, timezone

from app.agent.prompts import build_messages


def test_the_system_prompt_states_today_so_relative_dates_can_be_worked_out():
    now = datetime(2026, 10, 4, 9, 30, tzinfo=timezone(timedelta(hours=4), "GST"))

    system = build_messages("find a flight in 3 days", None, now=now)[0]["content"]

    assert "Today is Sunday 04 October 2026" in system
    assert "09:30" in system
