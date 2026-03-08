import unittest

from translate_epub_ai.models import PendingNode
from translate_epub_ai.openai_batch import build_grouped_requests


class BatchGroupingTests(unittest.TestCase):
    def test_grouping_keeps_related_nodes_together(self) -> None:
        pending = [
            PendingNode(rel_path="a.xhtml", node_index=0, core_text="A" * 20),
            PendingNode(rel_path="a.xhtml", node_index=1, core_text="B" * 20),
            PendingNode(rel_path="b.xhtml", node_index=0, core_text="C" * 20),
        ]

        groups = build_grouped_requests(
            pending=pending,
            max_items_per_request=2,
            max_chars_per_request=500,
        )

        self.assertEqual(2, len(groups))
        self.assertEqual(["a.xhtml", "a.xhtml"], [item.rel_path for item in groups[0]])


if __name__ == "__main__":
    unittest.main()
