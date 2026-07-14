from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from polymarket.leaderboard_state import LeaderboardStateStore


class LeaderboardStateStoreTests(unittest.TestCase):
    def test_mdd_state_keeps_export_provenance_without_point_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LeaderboardStateStore(Path(tmp) / "leaderboard.sqlite3")
            try:
                store.prepare({"remote_sort": "PNL", "direction": "DESC", "period": "all", "category": "OVERALL"}, resume=False)
                store.record_page(
                    0,
                    50,
                    [
                        {
                            "rank": 1,
                            "display_name": "leader",
                            "wallet": "0x" + "1" * 40,
                            "pnl_usd": 20.0,
                            "volume_usd": 100.0,
                            "roi_pct": 20.0,
                            "trade_count": 3,
                            "raw": {"rank": 1},
                        }
                    ],
                )
                row = next(store.iter_mdd_candidates({}, sort="roi_pct", direction="DESC", limit=1))
                store.set_mdd(
                    row["id"],
                    {
                        "mdd_usd": 5.0,
                        "mdd_pct": 10.0,
                        "mdd_method": "public_data_historical_equity_curve_v2",
                        "mdd_pct_basis": "drawdown_usd / equity",
                        "points_total": 500,
                        "points": [{"timestamp": index, "value": float(index)} for index in range(500)],
                        "limitations": ["public data only"],
                    },
                )

                result = next(store.iter_results({}, require_mdd=True, sort="roi_pct", direction="DESC", limit=1))
                self.assertEqual(result["mdd_pct"], 10.0)
                self.assertEqual(result["mdd_pct_basis"], "drawdown_usd / equity")
                self.assertEqual(result["points_total"], 500)
                self.assertNotIn("points", result)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
