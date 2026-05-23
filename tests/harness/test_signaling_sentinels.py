"""W3 — text_sentinels parser, ported from v1's tests/test-parsing.sh.

Every case below mirrors a v1 synthetic fixture (case ids c1..c13 plus
the marker-contract positive/negative, repair-recipe, and phase-start
groups). v1 ran these against awk/jq through a ``jq`` filter that first
stripped tool-input content; in v2 that isolation happens in the harness
(only ``AssistantText`` with ``kind=="text"`` is fed here), so the
ported cases operate on the already-isolated assistant text.
"""

from __future__ import annotations

import pytest

from relay_v2.harness.protocol import SignalConfig
from relay_v2.harness.signaling import detect_in_text
from relay_v2.harness.signaling.sentinels import (
    MarkerError,
    count_closing_sentinels,
    extract_handoff_prompt,
    extract_pause_id,
    extract_pause_prompt,
    extract_pause_question,
    extract_pause_review_path,
    extract_pause_review_paths,
    extract_phase_start,
    validate_done_no_prompt_markers,
)

SENTINELS = SignalConfig(strategy="text_sentinels")

# --- c1..c13: counting + extraction -------------------------------------

C1 = "Phase 1: Evaluation\nAll units shipped. Final state green.\n[[engteam:done]]"

C2 = (
    "W1 done. Recommendation: fresh session for W2.\n"
    "Here is the next-session prompt:\n"
    "\n"
    "[[engteam:prompt-start]]\n"
    "Continue plan eng-team-demo. Next unit: W2 (parser).\n"
    "\n"
    "State: main is at abc123, 16 tests green.\n"
    "[[engteam:prompt-end]]\n"
    "\n"
    "[[engteam:handoff]]"
)
C2_BODY = (
    "Continue plan eng-team-demo. Next unit: W2 (parser).\n"
    "\n"
    "State: main is at abc123, 16 tests green."
)

# c3: tool inputs are already stripped by the harness — only the
# assistant text reaches the parser. The literal sentinels that lived in
# Bash/cat tool inputs simply are not present here.
C3 = "Running a sanity check.\nAll checks pass.\n[[engteam:done]]"

C4 = (
    "Earlier in the run there was a code sample:\n"
    "\n"
    "```\n"
    "this is NOT the next prompt\n"
    "```\n"
    "\n"
    "Then later, the handoff:\n"
    "\n"
    "[[engteam:prompt-start]]\n"
    "THIS IS the next prompt\n"
    "spanning multiple lines\n"
    "[[engteam:prompt-end]]\n"
    "\n"
    "[[engteam:handoff]]"
)
C4_BODY = "THIS IS the next prompt\nspanning multiple lines"

C5 = "Did some work. Forgot to emit a sentinel."

C6 = (
    "Recommend handoff but also might be done.\n"
    "\n"
    "[[engteam:prompt-start]]\n"
    "next prompt\n"
    "[[engteam:prompt-end]]\n"
    "\n"
    "[[engteam:handoff]]\n"
    "[[engteam:done]]"
)

C7 = "[[engteam:done]]   "

C8 = "Phase done. Marker: [[engteam:done]] (mentioned inline)."

C9 = (
    "During W3 I hit a design fork: should the cache be in-memory or use Redis?\n"
    "\n"
    "Here is the next-session prompt:\n"
    "\n"
    "[[engteam:prompt-start]]\n"
    "Resume W3 (cache implementation). Worktree is at .claude/worktrees/eng-cache.\n"
    "Apply the user's choice (memory vs Redis) and continue.\n"
    "[[engteam:prompt-end]]\n"
    "\n"
    '[[engteam:pause-for-input id="P1" '
    'question="Use in-memory cache or Redis for W3?"]]'
)
C9_BODY = (
    "Resume W3 (cache implementation). Worktree is at .claude/worktrees/eng-cache.\n"
    "Apply the user's choice (memory vs Redis) and continue."
)

C10 = (
    "Quick question with no marker pair:\n"
    "\n"
    '[[engteam:pause-for-input id="P2" question="Should we proceed?"]]'
)

C11 = (
    "[[engteam:prompt-start]]\n"
    "resume here\n"
    "[[engteam:prompt-end]]\n"
    "\n"
    '[[engteam:pause-for-input id="P3" '
    'question="Use the \\"new\\" API or the legacy one?"]]'
)

