"""Patch 107: phone-only layout rules in the panel; the desktop rules stay as they were."""
import os, re, unittest

import jh_harness as H


class PhoneLayout(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(H.built_dir(), "frontend", "jarvis-panel.js"), encoding="utf-8") as f:
            self.js = f.read()

    def block(self):
        start = self.js.index("/* jh-phone-fit")
        return self.js[start:self.js.index("very small phones", start)]

    def test_added_once_inside_a_phone_media_query(self):
        self.assertEqual(self.js.count("/* jh-phone-fit"), 1)
        self.assertIn("@media (max-width: 600px) {", self.block())

    def test_header_and_chat_fit(self):
        b = self.block()
        for rule in (".brand > span { display: none; }", ".masthead-right { grid-column: 1 / -1;",
                     ".chat-compose { flex-wrap: wrap; }", ".chat-compose .chat-input { flex: 1 1 100%; }"):
            self.assertIn(rule, b)

    def test_other_tabs_fit(self):
        b = self.block()
        for sel in (".faces-card-snap .faces-snap-cam", ".log-entry .log-msg", ".sug-ev, .sug-ev li"):
            self.assertIn(sel, b)

    def test_desktop_rules_unchanged(self):
        self.assertIn("  .faces-snap-cam { max-width: 140px; }", self.js)
        self.assertIn("    font-size: 19px;\n    letter-spacing: 0.35em;", self.js)
        self.assertEqual(self.js.count("  .chat-compose {\n    display: flex; gap: 8px; align-items: flex-end;"), 1)

    def test_braces_balance(self):
        b = self.block()
        self.assertEqual(b.count("{"), b.count("}"))


if __name__ == "__main__":
    unittest.main()
