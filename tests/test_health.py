import json
from pathlib import Path
import queue
import re
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen

from montara_core.application.health import health


class HealthTests(unittest.TestCase):
    def test_use_case(self):
        self.assertEqual(health(), {"status": "ok"})

    def test_installed_entrypoint_and_http(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "test.toml"
            config.write_text("[http]\nport = 0\n", encoding="utf-8")
            with subprocess.Popen(
                [sys.executable, "-m", "montara_core", "--config", str(config)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            ) as process:
                lines = queue.Queue()
                reader = threading.Thread(
                    target=lambda: lines.put(process.stdout.readline()), daemon=True
                )
                reader.start()
                try:
                    line = lines.get(timeout=10)
                    match = re.fullmatch(
                        r"Montara Core health listening on 127\.0\.0\.1:(\d+)\n", line
                    )
                    self.assertIsNotNone(match, line)
                    url = f"http://127.0.0.1:{match.group(1)}"
                    with urlopen(url + "/health", timeout=5) as response:
                        self.assertEqual(response.status, 200)
                        self.assertEqual(response.headers.get_content_type(), "application/json")
                        self.assertEqual(json.load(response), {"status": "ok"})
                    with self.assertRaises(HTTPError) as caught:
                        urlopen(url + "/unknown", timeout=5)
                    with caught.exception as response:
                        self.assertEqual(response.code, 404)
                        self.assertEqual(json.load(response), {"error": "not_found"})
                finally:
                    if process.poll() is None:
                        process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                    reader.join(timeout=2)

    def test_invalid_config_cli_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "bad.toml"
            config.write_text("[http]\nport = true\n", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-m", "montara_core", "--config", str(config)],
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertIn("Configuration error:", result.stderr)
            self.assertNotIn(str(config), result.stderr)