C12 = (
    "[[engteam:prompt-start]]\n"
    "prompt for handoff\n"
    "[[engteam:prompt-end]]\n"
    "\n"
    "[[engteam:handoff]]\n"
    "\n"
    "[[engteam:prompt-start]]\n"
    "prompt for pause\n"
    "[[engteam:prompt-end]]\n"
    "\n"
    '[[engteam:pause-for-input id="P4" question="why both?"]]'
)

C13 = (
    "Phase 3 in progress.\n"
    "\n"
    '[[engteam:unit-start id="W1" title="Fix factorial off-by-one"]]\n'
    '[[engteam:unit-done id="W1" title="Fix factorial off-by-one"]]\n'
    "\n"
    '[[engteam:unit-start id="W2" title="Add type annotations"]]\n'
    '[[engteam:unit-done id="W2" title="Add type annotations"]]\n'
    "\n"
    "Reached W3, which has a public-API fork. Pausing.\n"
    "\n"
    "[[engteam:prompt-start]]\n"
    "Resume W3 (divide). Apply the user's answer and continue with W4-W6.\n"
    "State: W1+W2 are uncommitted in the worktree.\n"
    "[[engteam:prompt-end]]\n"
    "\n"
    '[[engteam:pause-for-input id="P1" '
    'question="ZeroDivisionError or ValueError for divide(x, 0)?"]]'
)
C13_BODY = (
    "Resume W3 (divide). Apply the user's answer and continue with W4-W6.\n"
    "State: W1+W2 are uncommitted in the worktree."
)


@pytest.mark.parametrize(
    "name,text,done,handoff,pause",
    [
        ("c1-clean-done", C1, 1, 0, 0),
        ("c2-clean-handoff", C2, 0, 1, 0),
        ("c3-tool-input-isolation", C3, 1, 0, 0),
        ("c4-markers-not-earlier-fence", C4, 0, 1, 0),
        ("c5-no-sentinel", C5, 0, 0, 0),
        ("c6-both-sentinels", C6, 1, 1, 0),
        ("c7-trailing-whitespace", C7, 1, 0, 0),
        ("c8-inline-mention", C8, 0, 0, 0),
        ("c9-clean-pause", C9, 0, 0, 1),
        ("c12-pause-plus-handoff", C12, 0, 1, 1),
        ("c13-pause-after-unit-start", C13, 0, 0, 1),
    ],
)
def test_closing_sentinel_counts(
    name: str, text: str, done: int, handoff: int, pause: int
) -> None:
    counts = count_closing_sentinels(text)
    assert counts == {
        "done": done,
        "handoff": handoff,
        "pause": pause,
        "fanout": 0,
    }


def test_c2_handoff_prompt_body() -> None:
    assert extract_handoff_prompt(C2) == C2_BODY


def test_c4_markers_not_earlier_fence() -> None:
    assert extract_handoff_prompt(C4) == C4_BODY


def test_c9_clean_pause_extraction() -> None:
    assert extract_pause_prompt(C9) == C9_BODY
    assert extract_pause_question(C9) == "Use in-memory cache or Redis for W3?"
    assert extract_pause_id(C9) == "P1"


def test_c10_pause_without_markers_errors() -> None:
    with pytest.raises(MarkerError) as ei:
        extract_pause_prompt(C10)
    assert "no [[engteam:prompt-end]] preceding" in str(ei.value)


def test_c11_pause_question_unescaped() -> None:
    assert extract_pause_question(C11) == 'Use the "new" API or the legacy one?'


def test_c13_pause_prompt_survives_earlier_unit_sentinels() -> None:
    # v1 regression: awk -v bracket-escape mangling matched earlier
    # unit-start lines and yielded an empty prompt. Port guards it.
    assert extract_pause_prompt(C13) == C13_BODY


# --- marker contract: positive ------------------------------------------

