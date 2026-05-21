"""Unit tests for the fanout sentinel grammar (9b). All offline — no pi."""
from __future__ import annotations

import pytest
from relay_v2.harness.signaling.fanout import FanoutParseError, FanoutPayload


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
