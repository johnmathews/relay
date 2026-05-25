"""Unit tests for the fanout sentinel grammar (9b). All offline — no pi."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from relay_v2.harness.protocol import SignalConfig
from relay_v2.harness.signaling import MarkerError
from relay_v2.harness.signaling.fanout import FanoutParseError, FanoutPayload
from relay_v2.harness.signaling.sentinels import (
    count_closing_sentinels,
    detect_in_text,
    extract_fanout_payload,
)

_CFG = SignalConfig(strategy="text_sentinels")


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
    with pytest.raises(ValidationError):
        FanoutPayload.model_validate({"children": [], "join_prompt": "x"})


def test_fanout_payload_too_many_children_raises() -> None:
    """Hard cap (32) is parser-enforced regardless of config."""
    too_many = [{"role": f"r{i}", "prompt": "p"} for i in range(33)]
    with pytest.raises(ValidationError, match="hard cap"):
        FanoutPayload.model_validate({
            "children": too_many,
            "join_prompt": "x",
        })


def test_fanout_payload_missing_join_prompt_raises() -> None:
    with pytest.raises(ValidationError):
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


def test_detect_in_text_fanout_returns_fanout_signal() -> None:
    sig = detect_in_text(FANOUT_BLOCK, _CFG)
    assert sig is not None
    assert sig.kind == "fanout"
    assert sig.args["payload"]["join_prompt"] == "Merge."
    assert sig.args["payload"]["children"][0]["role"] == "explorer-a"


def test_detect_in_text_fanout_beats_unit_done() -> None:
    """fanout in same text as unit_done: fanout wins (terminal beats non-terminal)."""
    text = FANOUT_BLOCK + '\n\n[[engteam:unit-done id="u1" title="s"]]\n'
    sig = detect_in_text(text, _CFG)
    assert sig is not None and sig.kind == "fanout"


def test_detect_in_text_no_fanout_sentinel_returns_none() -> None:
    assert detect_in_text("Ordinary text.", _CFG) is None
