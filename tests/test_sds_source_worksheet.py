"""Static guardrails for the blank source worksheet, not safety-content validation."""
from pathlib import Path
import re
import unittest

SOURCE = (Path(__file__).resolve().parents[1] / 'docs' / 'sds-source-worksheet.html').read_text(encoding='utf-8')

class SourceWorksheetTest(unittest.TestCase):
    def test_not_a_supplier_sds(self):
        for phrase in ['NOT A SAFETY DATA SHEET', 'NOT VALID FOR CHEMICAL USE OR EMERGENCIES', 'does not generate a compliant SDS', 'Unknown / not yet checked', 'omits required subsections', 'must not be represented as REACH-compliant']:
            self.assertIn(phrase, SOURCE)
    def test_no_remote_code_media_or_persistence(self):
        script = SOURCE.split('<script>', 1)[1].split('</script>', 1)[0]
        self.assertNotRegex(script, r'\b(fetch|XMLHttpRequest|localStorage|sessionStorage|indexedDB)\b|document\.cookie')
        self.assertNotRegex(SOURCE, r'<(?:iframe|img|link)\b|<script\s+src=')
    def test_sixteen_headings_and_no_safety_defaults(self):
        headings = re.search(r'const headings=\[(.*?)\];', SOURCE).group(1)
        self.assertEqual(len(re.findall(r"'[^']*'", headings)), 16)
        self.assertIn("textarea.placeholder='Unknown / not yet checked", SOURCE)
        self.assertNotIn('value="Safe"', SOURCE)
    def test_print_keeps_warning_on_every_section(self):
        self.assertIn('window.print()', SOURCE)
        self.assertIn('SOURCE WORKSHEET ONLY · NOT AN SDS', SOURCE)
        self.assertIn('@media print', SOURCE)
        self.assertIn('break-inside:avoid', SOURCE)
    def test_clear_requires_explicit_confirmation(self):
        self.assertIn("if(!confirm('Clear every worksheet entry?", SOURCE)

if __name__ == '__main__':
    unittest.main()
