"""Unit tests for the fanout sentinel grammar (9b). All offline — no pi."""
from __future__ import annotations

import pytest
from relay_v2.harness.signaling import MarkerError
from relay_v2.harness.signaling.fanout import FanoutParseError, FanoutPayload
from relay_v2.harness.signaling.sentinels import (
    count_closing_sentinels,
    extract_fanout_payload,
)


def test_fanout_payload_valid() -> None:
    p = FanoutPayload.model_validate({
        "children": [
            {"role": "explorer-a", "prompt": "Do A."},
            {"role": "explorer-b", "prompt": "Do B."},
        ],
        "join_prompt": "Synthesize A and B.",
    })
    assert len(p.children) == 2
    assert p.children[0].role == "explorer-a"
    assert p.join_prompt == "Synthesize A and B."


def test_fanout_payload_empty_children_raises() -> None:
    with pytest.raises(Exception):
        FanoutPayload.model_validate({"children": [], "join_prompt": "x"})


def test_fanout_payload_missing_join_prompt_raises() -> None:
    with pytest.raises(Exception):
        FanoutPayload.model_validate({
            "children": [{"role": "r", "prompt": "p"}],
        })


def test_count_fanout_sentinel() -> None:
    counts = count_closing_sentinels("Some work.\n\n[[engteam:fanout]]\n")
    assert counts["fanout"] == 1 and counts["done"] == 0


def test_count_fanout_not_at_column_zero_ignored() -> None:
    assert count_closing_sentinels("    [[engteam:fanout]]\n")["fanout"] == 0


def test_count_existing_sentinels_unaffected() -> None:
    counts = count_closing_sentinels("All done.\n\n[[engteam:done]]")
    assert counts["done"] == 1 and counts["fanout"] == 0


FANOUT_BLOCK = (
    "Dispatching parallel exploration.\n\n"
    "[[engteam:fanout-start]]\n"
    '{"children": [{"role": "explorer-a", "prompt": "Do A."}, '
    '{"role": "explorer-b", "prompt": "Do B."}], "join_prompt": "Merge."}\n'
    "[[engteam:fanout-end]]\n\n"
    "[[engteam:fanout]]"
)


def test_extract_fanout_payload_valid() -> None:
    payload = extract_fanout_payload(FANOUT_BLOCK)
    assert len(payload.children) == 2
    assert payload.children[0].role == "explorer-a"
    assert payload.children[1].prompt == "Do B."
    assert payload.join_prompt == "Merge."


def test_extract_fanout_payload_no_end_marker_raises_marker_error() -> None:
    with pytest.raises(MarkerError):
        extract_fanout_payload(
            "[[engteam:fanout-start]]\n{}\n[[engteam:fanout]]"
        )


def test_extract_fanout_payload_no_start_marker_raises_marker_error() -> None:
    with pytest.raises(MarkerError):
        extract_fanout_payload(
            '{"x": 1}\n[[engteam:fanout-end]]\n\n[[engteam:fanout]]'
        )


def test_extract_fanout_payload_bad_json_raises_parse_error() -> None:
    with pytest.raises(FanoutParseError):
        extract_fanout_payload(
            "[[engteam:fanout-start]]\n{not valid}\n"
            "[[engteam:fanout-end]]\n\n[[engteam:fanout]]"
        )


def test_extract_fanout_payload_empty_children_raises_parse_error() -> None:
    with pytest.raises(FanoutParseError):
        extract_fanout_payload(
            "[[engteam:fanout-start]]\n"
            '{"children": [], "join_prompt": "x"}\n'
            "[[engteam:fanout-end]]\n\n[[engteam:fanout]]"
        )


def test_extract_fanout_payload_multiline_json() -> None:
    text = (
        "[[engteam:fanout-start]]\n"
        "{\n"
        '  "children": [{"role": "r", "prompt": "p"}],\n'
        '  "join_prompt": "j"\n'
        "}\n"
        "[[engteam:fanout-end]]\n\n"
        "[[engteam:fanout]]"
    )
    assert extract_fanout_payload(text).children[0].role == "r"
