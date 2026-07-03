import unittest

from app.services.chat_json import (
    extract_json_array,
    extract_json_object,
    parse_json_array,
    parse_json_object,
)


class ChatJsonTests(unittest.TestCase):
    def test_parse_embedded_object(self):
        self.assertEqual(parse_json_object('prefix {"a": 1} suffix'), {"a": 1})

    def test_parse_embedded_array(self):
        self.assertEqual(parse_json_array('prefix [{"a": 1}] suffix'), [{"a": 1}])

    def test_extract_json_code_fence(self):
        self.assertEqual(extract_json_object('```json\n{"ok": true}\n```'), '{"ok": true}')

    def test_invalid_parse_returns_none(self):
        self.assertIsNone(parse_json_object("not json"))
        self.assertIsNone(parse_json_array("not json"))

    def test_extract_falls_back_to_cleaned_text(self):
        self.assertEqual(extract_json_array("not json"), "not json")


if __name__ == "__main__":
    unittest.main()
