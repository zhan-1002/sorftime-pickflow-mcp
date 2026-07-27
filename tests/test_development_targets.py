"""Coverage for scoring semantics, traffic paging and private evaluation."""

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory


def _complete_detail() -> dict:
    return {
        "price": 29.99,
        "review_count": 20,
        "monthly_sales_volume": 300,
        "days_on_shelf": 30,
        "gross_profit_rate": 55,
        "top_category": "Home & Kitchen (Rank: 10000)",
        "variation_count": 2,
        "a_plus": True,
        "star_rating": 4.4,
        "fba_fee": 5.0,
    }


def test_scoring_versions_preserve_legacy_and_fix_monthly_semantics():
    from pickflow_mcp_server.scoring import score_detailed

    recent = _complete_detail()
    established = dict(recent, days_on_shelf=300)

    legacy_recent = score_detailed(recent, 10, 20.0, "v1_legacy")
    legacy_established = score_detailed(established, 10, 20.0, "v1_legacy")
    semantic_recent = score_detailed(recent, 10, 20.0, "v2_semantic")
    semantic_established = score_detailed(established, 10, 20.0, "v2_semantic")

    assert legacy_recent.dimensions["sales_velocity"] != legacy_established.dimensions["sales_velocity"]
    assert semantic_recent.dimensions["sales_velocity"] == semantic_established.dimensions["sales_velocity"]
    assert legacy_recent.scoring_version == "v1_legacy"
    assert semantic_recent.scoring_version == "v2_semantic"


def test_missing_data_is_not_converted_to_zero():
    from pickflow_mcp_server.scoring import score_detailed

    result = score_detailed({"price": 29.99}, None, None)

    assert result.dimensions["organic_health"] is None
    assert result.dimensions["traffic_breadth"] is None
    assert "sales_velocity" in result.missing_dimensions
    assert result.data_completeness < 50
    assert result.score_confidence < 50


def test_hard_filters_have_pass_fail_unknown_states():
    from pickflow_mcp_server.scoring import evaluate_hard_filters

    unknown = evaluate_hard_filters({})
    failed = evaluate_hard_filters({"price": 8, "monthly_sales_volume": 0, "fba_fee": 4, "review_count": 2})
    passed = evaluate_hard_filters(_complete_detail())

    assert unknown["status"] == "unknown"
    assert failed["status"] == "fail"
    assert set(failed["failed_filters"]) == {"price_too_low", "zero_sales"}
    assert passed["status"] == "pass"


def test_traffic_pages_are_deduplicated():
    from pickflow_mcp_server import api

    async def fake_page(asin, page=1, site="US"):
        pages = {
            1: {"data": [
                {"keyword": "Party Favor", "exposure_position": "Organic"},
                {"keyword": "Gift Bag", "exposure_position": "Ad"},
            ]},
            2: {"data": [
                {"keyword": "party favor", "exposure_position": "Organic"},
                {"keyword": "Wedding Favor", "exposure_position": "Organic"},
            ]},
        }
        return pages[page]

    original = api.product_traffic_terms
    api.product_traffic_terms = fake_page
    try:
        result = asyncio.run(api.product_traffic_terms_all("TEST", max_pages=2))
    finally:
        api.product_traffic_terms = original

    assert len(result.items) == 3
    assert result.duplicates_removed == 1
    assert result.pages_succeeded == 2
    assert result.complete is True


def test_traffic_failure_is_unavailable_not_empty():
    from pickflow_mcp_server import api

    async def failing_page(asin, page=1, site="US"):
        raise TimeoutError("synthetic timeout")

    original = api.product_traffic_terms
    api.product_traffic_terms = failing_page
    try:
        result = asyncio.run(api.product_traffic_terms_all("TEST", max_pages=2))
    finally:
        api.product_traffic_terms = original

    assert result.available is False
    assert result.items == []
    assert result.complete is False
    assert len(result.page_errors) == 2


def test_evaluator_reports_failure_stages_without_identifiers():
    from pickflow_mcp_server.api import TrafficTermsResult
    from pickflow_mcp_server.evaluation import evaluate_records

    records = [
        {"asin": "TARGET-A", "keyword": "found keyword"},
        {"asin": "TARGET-B", "keyword": "missing keyword"},
        {"asin": "TARGET-C", "keyword": ""},
    ]

    async def fake_search(keyword, **kwargs):
        if keyword == "found keyword" and kwargs["page"] == 1:
            return {"data": [{"asin": "TARGET-A"}]}
        return {"data": []}

    async def fake_detail(asin):
        return {"data": _complete_detail()}

    async def fake_traffic(asin, max_pages=2, site="US"):
        return TrafficTermsResult(
            [{"keyword": "traffic", "exposure_position": "Organic"}],
            1,
            1,
            [],
            True,
            0,
        )

    with TemporaryDirectory() as temp_dir:
        result = asyncio.run(
            evaluate_records(
                records,
                search_pages=2,
                traffic_pages=2,
                search_fn=fake_search,
                detail_fn=fake_detail,
                traffic_fn=fake_traffic,
                cache_db=Path(temp_dir) / "missing.db",
            )
        )

    assert result["summary"]["found_cases"] == 1
    assert result["summary"]["discovery_stages"] == {
        "found": 1,
        "no_keyword": 1,
        "not_in_search_results": 1,
    }
    assert result["summary"]["scored_cases"] == 3
    assert all("asin" not in case and "keyword" not in case for case in result["cases"])


def test_sse_parser_handles_standard_json_rpc_payload():
    import json

    from pickflow_mcp_server.api import _parse_mcp_response

    inner = json.dumps({"data": [{"keyword": "synthetic"}]})
    outer = json.dumps({
        "result": {"content": [{"type": "text", "text": inner}]}
    })
    assert _parse_mcp_response(f"event: message\ndata: {outer}\n") == {
        "data": [{"keyword": "synthetic"}]
    }


def test_sse_parser_structures_non_json_tool_text():
    import json

    from pickflow_mcp_server.api import _parse_mcp_response

    outer = json.dumps({
        "result": {"content": [{"type": "text", "text": "No more data"}]}
    })
    result = _parse_mcp_response(f"data: {outer}\n")

    assert result == {
        "_error": {"code": "NON_JSON_TEXT", "message": "No more data"}
    }


def test_plain_text_no_data_ends_traffic_pagination_normally():
    from pickflow_mcp_server import api

    async def fake_page(asin, page=1, site="US"):
        if page == 1:
            return {"data": [{"keyword": "only page"}]}
        return {
            "_error": {"code": "NON_JSON_TEXT", "message": "No relevant data."}
        }

    original = api.product_traffic_terms
    api.product_traffic_terms = fake_page
    try:
        result = asyncio.run(api.product_traffic_terms_all("TEST", max_pages=2))
    finally:
        api.product_traffic_terms = original

    assert len(result.items) == 1
    assert result.pages_succeeded == 2
    assert result.page_errors == []
    assert result.complete is True

    assert api.is_no_data_result({
        "_error": {
            "code": "NON_JSON_TEXT",
            "message": "No related products found.",
        }
    })
