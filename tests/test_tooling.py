"""Checks on the injected JavaScript and the operator scripts.

The JS is validated with Node when Node is available. That proves syntax and
construction, not behaviour in a real browser — only a live run does that.
"""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.helpers import ROOT

from core.browser.js import ALL_JS_SNIPPETS, PICKER_JS

NODE = shutil.which("node")


class JavaScriptTests(unittest.TestCase):
    def test_all_snippets_present(self):
        self.assertIn("PICKER_JS", ALL_JS_SNIPPETS)
        self.assertIn("CONTENT_SIGNATURE_JS", ALL_JS_SNIPPETS)
        for name, source in ALL_JS_SNIPPETS.items():
            self.assertTrue(source.strip(), name)

    def test_picker_uses_real_dom_apis(self):
        # A fake picker would not need these; this guards against placeholder JS.
        for token in ("addEventListener", "querySelectorAll", "classList", "nth-of-type"):
            self.assertIn(token, PICKER_JS)
        self.assertIn("window.__b2bPicker", PICKER_JS)

    @unittest.skipIf(NODE is None, "Node is not installed")
    def test_snippets_are_syntactically_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, source in ALL_JS_SNIPPETS.items():
                # Arrow-function snippets are expressions; statements need wrapping.
                code = source if name == "CONTENT_SIGNATURE_JS" else f"void ({source});"
                path = Path(tmp) / f"{name}.js"
                path.write_text(code, encoding="utf-8")
                result = subprocess.run([NODE, "--check", str(path)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, f"{name}: {result.stderr[:300]}")


class ScriptTests(unittest.TestCase):
    def _load(self, relative: str):
        path = ROOT / relative
        spec = importlib.util.spec_from_file_location(path.stem, path)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        return module

    def test_assess_site_imports(self):
        module = self._load("scripts/assess_site.py")
        self.assertTrue(callable(module.main))

    def test_verify_live_imports(self):
        module = self._load("scripts/verify_live.py")
        self.assertTrue(callable(module.main))

    def test_verify_live_reports_missing_browser(self):
        if "playwright" in sys.modules:
            sys.modules.pop("playwright", None)
        module = self._load("scripts/verify_live.py")
        # diagnose() returns (False, help) when Playwright is absent.
        ok, message = module.diagnose()
        if ok:
            self.skipTest("Playwright is actually installed here")
        self.assertIn("playwright", message.lower())


if __name__ == "__main__":
    unittest.main()