MP_SINGLE = (
    '[[engteam:phase-start phase="planning"]]\n'
    "some narrative\n"
    "[[engteam:prompt-start]]\n"
    "Implement W1 next iter.\n"
    "[[engteam:prompt-end]]\n"
    "[[engteam:handoff]]"
)
MP_MULTI = (
    '[[engteam:phase-start phase="planning"]]\n'
    "[[engteam:prompt-start]]\n"
    "First paragraph.\n"
    "\n"
    "Second paragraph.\n"
    "[[engteam:prompt-end]]\n"
    "[[engteam:handoff]]"
)
MP_INNER_FENCE = (
    '[[engteam:phase-start phase="development"]]\n'
    "[[engteam:prompt-start]]\n"
    "Run these commands next iter:\n"
    "\n"
    "```bash\n"
    "cd /tmp/x\n"
    "ls\n"
    "```\n"
    "\n"
    "Then commit.\n"
    "[[engteam:prompt-end]]\n"
    "[[engteam:handoff]]"
)
MP_INNER_FENCE_BODY = (
    "Run these commands next iter:\n\n```bash\ncd /tmp/x\nls\n```\n\nThen commit."
)
MP_NESTED_FENCE = (
    '[[engteam:phase-start phase="wrap-up"]]\n'
    "[[engteam:prompt-start]]\n"
    "Phase 4 wrap-up. Run:\n"
    "\n"
    "```\n"
    "outer\n"
    "```bash\n"
    "inner\n"
    "```\n"
    "```\n"
    "\n"
    "Done.\n"
    "[[engteam:prompt-end]]\n"
    "[[engteam:handoff]]"
)
MP_NESTED_FENCE_BODY = (
    "Phase 4 wrap-up. Run:\n\n```\nouter\n```bash\ninner\n```\n```\n\nDone."
)
MP_BLANKS = (
    "[[engteam:prompt-start]]\n"
    "Body.\n"
    "[[engteam:prompt-end]]\n"
    "\n"
    "[[engteam:handoff]]"
)
MP_PAUSE = (
    '[[engteam:phase-start phase="planning"]]\n'
    "[[engteam:prompt-start]]\n"
    "Resume content here.\n"
    "[[engteam:prompt-end]]\n"
    '[[engteam:pause-for-input question="confirm?" id="q1"]]'
)


@pytest.mark.parametrize(
    "text,expected",
    [
        (MP_SINGLE, "Implement W1 next iter."),
        (MP_MULTI, "First paragraph.\n\nSecond paragraph."),
        (MP_INNER_FENCE, MP_INNER_FENCE_BODY),
        (MP_NESTED_FENCE, MP_NESTED_FENCE_BODY),
        (MP_BLANKS, "Body."),
    ],
)
def test_marker_positive_handoff(text: str, expected: str) -> None:
    assert extract_handoff_prompt(text) == expected


def test_marker_positive_pause() -> None:
    assert extract_pause_prompt(MP_PAUSE) == "Resume content here."


def test_done_with_no_markers_passes_validation() -> None:
    text = '[[engteam:phase-start phase="wrap-up"]]\nAll units done.\n[[engteam:done]]'
    validate_done_no_prompt_markers(text)  # must not raise


# --- marker contract: negative ------------------------------------------

NEG_NO_MARKERS = (
    '[[engteam:phase-start phase="planning"]]\nnarrative\n[[engteam:handoff]]'
)
NEG_END_NO_START = (
    '[[engteam:phase-start phase="planning"]]\n'
    "Body without an opener.\n"
    "[[engteam:prompt-end]]\n"
    "[[engteam:handoff]]"
)
NEG_START_NO_END = (
    '[[engteam:phase-start phase="planning"]]\n'
    "[[engteam:prompt-start]]\n"
    "Body that never closes.\n"
    "[[engteam:handoff]]"
)
NEG_TWO_END = (
    '[[engteam:phase-start phase="planning"]]\n'
    "[[engteam:prompt-start]]\n"
    "First close.\n"
    "[[engteam:prompt-end]]\n"
    "Stray content.\n"
    "[[engteam:prompt-end]]\n"
    "[[engteam:handoff]]"
)
NEG_CONTENT_BETWEEN = (
    '[[engteam:phase-start phase="planning"]]\n'
    "[[engteam:prompt-start]]\n"
    "ok\n"
    "[[engteam:prompt-end]]\n"
    "stray paragraph here.\n"
    "[[engteam:handoff]]"
)


@pytest.mark.parametrize(
    "text,needle",
    [
        (NEG_NO_MARKERS, "no [[engteam:prompt-end]] preceding"),
        (NEG_END_NO_START, "without matching [[engteam:prompt-start]]"),
        (NEG_START_NO_END, "no [[engteam:prompt-end]] preceding"),
        (NEG_TWO_END, "content between [[engteam:prompt-end]] and closing sentinel"),
        (
            NEG_CONTENT_BETWEEN,
            "content between [[engteam:prompt-end]] and closing sentinel",
        ),
    ],
)
def test_marker_negative_handoff(text: str, needle: str) -> None:
    with pytest.raises(MarkerError) as ei:
        extract_handoff_prompt(text)
    assert needle in str(ei.value)


