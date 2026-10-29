import pytest

from examples.reliability_comparison import slots, summarize


def observations():
    rows = []
    for slot in slots(5):
        unsafe_runtime = slot["arm"] == "runtime" and slot["mode"] == "unsafe" and slot["crash"]
        duplicate = slot["crash"] and (slot["arm"] == "volatile_key" or
                                      (slot["arm"] == "stable_key_only" and slot["mode"] == "unsafe"))
        rows.append({**slot, "requests": 1 if unsafe_runtime or not slot["crash"] else 2,
                     "effects": 2 if duplicate else 1,
                     "automatic_status": "NEEDS_REVIEW" if unsafe_runtime else "SUCCEEDED",
                     "operator_confirmation": unsafe_runtime})
    return rows


def test_control_denominators_and_manual_recovery_are_not_pooled():
    result = summarize(observations(), 5)
    assert result["trials"] == 36
    unsafe = next(g for g in result["groups"] if g["arm"] == "runtime" and g["mode"] == "unsafe" and g["crash"])
    assert unsafe["automatic_successes"] == 0
    assert unsafe["needs_review"] == unsafe["operator_confirmations"] == 5
    stable = next(g for g in result["groups"] if g["arm"] == "stable_key_only" and g["mode"] == "idempotent" and g["crash"])
    assert stable["duplicate_effects"] == 0 and stable["requests"] == 10


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "impossible_counts"])
def test_invalid_comparison_evidence_is_not_complete(mutation):
    rows = observations()
    if mutation == "missing":
        rows.pop()
    elif mutation == "duplicate":
        rows[-1] = rows[0]
    else:
        rows[0]["effects"] = 3
    with pytest.raises(ValueError):
        summarize(rows, 5)
