"""Tests for DiffLineMapper unified diff parsing and GitHub coordinate validation."""

from pr_agent.github.diff_mapper import DiffLineMapper

SAMPLE_DIFF = """diff --git a/services/refund.py b/services/refund.py
index 1111111..2222222 100644
--- a/services/refund.py
+++ b/services/refund.py
@@ -10,4 +10,6 @@ def refund_order(order):
     if order is None:
         raise ValueError("missing")
-    return stripe.refund(order.id)
+    if order.status == "refunded":
+        raise ValueError("already refunded")
+    return stripe.refund(order.id, amount=order.amount)
"""


def test_diff_mapper_validates_added_context_and_deleted_lines() -> None:
    mapper = DiffLineMapper(SAMPLE_DIFF)
    assert mapper.files == ["services/refund.py"]

    # Context line 10 and 11 exist on both sides.
    assert mapper.is_valid_line("services/refund.py", 10, "RIGHT") is True
    assert mapper.is_valid_line("services/refund.py", 10, "LEFT") is True

    # Deleted line 12 only exists on LEFT.
    assert mapper.is_valid_line("services/refund.py", 12, "LEFT") is True

    # Added lines 12, 13, 14 exist on RIGHT.
    assert mapper.is_valid_line("services/refund.py", 12, "RIGHT") is True
    assert mapper.is_valid_line("services/refund.py", 14, "RIGHT") is True

    # Line 50 is outside the diff hunk -> fallback_to_summary=True.
    outside = mapper.map_location("services/refund.py", 50, "RIGHT")
    assert outside.in_diff_hunk is False
    assert outside.fallback_to_summary is True

    # Unknown file -> fallback_to_summary=True.
    unknown = mapper.map_location("other.py", 1, "RIGHT")
    assert unknown.fallback_to_summary is True