def test_done_with_markers_rejected() -> None:
    text = (
        '[[engteam:phase-start phase="wrap-up"]]\n'
        "[[engteam:prompt-start]]\n"
        "Shouldnt be here.\n"
        "[[engteam:prompt-end]]\n"
        "[[engteam:done]]"
    )
    with pytest.raises(MarkerError) as ei:
        validate_done_no_prompt_markers(text)
    assert "cannot have prompt markers" in str(ei.value)


# --- repair-recipe text (regression anchor for incidents 1-3) -----------

REPAIR_HANDOFF = (
    '[[engteam:phase-start phase="planning"]]\n'
    "Plan saved. Three work units.\n"
    "\n"
    "```\n"
    "Phase 3 development next iter. Read the plan and start W1.\n"
    "```\n"
    "\n"
    "[[engteam:handoff]]"
)
REPAIR_PAUSE = (
    '[[engteam:phase-start phase="planning"]]\n'
    "Plan saved.\n"
    "\n"
    "```\n"
    "Resume with the plan in hand.\n"
    "```\n"
    "\n"
    '[[engteam:pause-for-input id="P1" question="approve plan?"]]'
)


def test_repair_recipe_handoff_incident() -> None:
    with pytest.raises(MarkerError) as ei:
        extract_handoff_prompt(REPAIR_HANDOFF)
    assert "pre-2026-05-17 convention" in str(ei.value)


def test_repair_recipe_pause_incident() -> None:
    with pytest.raises(MarkerError) as ei:
        extract_pause_prompt(REPAIR_PAUSE)
    assert "pre-2026-05-17 convention" in str(ei.value)


def test_repair_recipe_done_incident() -> None:
    text = (
        "[[engteam:prompt-start]]\n"
        "something\n"
        "[[engteam:prompt-end]]\n"
        "[[engteam:done]]"
    )
    with pytest.raises(MarkerError) as ei:
        validate_done_no_prompt_markers(text)
    assert "takes no prompt body" in str(ei.value)


# --- phase-start --------------------------------------------------------


def test_phase_start_single_emission() -> None:
    text = (
        '[[engteam:phase-start phase="evaluation"]]\n'
        "Doing eval work.\n"
        "\n"
        "[[engteam:prompt-start]]\n"
        "Next iter prompt body.\n"
        "[[engteam:prompt-end]]\n"
        "\n"
        "[[engteam:handoff]]"
    )
    assert extract_phase_start(text) == "evaluation"


def test_phase_start_last_value_wins() -> None:
    text = (
        '[[engteam:phase-start phase="evaluation"]]\n'
        "eval work\n"
        '[[engteam:phase-start phase="planning"]]\n'
        "plan work"
    )
    assert extract_phase_start(text) == "planning"


def test_phase_start_absent() -> None:
    assert extract_phase_start("No phase sentinel here.\nplain text") == ""


def test_phase_start_indented_does_not_match() -> None:
    # Leading whitespace breaks the column-0 anchor.
    assert extract_phase_start('  [[engteam:phase-start phase="evaluation"]]') == ""


# --- detect_in_text integration -----------------------------------------


def test_detect_terminal_signals() -> None:
    assert detect_in_text(C1, SENTINELS).kind == "done"  # type: ignore[union-attr]
    h = detect_in_text(C2, SENTINELS)
    assert h is not None and h.kind == "handoff" and h.args["next_prompt"] == C2_BODY
    p = detect_in_text(C9, SENTINELS)
    assert p is not None and p.kind == "pause"
    assert p.args["question"] == "Use in-memory cache or Redis for W3?"
    assert p.args["id"] == "P1" and p.args["next_prompt"] == C9_BODY


def test_detect_non_closing_signals() -> None:
    assert detect_in_text(C5, SENTINELS) is None
    ph = detect_in_text('[[engteam:phase-start phase="development"]]', SENTINELS)
    assert ph is not None and ph.kind == "phase_start"
    assert ph.args == {"phase": "development"}
    ud = detect_in_text(
        '[[engteam:unit-done id="W2" title="Add type annotations"]]', SENTINELS
    )
    assert ud is not None and ud.kind == "unit_done" and ud.args["id"] == "W2"


def test_detect_done_with_markers_propagates_marker_error() -> None:
    bad = (
        "[[engteam:prompt-start]]\nx\n[[engteam:prompt-end]]\n[[engteam:done]]"
    )
    with pytest.raises(MarkerError):
        detect_in_text(bad, SENTINELS)


