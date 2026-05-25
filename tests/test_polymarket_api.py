from __future__ import annotations

import unittest
from unittest.mock import patch

from polymarket import clob_rest, data_api, gamma


class FakeResponse:
    def __init__(self, payload, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class PolymarketApiWrapperTests(unittest.TestCase):
    def test_parse_market_outcomes_handles_json_encoded_fields(self) -> None:
        outcomes = gamma.parse_market_outcomes(
            {
                "clobTokenIds": '["token-yes", "token-no"]',
                "outcomes": '["Yes", "No"]',
                "outcomePrices": '["0.61", "0.39"]',
            }
        )

        self.assertEqual([o.outcome for o in outcomes], ["Yes", "No"])
        self.assertEqual([o.token_id for o in outcomes], ["token-yes", "token-no"])
        self.assertEqual([o.price for o in outcomes], [0.61, 0.39])

    def test_best_bid_ask_accepts_books_and_legacy_buy_sell_names(self) -> None:
        self.assertEqual(
            clob_rest.best_bid_ask_from_book(
                {"bids": [{"price": "0.49"}], "asks": [{"price": "0.51"}]}
            ),
            (0.49, 0.51),
        )
        self.assertEqual(
            clob_rest.best_bid_ask_from_book(
                {"buys": [{"price": "0.48"}], "sells": [{"price": "0.52"}]}
            ),
            (0.48, 0.52),
        )

    def test_get_midpoint_accepts_dict_payload(self) -> None:
        with patch("polymarket.clob_rest.requests.get", return_value=FakeResponse({"midpoint": "0.42"})) as mock_get:
            midpoint = clob_rest.get_midpoint("token-1", timeout=3)

        self.assertEqual(midpoint, 0.42)
        self.assertEqual(mock_get.call_args.kwargs["params"], {"token_id": "token-1"})
        self.assertEqual(mock_get.call_args.kwargs["timeout"], 3)

    def test_activity_request_clamps_limit_and_offset_and_passes_filters(self) -> None:
        with patch("polymarket.data_api.requests.get", return_value=FakeResponse([{"id": 1}])) as mock_get:
            result = data_api.get_activity(
                "0xabc",
                limit=999,
                offset=-5,
                types=["TRADE"],
                side="BUY",
                market=["condition-1"],
                start=10,
                end=20,
                timeout=4,
            )

        params = mock_get.call_args.kwargs["params"]
        self.assertEqual(result, [{"id": 1}])
        self.assertEqual(params["limit"], 500)
        self.assertEqual(params["offset"], 0)
        self.assertEqual(params["type"], ["TRADE"])
        self.assertEqual(params["side"], "BUY")
        self.assertEqual(params["market"], ["condition-1"])
        self.assertEqual(params["start"], 10)
        self.assertEqual(params["end"], 20)
        self.assertEqual(mock_get.call_args.kwargs["timeout"], 4)


if __name__ == "__main__":
    unittest.main()
