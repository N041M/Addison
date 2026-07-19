"""Widget spec validation — the SAVE-time / RENDER-time gate (agent_core/widgets.py).

A widget is a DECLARATIVE spec, one of exactly two shapes, NEVER code. These
tests pin that: both valid kinds accept; unknown kinds/sources reject; code-
looking ids reject; over-long titles and extra fields reject; the pinned cap is a
constant the server enforces.
"""

from __future__ import annotations

from agent_core.policy import PolicyMode
from agent_core.widgets import (
    MAX_PINNED,
    MAX_TITLE_LEN,
    STAT_SOURCES,
    validate_widget_spec,
    widget_summary,
)


def test_valid_routine_widget_accepts():
    spec = {"kind": "routine", "routineId": "1b9d6bcd-bbfd-4b2d-9b5d-ab8dfbbd4bed", "title": "Weather note"}
    assert validate_widget_spec(spec) is None


def test_valid_stat_widget_accepts_each_whitelisted_source():
    for source in STAT_SOURCES:
        spec = {"kind": "stat", "source": source, "title": "A stat"}
        assert validate_widget_spec(spec) is None, source


def test_unknown_kind_rejects():
    assert validate_widget_spec({"kind": "agent", "title": "x"}) is not None
    assert validate_widget_spec({"kind": "command", "title": "x", "cmd": "rm -rf /"}) is not None


def test_unknown_stat_source_rejects():
    assert validate_widget_spec({"kind": "stat", "source": "disk_space", "title": "x"}) is not None
    # A code-looking source fails the whitelist equality check.
    assert validate_widget_spec({"kind": "stat", "source": "eval(1)", "title": "x"}) is not None


def test_code_looking_routine_id_rejects():
    for bad in ("eval(1)", "${danger}", "a; rm -rf /", "a b", "os.system('x')", "`x`", "{x}"):
        spec = {"kind": "routine", "routineId": bad, "title": "x"}
        assert validate_widget_spec(spec) is not None, bad


def test_missing_or_blank_title_rejects():
    assert validate_widget_spec({"kind": "stat", "source": "connections"}) is not None
    assert validate_widget_spec({"kind": "stat", "source": "connections", "title": "  "}) is not None


def test_over_long_title_rejects():
    spec = {"kind": "stat", "source": "connections", "title": "x" * (MAX_TITLE_LEN + 1)}
    assert validate_widget_spec(spec) is not None
    spec_ok = {"kind": "stat", "source": "connections", "title": "x" * MAX_TITLE_LEN}
    assert validate_widget_spec(spec_ok) is None


def test_extra_fields_reject():
    # No smuggling an extra field (e.g. an "action"/"code" key) past the schema.
    assert validate_widget_spec(
        {"kind": "stat", "source": "connections", "title": "x", "action": "run"}
    ) is not None
    assert validate_widget_spec(
        {"kind": "routine", "routineId": "abc", "title": "x", "code": "eval"}
    ) is not None


def test_non_dict_rejects():
    assert validate_widget_spec("not a dict") is not None
    assert validate_widget_spec(None) is not None
    assert validate_widget_spec(["kind", "stat"]) is not None


def test_pinned_cap_is_six():
    assert MAX_PINNED == 6


def test_widget_summary_is_plain_language():
    assert widget_summary({"kind": "routine", "routineId": "a", "title": "x"})
    assert "token" in widget_summary({"kind": "stat", "source": "tokens_month", "title": "x"}).lower()


# --- command widget kind: OPEN-mode only (owner decision 2026-07-19) ---------

def test_command_widget_rejected_in_safe_mode():
    spec = {"kind": "command", "command": "ls -la", "title": "List files"}
    # Default mode is SAFE, and SAFE mode never accepts a command widget.
    assert validate_widget_spec(spec) is not None
    assert validate_widget_spec(spec, PolicyMode.SAFE) is not None


def test_command_widget_accepts_in_open_mode():
    spec = {"kind": "command", "command": "ls -la", "title": "List files"}
    assert validate_widget_spec(spec, PolicyMode.OPEN) is None


def test_command_widget_needs_a_command_and_no_extra_fields_even_in_open_mode():
    assert validate_widget_spec({"kind": "command", "title": "x"}, PolicyMode.OPEN) is not None
    assert validate_widget_spec(
        {"kind": "command", "command": "  ", "title": "x"}, PolicyMode.OPEN
    ) is not None
    assert validate_widget_spec(
        {"kind": "command", "command": "ls", "title": "x", "shell": "bash"}, PolicyMode.OPEN
    ) is not None


def test_stat_and_routine_widgets_still_valid_in_open_mode():
    # OPEN mode is a superset — the two SAFE shapes remain valid.
    assert validate_widget_spec(
        {"kind": "stat", "source": "connections", "title": "x"}, PolicyMode.OPEN
    ) is None
    assert validate_widget_spec(
        {"kind": "routine", "routineId": "abc", "title": "x"}, PolicyMode.OPEN
    ) is None


def test_command_widget_summary_is_plain_language():
    assert "command" in widget_summary(
        {"kind": "command", "command": "ls", "title": "x"}
    ).lower()
