from __future__ import annotations

import gc
import unittest
from unittest.mock import patch

from core.models import AppConfig
from market_adapters.errors import MarketConfigurationError
from market_adapters.kalshi import KalshiAdapter
from market_adapters.registry import AdapterRegistry
from market_adapters.runtime import AdapterRuntime, RateLimiter
import market_adapters.runtime as runtime_module
from web_api import market_events_payload


class SharedRateLimitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry_patch = patch.dict(runtime_module._shared_rate_limiters, clear=True)
        self.registry_patch.start()
        self.addCleanup(self.registry_patch.stop)

    def test_separate_api_adapter_instances_keep_upstream_pacing(self) -> None:
        calls = []
        now = [10.0]
        sleeps = []

        def sleep(seconds):
            sleeps.append(seconds)
            now[0] += seconds

        def controlled_sleep(control, seconds):
            control.check()
            sleep(seconds)
            control.check()

        class Response:
            status_code = 200
            headers = {}

            def iter_content(self, chunk_size):
                yield b'{"markets":[]}'

            def close(self):
                pass

        class Session:
            def request(self, *_args, **_kwargs):
                calls.append(now[0])
                return Response()

        def factory(config):
            return KalshiAdapter(config, runtime=AdapterRuntime("kalshi", config, session=Session()))

        cfg = AppConfig()
        cfg.markets["kalshi"].enabled = True
        cfg.markets["kalshi"].settings["min_request_interval_seconds"] = 0.04
        registry = AdapterRegistry()
        registry.register_factory(KalshiAdapter.metadata, factory)
        # Exercise the real shared limiter and API path without making assertions
        # about OS scheduling between reservation and the fake HTTP transport.
        with patch.object(
            runtime_module, "RateLimiter",
            side_effect=lambda interval=0: RateLimiter(interval, clock=lambda: now[0], sleeper=sleep),
        ), patch("core.request_control.RequestControl.sleep", autospec=True, side_effect=controlled_sleep):
            for _ in range(4):
                market_events_payload(cfg, registry, "kalshi", {})
                gc.collect()  # Collection must not erase the reserved next interval.
                self.assertFalse(runtime_module._shared_rate_limiters["kalshi"].owners)
        self.assertEqual(len(calls), 4)
        self.assertEqual(len(sleeps), 3)
        for first, second in zip(calls, calls[1:], strict=False):
            self.assertAlmostEqual(second - first, 0.04)
        for delay in sleeps:
            self.assertAlmostEqual(delay, 0.04)

    def test_bounded_registry_does_not_evict_schedules_with_live_owners(self) -> None:
        with patch("market_adapters.runtime.MAX_SHARED_RATE_LIMITERS", 2):
            first = AdapterRuntime("venue-one", min_request_interval_seconds=0.04)
            second = AdapterRuntime("venue-two", min_request_interval_seconds=0.04)
            with self.assertRaisesRegex(MarketConfigurationError, "Too many active"):
                AdapterRuntime("venue-three", min_request_interval_seconds=0.04)
            self.assertEqual(len(runtime_module._shared_rate_limiters), 2)
            del first
            gc.collect()
            third = AdapterRuntime("venue-three", min_request_interval_seconds=0.04)
            self.assertIsNot(second.rate_limiter, third.rate_limiter)
            self.assertEqual(len(runtime_module._shared_rate_limiters), 2)

    def test_new_runtime_cannot_weaken_existing_venue_schedule(self) -> None:
        first = AdapterRuntime("venue-one", min_request_interval_seconds=0.04)
        second = AdapterRuntime("venue-one", min_request_interval_seconds=0)
        self.assertIs(first.rate_limiter, second.rate_limiter)
        self.assertEqual(second.rate_limiter.min_interval_seconds, 0.04)

    def test_stricter_interval_extends_the_existing_reservation(self) -> None:
        now = [10.0]
        sleeps = []

        def sleep(seconds):
            sleeps.append(seconds)
            now[0] += seconds

        limiter = RateLimiter(0.04, clock=lambda: now[0], sleeper=sleep)
        self.assertEqual(limiter.wait(), 0)
        now[0] += 0.01
        limiter.strengthen_interval(0.1)
        self.assertAlmostEqual(limiter.wait(), 0.09)
        self.assertEqual(len(sleeps), 1)
        self.assertAlmostEqual(now[0], 10.1)
        limiter.strengthen_interval(0)
        self.assertEqual(limiter.min_interval_seconds, 0.1)


if __name__ == "__main__":
    unittest.main()
