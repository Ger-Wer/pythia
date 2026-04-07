from __future__ import annotations

from pathlib import Path
import unittest


class DockerfileLayoutTests(unittest.TestCase):
    def test_dockerfile_copies_decision_core_sql_directory(self) -> None:
        dockerfile = Path(__file__).resolve().parent.parent / "Dockerfile"
        content = dockerfile.read_text(encoding="utf-8")

        self.assertIn("COPY sql /app/sql", content)


if __name__ == "__main__":
    unittest.main()
