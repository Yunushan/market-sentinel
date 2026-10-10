from __future__ import annotations

import gc
import threading
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

    def _exercise_api_pacing(self, *, dispatch_delay=0.0, transport_duration=0.0):
        calls = []
        now = [10.0]
        sleeps = []
        delayed = [False]

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
                now[0] += transport_duration
                return Response()

        def factory(config):
            runtime = AdapterRuntime("kalshi", config, session=Session())
            original = runtime._transport_accepts_keyword

            def transport_option(keyword):
                if not delayed[0]:
                    now[0] += dispatch_delay
                    delayed[0] = True
                return original(keyword)

            runtime._transport_accepts_keyword = transport_option
            return KalshiAdapter(config, runtime=runtime)

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
        return calls, sleeps

    def test_separate_api_adapter_instances_keep_upstream_pacing(self) -> None:
        calls, sleeps = self._exercise_api_pacing()
        self.assertEqual(len(sleeps), 3)
        for first, second in zip(calls, calls[1:], strict=False):
            self.assertAlmostEqual(second - first, 0.04)
        for delay in sleeps:
            self.assertAlmostEqual(delay, 0.04)

    def test_dispatch_delay_cannot_compress_the_next_upstream_interval(self) -> None:
        calls, sleeps = self._exercise_api_pacing(dispatch_delay=0.03)
        self.assertEqual(len(sleeps), 3)
        for first, second in zip(calls, calls[1:], strict=False):
            self.assertAlmostEqual(second - first, 0.04)
        for delay in sleeps:
            self.assertAlmostEqual(delay, 0.04)

    def test_slow_transport_keeps_the_interval_after_dispatch_returns(self) -> None:
        calls, sleeps = self._exercise_api_pacing(transport_duration=0.2)
        self.assertEqual(len(sleeps), 3)
        for first, second in zip(calls, calls[1:], strict=False):
            self.assertAlmostEqual(second - first, 0.24)
        for delay in sleeps:
            self.assertAlmostEqual(delay, 0.04)

    def test_dispatch_failure_releases_the_slot_and_preserves_pacing(self) -> None:
        now = [10.0]
        sleeps = []

        def sleep(seconds):
            sleeps.append(seconds)
            now[0] += seconds

        limiter = RateLimiter(0.04, clock=lambda: now[0], sleeper=sleep)
        with self.assertRaisesRegex(RuntimeError, "transport failed"):
            with limiter.request_slot():
                now[0] += 0.03
                raise RuntimeError("transport failed")
        self.assertFalse(limiter._lock.locked())
        with limiter.request_slot():
            self.assertAlmostEqual(now[0], 10.07)
        self.assertEqual(len(sleeps), 1)
        self.assertAlmostEqual(sleeps[0], 0.04)
        self.assertFalse(limiter._lock.locked())

    def test_concurrent_dispatches_cannot_use_the_same_venue_slot(self) -> None:
        now = [10.0]
        first_entered = threading.Event()
        release_first = threading.Event()
        second_attempted = threading.Event()
        second_entered = threading.Event()
        failures = []
        calls = []

        def sleep(seconds):
            now[0] += seconds

        limiter = RateLimiter(0.04, clock=lambda: now[0], sleeper=sleep)

        def first():
            try:
                with limiter.request_slot():
                    calls.append(now[0])
                    first_entered.set()
                    if not release_first.wait(2):
                        raise RuntimeError("test dispatch was not released")
                    now[0] += 0.2
            except Exception as exc:
                failures.append(exc)

        def second():
            try:
                second_attempted.set()
                with limiter.request_slot():
                    calls.append(now[0])
                    second_entered.set()
            except Exception as exc:
                failures.append(exc)

        first_thread = threading.Thread(target=first, daemon=True)
        second_thread = threading.Thread(target=second, daemon=True)
        first_thread.start()
        try:
            self.assertTrue(first_entered.wait(2))
            second_thread.start()
            self.assertTrue(second_attempted.wait(2))
            self.assertFalse(second_entered.wait(0.05))
        finally:
            release_first.set()
            first_thread.join(2)
            if second_thread.ident is not None:
                second_thread.join(2)
        self.assertFalse(first_thread.is_alive())
        self.assertFalse(second_thread.is_alive())
        self.assertEqual(failures, [])
        self.assertTrue(second_entered.is_set())
        self.assertEqual(len(calls), 2)
        self.assertAlmostEqual(calls[1] - calls[0], 0.24)

    def test_zero_interval_does_not_acquire_a_dispatch_slot(self) -> None:
        limiter = RateLimiter(0)
        limiter._lock.acquire()
        try:
            with limiter.request_slot():
                self.assertEqual(limiter._next_allowed_at, 0)
        finally:
            limiter._lock.release()

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
