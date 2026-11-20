#!/usr/bin/env python3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import html_guard


_HERE = Path(__file__).resolve().parent
SOURCE = _HERE.parent / 'index.html'
if not SOURCE.exists():
    SOURCE = _HERE / 'index.html'


class HtmlGuardTests(unittest.TestCase):
    def setUp(self):
        self.good = SOURCE.read_text(encoding='utf-8')
        self.bad = self.good.replace('</script>', '}</script>', 1)

    def test_current_page_passes(self):
        output = html_guard.validate_html(SOURCE)
        self.assertIn('RENDER CHECK OK', output)
        self.assertIn('currentValue=', output)

    def test_broken_page_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / 'broken.html'
            candidate.write_text(self.bad, encoding='utf-8')
            with self.assertRaises(html_guard.RenderCheckError):
                html_guard.validate_html(candidate)

    def test_broken_candidate_never_replaces_current_page(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'index.html'
            target.write_text(self.good, encoding='utf-8')
            before = target.read_bytes()
            with self.assertRaises(html_guard.RenderCheckError):
                html_guard.write_html_checked(target, self.bad)
            self.assertEqual(before, target.read_bytes())

    def test_post_write_failure_rolls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'index.html'
            target.write_text(self.good, encoding='utf-8')
            before = target.read_bytes()
            with mock.patch.object(
                html_guard,
                'validate_html',
                side_effect=['RENDER CHECK OK', html_guard.RenderCheckError('模拟发布后复检失败')],
            ):
                with self.assertRaises(html_guard.RenderCheckError):
                    html_guard.write_html_checked(target, self.good + '\n<!-- new version -->')
            self.assertEqual(before, target.read_bytes())


if __name__ == '__main__':
    unittest.main(verbosity=2)
