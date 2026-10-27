"""The ticket frontmatter parser.   python -m unittest discover tests"""

import unittest

import support  # noqa: F401  (puts core/engine on the path)

import todo  # noqa: E402


class Parse(unittest.TestCase):
    def test_quoted_title_means_the_text_inside(self):
        meta, _ = todo.parse('---\nid: T0001\ntitle: "Bug: the board drops a ticket"\n---\nbody\n')
        self.assertEqual(meta["title"], "Bug: the board drops a ticket")

    def test_title_that_only_starts_with_a_quote_is_kept(self):
        meta, _ = todo.parse('---\ntitle: "ressurect" is misspelled in two "tables"\n---\n')
        self.assertEqual(meta["title"], '"ressurect" is misspelled in two "tables"')


if __name__ == "__main__":
    unittest.main()
