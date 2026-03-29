import sqlite3
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch

import requests

from python import resolution_checker


class ResolutionCheckerTests(unittest.TestCase):
    def test_fetch_market_marks_repeated_422_as_invalid(self):
        response = requests.Response()
        response.status_code = 422
        response._content = b'{"error":"unprocessable entity"}'
        http_error = requests.HTTPError(response=response)

        with patch(
            "python.resolution_checker.requests.get",
            side_effect=[http_error, http_error, http_error],
        ):
            market, should_invalidate = resolution_checker.fetch_market("missing-market")

        self.assertIsNone(market)
        self.assertTrue(should_invalidate)

    def test_mark_market_invalid_sets_is_valid_zero(self):
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "pythia.db"
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    CREATE TABLE predictions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        market_id TEXT NOT NULL,
                        outcome INTEGER,
                        is_valid INTEGER DEFAULT 1
                    )
                    """
                )
                conn.executemany(
                    """
                    INSERT INTO predictions (market_id, outcome, is_valid)
                    VALUES (?, ?, ?)
                    """,
                    [
                        ("market-a", None, 1),
                        ("market-a", None, 1),
                    ],
                )

                resolution_checker.mark_market_invalid(
                    conn, "market-a", "Gamma API returned HTTP 422"
                )

                rows = conn.execute(
                    "SELECT is_valid FROM predictions WHERE market_id = ?",
                    ("market-a",),
                ).fetchall()

            self.assertEqual(rows, [(0,), (0,)])


if __name__ == "__main__":
    unittest.main()
