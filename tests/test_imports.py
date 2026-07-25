"""Verify all modules import cleanly and tool signatures are valid."""
import sys
import asyncio
import json

def test_imports():
    """All modules must import without error."""
    from pickflow_mcp_server import config, api, cache, scoring, server
    assert config.SWEETSPOT["price_min"] == 10
    assert config.FILTER_WORDS
    assert config.match_keyword("party favors bulk") == True
    assert config.match_keyword("toilet paper") == False
    assert config.match_keyword("roblox gift card") == False  # blacklist
    assert config.match_keyword("abc") == False  # too short
    print("[PASS] config imports + match_keyword")

    # api
    assert callable(api.call)
    assert callable(api.keyword_detail)
    assert callable(api.product_search)
    print("[PASS] api functions")

    # cache
    assert callable(cache.store_page)
    assert callable(cache.get_cache_status)
    assert callable(cache.query_pool)
    print("[PASS] cache functions")

    # scoring
    assert callable(scoring.score)
    assert callable(scoring.parse_exposure_items)
    assert scoring.WEIGHTS["sales_velocity"] == 2.0
    print("[PASS] scoring + weights")

    # server
    assert server.mcp is not None
    print("[PASS] server FastMCP instance")

    print("\nAll import tests passed.")


def test_scoring_formula():
    """Verify scoring formula edge cases."""
    from pickflow_mcp_server.scoring import score, parse_exposure_items

    # Minimal valid detail
    detail = {
        "price": 0, "review_count": 0, "monthly_sales_volume": 0,
        "days_on_shelf": 0, "gross_profit_rate": 0, "top_category": "",
        "variation_count": 0, "a_plus": False,
    }
    total, tier, scores = score(detail, 0, 0.0)
    assert 0 <= total <= 100, f"Score out of range: {total}"
    assert tier in ("S", "A", "B", "C"), f"Invalid tier: {tier}"
    assert len(scores) == 9, f"Expected 9 dimensions, got {len(scores)}"
    print(f"[PASS] scoring zero case: {total} [{tier}]")

    # High-end case
    detail2 = {
        "price": 29.99, "review_count": 5, "monthly_sales_volume": 2000,
        "days_on_shelf": 60, "gross_profit_rate": 68.0,
        "top_category": "Home & Kitchen (Rank: 15000)",
        "variation_count": 3, "a_plus": True,
    }
    total2, tier2, scores2 = score(detail2, 40, 10.0)
    print(f"[PASS] scoring high case: {total2} [{tier2}]")

    # Edge: days_on_shelf = 0 (new listing)
    detail3 = dict(detail2)
    detail3["days_on_shelf"] = 0
    total3, tier3, _ = score(detail3, 20, 25.0)
    print(f"[PASS] scoring shelf=0 case: {total3} [{tier3}]")


def test_parse_exposure_items():
    """Verify exposure_position parsing."""
    from pickflow_mcp_server.scoring import parse_exposure_items

    items = [
        {"exposure_position": "Ad,Organic", "keyword": "gift"},
        {"exposure_position": "Ad", "keyword": "present"},
        {"exposure_position": "Organic", "keyword": "surprise"},
        {"exposure_position": "Ad,Organic", "keyword": "presente"},
    ]
    count, ad_pct = parse_exposure_items(items)
    assert count == 4
    assert ad_pct == 75.0, f"Expected 75% ad, got {ad_pct}%"
    print(f"[PASS] parse_exposure_items: {count} keywords, {ad_pct}% ad")


def test_cache_crud():
    """Verify cache store/query/clear lifecycle."""
    from pickflow_mcp_server.cache import store_page, query_pool, get_cache_status, clear_cache, _ensure_db

    # Clear first
    clear_cache()

    # Store a test page
    test_kws = [
        {"keyword": "party favors bulk test", "weekly_search_rank": 100, "search_volume_30d": 5000, "page": 1},
        {"keyword": "wedding favors bulk test", "weekly_search_rank": 200, "search_volume_30d": 3000, "page": 1},
        {"keyword": "christmas gifts bulk test", "weekly_search_rank": 300, "search_volume_30d": 8000, "page": 1},
        {"keyword": "toilet paper test", "weekly_search_rank": 400, "search_volume_30d": 999999, "page": 1},
    ]
    stored = store_page(1, test_kws)
    assert stored >= 3, f"Expected >= 3 new, got {stored}"

    # Query
    results = query_pool(categories=["party", "wedding", "christmas"], min_sv=1000, limit=10)
    assert len(results) >= 2, f"Expected >= 2 results, got {len(results)}"
    print(f"[PASS] cache store+query: {len(results)} keywords")

    # toilet paper should NOT match (not in filter words)
    tp_results = query_pool(categories=["toilet"], min_sv=0, limit=10)
    assert len(tp_results) == 0, f"Toilet paper should not match filter"
    print(f"[PASS] filter correctly excludes non-category keywords")

    # Clean up
    clear_cache()
    results = query_pool(min_sv=0, limit=10)
    assert len(results) == 0
    print(f"[PASS] cache clear")


def test_match_keyword():
    """Verify keyword filtering rules."""
    from pickflow_mcp_server.config import match_keyword

    # Valid
    assert match_keyword("party favors bulk")
    assert match_keyword("christmas decorations indoor")
    assert match_keyword("wedding gifts for guests")
    assert match_keyword("gift bags with handles")

    # Invalid - too short
    assert not match_keyword("gift")
    assert not match_keyword("bags")

    # Invalid - blacklist
    assert not match_keyword("roblox gift card")
    assert not match_keyword("amazon gift card")

    # Invalid - no matching category
    assert not match_keyword("toilet paper rolls")
    assert not match_keyword("iphone case")

    print(f"[PASS] match_keyword edge cases")


def test_cache_distribution():
    """Verify term distribution analysis."""
    from pickflow_mcp_server.cache import store_page, term_distribution, clear_cache

    clear_cache()

    # Store keywords across pages
    for page in range(100, 500, 50):
        kws = [{"keyword": f"christmas ornament set {page}", "search_volume_30d": 5000}]
        store_page(page, kws)

    dist = term_distribution("christmas")
    assert dist["total_matches"] >= 4
    print(f"[PASS] term_distribution: {dist['total_matches']} matches across pages {dist['page_range']}")

    clear_cache()


if __name__ == "__main__":
    test_imports()
    test_scoring_formula()
    test_parse_exposure_items()
    test_match_keyword()
    test_cache_crud()
    test_cache_distribution()
    print("\n=== ALL TESTS PASSED ===")
