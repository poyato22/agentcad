from mises.claude_code.session import RECOVERABLE, classify_claude_error


def test_safeguard_flag_is_classified_and_recoverable():
    msg = ("API Error: Sonnet 5.5's safeguards flagged this message "
           "(https://www.anthropic.com/legal/aup). Details: `[reasoning_extraction]`")
    c = classify_claude_error(msg)
    assert c["kind"] == "safeguard" and "Opus" in c["hint"]
    assert "safeguard" in RECOVERABLE


def test_max_loop_no_longer_asks_for_rejected_alternatives():
    from mises.effort import MAX_LOOP
    assert "why you did not pick" not in MAX_LOOP and "rejected" not in MAX_LOOP
