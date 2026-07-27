"""Regression coverage for fixed MCP tool runtime and cache contracts."""
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory


def test_asin_score_returns_weights():
    """asin_score should complete successfully and expose scoring weights."""
    from pickflow_mcp_server import server
    from pickflow_mcp_server.api import TrafficTermsResult
    from pickflow_mcp_server.scoring import WEIGHTS, score

    async def fake_detail(asin):
        return {
            "data": {
                "title": "Regression probe",
                "price": 29.99,
                "review_count": 10,
                "monthly_sales_volume": 300,
                "days_on_shelf": 30,
                "gross_profit_rate": 55,
                "top_category": "Home & Kitchen (Rank: 10000)",
                "variation_count": 1,
                "a_plus": True,
                "fba_fee": 5,
            }
        }

    async def fake_traffic(asin, max_pages=2):
        return TrafficTermsResult(
            items=[{"keyword": "regression", "exposure_position": "Organic"}],
            pages_requested=1,
            pages_succeeded=1,
            page_errors=[],
            complete=True,
            duplicates_removed=0,
        )

    original_detail = server.product_detail
    original_traffic = server.product_traffic_terms_all
    server.product_detail = fake_detail
    server.product_traffic_terms_all = fake_traffic
    try:
        result = asyncio.run(server.asin_score("TEST-ASIN"))
    finally:
        server.product_detail = original_detail
        server.product_traffic_terms_all = original_traffic

    assert "error" not in result
    assert result["weights_used"] == {k: round(v, 1) for k, v in WEIGHTS.items()}

    # Sales velocity is based on the monthly estimate, not listing age.
    recent = fake_detail("TEST-ASIN")
    recent_data = asyncio.run(recent)["data"]
    established_data = dict(recent_data, days_on_shelf=300)
    recent_velocity = score(recent_data, 1, 0.0)[2]["sales_velocity"]
    established_velocity = score(established_data, 1, 0.0)[2]["sales_velocity"]
    assert recent_velocity == established_velocity


def test_market_screen_cache_contract_is_stable():
    """A cache hit should return the same market fields and values as a miss."""
    from pickflow_mcp_server import cache, server

    async def fake_keyword_detail(keyword):
        return {
            "data": {
                "monthly_search_volume": 12000,
                "recommended_cpc_bid": 0.75,
                "search_result_competitor_count": 50000,
                "search_volume_peak_season": "Nov-Dec",
                "search_result_first_page_stats": (
                    "Organic review-count below 100/300/500: 40%/60%/80%; "
                    "Organic non-Best-Seller share: 55%"
                ),
            }
        }

    original_dir = cache.CACHE_DIR
    original_db = cache.CACHE_DB
    original_keyword_detail = server.keyword_detail

    with TemporaryDirectory() as temp_dir:
        cache.CACHE_DIR = Path(temp_dir)
        cache.CACHE_DB = cache.CACHE_DIR / "test_cache.db"
        server.keyword_detail = fake_keyword_detail
        try:
            pool = json.dumps([
                {"keyword": "party favors bulk", "search_volume_30d": 12000}
            ])
            first = asyncio.run(
                server.market_screen(pool, limit=1, min_search_volume=0)
            )["top_markets"][0]
            second = asyncio.run(
                server.market_screen(pool, limit=1, min_search_volume=0)
            )["top_markets"][0]
        finally:
            server.keyword_detail = original_keyword_detail
            cache.CACHE_DIR = original_dir
            cache.CACHE_DB = original_db

    assert first["cached"] is False
    assert second["cached"] is True
    assert set(first) == set(second)
    assert {k: v for k, v in first.items() if k != "cached"} == {
        k: v for k, v in second.items() if k != "cached"
    }
