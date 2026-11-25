import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from montara_core.bootstrap.config import ConfigError, load_config
from montara_core.bootstrap.main import main


class ConfigTests(unittest.TestCase):
    def test_default(self):
        self.assertEqual(load_config().port, 8080)

    def test_explicit_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text("[http]\nport = 0\n", encoding="utf-8")
            self.assertEqual(load_config(path).port, 0)

    def test_invalid_values_fail_closed(self):
        cases = ("[http]\nport = true", "[http]\nport = -1",
                 "[http]\nport = 65536", "[http]\nport = '8080'",
                 "[http]\nport = 1.5", "[http]\nhost = '0.0.0.0'",
                 "[cloud]\nenabled = true", "http = 42", "[http")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            for case in cases:
                with self.subTest(case=case):
                    path.write_text(case, encoding="utf-8")
                    with self.assertRaises(ConfigError):
                        load_config(path)
                    with patch("montara_core.bootstrap.main.create_server") as create:
                        with contextlib.redirect_stderr(io.StringIO()):
                            self.assertEqual(main(["--config", str(path)]), 2)
                        create.assert_not_called()

    def test_missing_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ConfigError):
                load_config(Path(tmp) / "missing.toml")

    def test_check_only_never_binds(self):
        with patch("montara_core.bootstrap.main.create_server") as create:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["--check-config"]), 0)
            create.assert_not_called()

    def test_bind_failure(self):
        with patch("montara_core.bootstrap.main.create_server", side_effect=OSError):
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main([]), 1)