def test_detect_mcp_strategy_is_inert_here() -> None:
    assert detect_in_text(C1, SignalConfig(strategy="mcp_tools")) is None


# --- W7: detect_in_text gaps (unit_start / unit_abandoned / dual close) ---


def test_detect_unit_start_only() -> None:
    s = detect_in_text(
        '[[engteam:unit-start id="W3" title="Wire coverage"]]', SENTINELS
    )
    assert s is not None and s.kind == "unit_start"
    assert s.args["id"] == "W3" and s.args["title"] == "Wire coverage"


def test_detect_unit_abandoned_only() -> None:
    s = detect_in_text(
        '[[engteam:unit-abandoned id="W4" reason="blocked on pin"]]',
        SENTINELS,
    )
    assert s is not None and s.kind == "unit_abandoned"
    assert s.args["id"] == "W4" and s.args["reason"] == "blocked on pin"


def test_detect_dual_closing_done_wins() -> None:
    """handoff + done at column 0, no markers: done is checked first, so
    a marker-free dual close resolves to done (handoff never evaluated)."""
    dual = "Wrapping up.\n\n[[engteam:handoff]]\n[[engteam:done]]"
    s = detect_in_text(dual, SENTINELS)
    assert s is not None and s.kind == "done"


def test_detect_done_with_markers_is_violation() -> None:
    """C6 carries a prompt-marker pair *and* done — done-with-markers is
    a contract violation, surfaced as MarkerError, not a silent done."""
    with pytest.raises(MarkerError):
        detect_in_text(C6, SENTINELS)


# --- 14b: pause-for-input gains optional review_path attribute (ADR-40) ---

_RP_BLOCK_NO_RP = (
    "Plan saved.\n\n"
    "[[engteam:prompt-start]]\n"
    "Re-read $RELAY_RUN_DIR/improvement-plan.md and continue.\n"
    "[[engteam:prompt-end]]\n"
    '[[engteam:pause-for-input id="P1" question="Approve plan?"]]'
)


def _rp_block(review_attr: str) -> str:
    """Build a well-formed pause block with the given review_path attribute
    spelling appended to the sentinel line."""
    return (
        "Plan saved.\n\n"
        "[[engteam:prompt-start]]\n"
        "Re-read $RELAY_RUN_DIR/improvement-plan.md and continue.\n"
        "[[engteam:prompt-end]]\n"
        f'[[engteam:pause-for-input id="P1" question="Approve plan?" {review_attr}]]'
    )


def test_extract_pause_review_path_absent() -> None:
    assert extract_pause_review_path(_RP_BLOCK_NO_RP) is None


def test_extract_pause_review_path_present() -> None:
    text = _rp_block('review_path="improvement-plan.md"')
    assert extract_pause_review_path(text) == "improvement-plan.md"


def test_extract_pause_review_path_subdir() -> None:
    # Parser does not normalise; subdir paths are returned verbatim.
    text = _rp_block('review_path="discussions/notes.md"')
    assert extract_pause_review_path(text) == "discussions/notes.md"


def test_extract_pause_review_path_unescapes_quotes() -> None:
    # Mirrors extract_pause_question's \" unescape rule.
    text = _rp_block(r'review_path="a\"b.md"')
    assert extract_pause_review_path(text) == 'a"b.md'


def test_extract_pause_review_path_rejects_empty() -> None:
    text = _rp_block('review_path=""')
    with pytest.raises(MarkerError) as ei:
        extract_pause_review_path(text)
    assert ei.value.headline.startswith("extract_pause_review_path:")
    assert "empty" in ei.value.headline


def test_extract_pause_review_path_rejects_absolute() -> None:
    text = _rp_block('review_path="/etc/passwd"')
    with pytest.raises(MarkerError) as ei:
        extract_pause_review_path(text)
    assert "absolute" in ei.value.headline


def test_extract_pause_review_path_rejects_traversal() -> None:
    text = _rp_block('review_path="../escape.md"')
    with pytest.raises(MarkerError) as ei:
        extract_pause_review_path(text)
    assert "'..'" in ei.value.headline


def test_extract_pause_review_path_rejects_traversal_nested() -> None:
    text = _rp_block('review_path="a/../b.md"')
    with pytest.raises(MarkerError) as ei:
        extract_pause_review_path(text)
    assert "'..'" in ei.value.headline


