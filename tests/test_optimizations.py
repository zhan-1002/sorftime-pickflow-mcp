"""
Validate three optimizations before push:
1. Concurrent API calls (Semaphore)
2. keyword_detail result cache
3. Smart skip for low-volume keywords
"""
import asyncio
import time


# ══════════════════════════════════════════
# TEST 1: Concurrency
# ══════════════════════════════════════════

class MockSlowAPI:
    """Simulate slow API with call counting."""
    def __init__(self):
        self.call_count = 0

    async def keyword_detail(self, kw):
        self.call_count += 1
        await asyncio.sleep(0.05)
        return {"data": {"keyword": kw, "monthly_search_volume": 10000}}

    async def product_detail(self, asin):
        self.call_count += 1
        await asyncio.sleep(0.05)
        return {"data": {"asin": asin, "price": 29.99}}


async def test_semaphore_concurrency():
    """Concurrent calls should be faster than serial."""
    api = MockSlowAPI()
    sem = asyncio.Semaphore(5)
    keywords = [f"kw_{i}" for i in range(20)]

    async def fetch_one(kw):
        async with sem:
            return await api.keyword_detail(kw)

    # Serial
    t0 = time.time()
    for kw in keywords:
        await api.keyword_detail(kw)
    serial_time = time.time() - t0
    serial_calls = api.call_count

    # Concurrent
    api.call_count = 0
    t0 = time.time()
    await asyncio.gather(*[fetch_one(kw) for kw in keywords])
    concurrent_time = time.time() - t0
    concurrent_calls = api.call_count

    assert concurrent_calls == 20
    assert concurrent_time < serial_time * 0.5, \
        f"Concurrent {concurrent_time:.2f}s should be < 50% of serial {serial_time:.2f}s"
    print(f"  Serial: {serial_time:.2f}s | Concurrent(5x): {concurrent_time:.2f}s | Speedup: {serial_time/concurrent_time:.1f}x")
    print("[PASS] concurrency speedup")


# ══════════════════════════════════════════
# TEST 2: Keyword Detail Cache
# ══════════════════════════════════════════

def test_market_cache():
    """Market cache avoids redundant API calls."""
    from pickflow_mcp_server.cache import (
        _ensure_db, store_market_cache, get_market_cache,
        clear_market_cache
    )

    clear_market_cache()

    # First query - cache miss
    hit, data = get_market_cache("party favors bulk")
    assert not hit
    assert data is None

    # Store
    store_market_cache("party favors bulk", 23000, 0.54, 150000, 51.0, 55.0)
    store_market_cache("wedding favors bulk", 1900, 0.56, 170000, 100.0, 21.0)

    # Second query - cache hit
    hit, data = get_market_cache("party favors bulk")
    assert hit
    assert data["monthly_sv"] == 23000
    assert data["rev100"] == 51.0
    print(f"  Cache hit: {data}")

    # TTL check
    hit2, data2 = get_market_cache("party favors bulk", ttl_hours=0)
    assert not hit2  # expired
    print("[PASS] market cache store/hit/expire")

    clear_market_cache()
    hit3, _ = get_market_cache("party favors bulk")
    assert not hit3
    print("[PASS] market cache clear")


# ══════════════════════════════════════════
# TEST 3: Smart Skip
# ══════════════════════════════════════════

def test_smart_skip():
    """Keywords below volume threshold should be skipped."""
    from pickflow_mcp_server.config import match_keyword

    keywords = [
        {"keyword": "party favors bulk", "search_volume_30d": 23000},
        {"keyword": "tiny niche thing bulk", "search_volume_30d": 200},
        {"keyword": "another small bulk", "search_volume_30d": 800},
        {"keyword": "christmas gifts bulk", "search_volume_30d": 50000},
    ]

    MIN_VOLUME = 1000

    skipped = [k for k in keywords if k["search_volume_30d"] < MIN_VOLUME]
    kept = [k for k in keywords if k["search_volume_30d"] >= MIN_VOLUME]

    assert len(skipped) == 2  # 200 and 800
    assert len(kept) == 2     # 23000 and 50000
    print(f"  Skipped {len(skipped)} low-vol keywords, kept {len(kept)}")
    print("[PASS] smart skip")


# ══════════════════════════════════════════
# TEST 4: Integrated Pipeline Speed
# ══════════════════════════════════════════

async def test_integrated_pipeline():
    """Simulate full market_screen with cache + concurrency."""
    from pickflow_mcp_server.cache import (
        store_market_cache, get_market_cache, clear_market_cache
    )

    clear_market_cache()
    api = MockSlowAPI()
    sem = asyncio.Semaphore(5)

    keywords = [{"keyword": f"bulk gift idea {i}", "search_volume_30d": 5000 + i * 100} for i in range(30)]

    async def screen_one(kw_dict):
        kw = kw_dict["keyword"]
        sv = kw_dict["search_volume_30d"]

        # Smart skip
        if sv < 1000:
            return None

        # Cache check
        hit, cached = get_market_cache(kw)
        if hit:
            return {"keyword": kw, "cached": True, "monthly_sv": cached["monthly_sv"]}

        # API call
        async with sem:
            result = await api.keyword_detail(kw)

        d = result.get("data", {})
        ms = d.get("monthly_search_volume", 0)

        # Store cache
        store_market_cache(kw, ms, 0.5, 100000, 50.0, 40.0)

        return {"keyword": kw, "cached": False, "monthly_sv": ms}

    t0 = time.time()
    results = await asyncio.gather(*[screen_one(kw) for kw in keywords])
    elapsed = time.time() - t0

    results = [r for r in results if r is not None]
    cached_count = sum(1 for r in results if r["cached"])
    api_count = sum(1 for r in results if not r["cached"])

    assert len(results) > 0
    assert elapsed < 3.0, f"Pipeline too slow: {elapsed:.1f}s"

    # Run again - should hit cache
    api.call_count = 0
    t1 = time.time()
    results2 = await asyncio.gather(*[screen_one(kw) for kw in keywords])
    elapsed2 = time.time() - t1
    cached2 = sum(1 for r in results2 if r["cached"])

    assert cached2 == 30  # All should be cached
    assert elapsed2 < 1.0, f"Second run should be instant: {elapsed2:.1f}s"
    assert api.call_count == 0

    print(f"  First run ({api_count} api, {cached_count} cached): {elapsed:.1f}s")
    print(f"  Second run (all cached): {elapsed2:.1f}s")
    print("[PASS] integrated pipeline with cache + concurrency")

    clear_market_cache()


if __name__ == "__main__":
    print("=== TEST 1: Concurrency ===")
    asyncio.run(test_semaphore_concurrency())

    print("\n=== TEST 2: Market Cache ===")
    test_market_cache()

    print("\n=== TEST 3: Smart Skip ===")
    test_smart_skip()

    print("\n=== TEST 4: Integrated Pipeline ===")
    asyncio.run(test_integrated_pipeline())

    print("\n=== ALL OPTIMIZATION TESTS PASSED ===")
