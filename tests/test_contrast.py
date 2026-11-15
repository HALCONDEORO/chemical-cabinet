import re
import unittest

from chemical_cabinet import app


def luminance(hex_color):
    channels = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    channels = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def ratio(a, b):
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


class ContrastTest(unittest.TestCase):
    def css(self):
        return app.CSS if hasattr(app, "CSS") else "".join(
            v for v in vars(app).values() if isinstance(v, str) and "input,textarea,button{" in v)

    def test_input_border_is_visible_on_white_and_page_background(self):
        rule = re.search(r"input,textarea,button\{[^}]*\}", self.css()).group(0)
        border = re.search(r"border:1px solid (#[0-9a-f]{6})", rule).group(1)
        self.assertGreaterEqual(ratio(border, "#ffffff"), 3.0)
        self.assertGreaterEqual(ratio(border, "#fbfaf6"), 3.0)


if __name__ == "__main__":
    unittest.main()