def test_extract_pause_review_path_rejects_nul() -> None:
    text = _rp_block('review_path="a\x00b"')
    with pytest.raises(MarkerError) as ei:
        extract_pause_review_path(text)
    assert "NUL" in ei.value.headline


def test_detect_in_text_pause_includes_review_paths_when_present() -> None:
    # 14f / ADR-41: detect_in_text writes the plural key, NOT the
    # singular legacy key (load-bearing for write_artifact's
    # no_review_path 409 branch when the attribute is absent).
    text = _rp_block('review_path="improvement-plan.md"')
    s = detect_in_text(text, SENTINELS)
    assert s is not None and s.kind == "pause"
    assert s.args["review_paths"] == ["improvement-plan.md"]
    assert "review_path" not in s.args
    # The pre-14b keys are still there unchanged.
    assert s.args["id"] == "P1"
    assert s.args["question"] == "Approve plan?"
    assert "Re-read $RELAY_RUN_DIR/improvement-plan.md" in s.args["next_prompt"]


def test_detect_in_text_pause_omits_review_paths_when_absent() -> None:
    # Backwards-compat regression: skills not on the 14b grammar produce
    # signal_args with NEITHER ``review_paths`` NOR ``review_path``
    # (absent, not None — load-bearing).
    s = detect_in_text(_RP_BLOCK_NO_RP, SENTINELS)
    assert s is not None and s.kind == "pause"
    assert "review_paths" not in s.args
    assert "review_path" not in s.args


# --- 14f / ADR-41: plural review_paths via repeated attribute ----------


def test_extract_pause_review_paths_absent() -> None:
    assert extract_pause_review_paths(_RP_BLOCK_NO_RP) == []


def test_extract_pause_review_paths_single() -> None:
    text = _rp_block('review_path="improvement-plan.md"')
    assert extract_pause_review_paths(text) == ["improvement-plan.md"]


def test_extract_pause_review_paths_two() -> None:
    text = _rp_block(
        'review_path="frontend-audit.md" review_path="backend-audit.md"'
    )
    assert extract_pause_review_paths(text) == [
        "frontend-audit.md",
        "backend-audit.md",
    ]


def test_extract_pause_review_paths_three() -> None:
    text = _rp_block(
        'review_path="a.md" review_path="b.md" review_path="c.md"'
    )
    assert extract_pause_review_paths(text) == ["a.md", "b.md", "c.md"]


def test_extract_pause_review_paths_validates_each_value() -> None:
    # An invalid value anywhere in the list raises, naming the
    # offending value. Order-independence: the bad one is second.
    text = _rp_block('review_path="ok.md" review_path="/abs.md"')
    with pytest.raises(MarkerError) as ei:
        extract_pause_review_paths(text)
    assert "/abs.md" in ei.value.headline
    assert "absolute" in ei.value.headline


def test_extract_pause_review_paths_validates_first_traversal() -> None:
    text = _rp_block('review_path="../bad.md" review_path="ok.md"')
    with pytest.raises(MarkerError) as ei:
        extract_pause_review_paths(text)
    assert "'..'" in ei.value.headline
    assert "../bad.md" in ei.value.headline


def test_extract_pause_review_paths_order_agnostic_with_id_question() -> None:
    # Mixed attribute order: id/review_path/question/review_path. The
    # parser collects all review_path values regardless of position.
    line = (
        '[[engteam:pause-for-input id="P1" review_path="a.md" '
        'question="Approve both?" review_path="b.md"]]'
    )
    text = (
        "Plan saved.\n\n"
        "[[engteam:prompt-start]]\n"
        "Body.\n"
        "[[engteam:prompt-end]]\n"
        f"{line}"
    )
    assert extract_pause_review_paths(text) == ["a.md", "b.md"]


def test_extract_pause_review_path_shim_returns_first() -> None:
    # The 14b single-value extractor stays as a back-compat shim
    # returning the first value (or None when absent).
    text = _rp_block('review_path="a.md" review_path="b.md"')
    assert extract_pause_review_path(text) == "a.md"
    assert extract_pause_review_path(_RP_BLOCK_NO_RP) is None


def test_detect_in_text_pause_with_two_review_paths() -> None:
    text = _rp_block('review_path="a.md" review_path="b.md"')
    s = detect_in_text(text, SENTINELS)
    assert s is not None and s.kind == "pause"
    assert s.args["review_paths"] == ["a.md", "b.md"]
    assert "review_path" not in s.args
