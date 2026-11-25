"""Static import policy; not a sandbox or a dynamic-import security proof."""
import ast
from pathlib import Path
import sys
import unittest

import montara_core


ALLOWED = {
    "domain": {"domain"},
    "application": {"domain", "application"},
    "api": {"application", "api"},
    "adapters": {"domain", "application", "adapters"},
    "bootstrap": {"domain", "application", "api", "adapters", "bootstrap"},
    "__main__": {"bootstrap"},
    "__init__": set(),
}


def violations(layer: str, source: str) -> list[str]:
    errors = []
    if layer not in ALLOWED:
        return [f"unknown package layer: {layer}"]
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                errors.append("relative imports are not allowed")
                continue
            modules = [node.module or ""]
        elif isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        else:
            continue
        for module in modules:
            root, _, tail = module.partition(".")
            if root == "montara_core":
                target = tail.split(".")[0]
                if target not in ALLOWED[layer]:
                    errors.append(f"{layer} cannot import {module}")
            elif root not in sys.stdlib_module_names:
                errors.append(f"third-party dependency not allowed in H1.1: {module}")
    return errors


class ArchitectureTests(unittest.TestCase):
    def test_package_import_rules(self):
        root = Path(montara_core.__file__).parent
        paths = sorted(root.rglob("*.py"))
        self.assertGreater(len(paths), 0)
        for path in paths:
            with self.subTest(path=path.relative_to(root)):
                first = path.relative_to(root).parts[0]
                layer = Path(first).stem
                self.assertEqual(violations(layer, path.read_text(encoding="utf-8")), [])

    def test_rules_reject_forbidden_imports(self):
        for layer, source in [
            ("domain", "from montara_core.api import http"),
            ("application", "import montara_core.adapters.sqlite"),
            ("application", "import google.cloud.storage"),
            ("api", "from montara_core.bootstrap import config"),
            ("domain", "from .. import adapters"),
            ("api", "from montara_core import bootstrap"),
        ]:
            with self.subTest(layer=layer, source=source):
                self.assertTrue(violations(layer, source))

    def test_rules_accept_allowed_imports(self):
        self.assertEqual(violations("api", "from montara_core.application.health import health"), [])
        self.assertEqual(violations("bootstrap", "import tomllib"), [])
