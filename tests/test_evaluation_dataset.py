"""Synthetic coverage for the private sample-set V1 contract."""

from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory


def _detail() -> dict:
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


def test_legacy_two_column_sample_remains_supported() -> None:
    from pickflow_mcp_server.evaluation import validate_sample_records

    result = validate_sample_records(
        [{"asin": "B012345678", "keyword": "synthetic gift"}]
    )

    profile = result["profile"]
    assert profile["total_records"] == 1
    assert profile["labeled_records"] == 0
    assert profile["v1_complete_records"] == 0
    assert profile["validation_errors"] == {}
    assert profile["strict_ready"] is False


def test_complete_v1_sample_reports_anonymous_profile() -> None:
    from pickflow_mcp_server.evaluation import validate_sample_records

    result = validate_sample_records(
        [
            {
                "asin": "B012345678",
                "keyword": "synthetic gift",
                "schema_version": "1.0",
                "marketplace": "US",
                "dataset_split": "validation",
                "label_status": "confirmed",
                "expected_discovery": "found",
                "expected_hard_filter": "pass",
                "expected_tier": "A",
                "expected_outcome": "select",
                "product_tags": "bulk|gift",
                "reason_codes": "historical_winner|supplier_ready",
            }
        ],
        require_v1=True,
    )

    profile = result["profile"]
    assert profile["strict_ready"] is True
    assert profile["v1_completeness_pct"] == 100.0
    assert profile["dataset_splits"] == {"validation": 1}
    assert profile["product_tags"] == {"bulk": 1, "gift": 1}
    assert profile["expected_outcomes"] == {"select": 1}


def test_validation_issues_never_include_private_identifiers() -> None:
    from pickflow_mcp_server.evaluation import validate_sample_records

    private_asin = "B0ABCDEFGH"
    private_keyword = "private synthetic phrase"
    result = validate_sample_records(
        [
            {
                "asin": private_asin,
                "keyword": private_keyword,
                "schema_version": "0.5",
                "dataset_split": "training",
                "label_status": "yes",
                "expected_outcome": "maybe",
                "reason_codes": private_keyword,
            }
        ],
        require_v1=True,
    )

    serialized = repr(result)
    assert private_asin not in serialized
    assert private_keyword not in serialized
    assert result["issues"]
    assert all(issue["case_id"] == "case-0001" for issue in result["issues"])


def test_empty_dataset_is_not_strict_ready() -> None:
    from pickflow_mcp_server.evaluation import validate_sample_records

    result = validate_sample_records([], require_v1=True)

    assert result["profile"]["strict_ready"] is False
    assert result["profile"]["validation_errors"] == {"empty_dataset": 1}


def test_strict_mode_identifies_unlabeled_legacy_rows() -> None:
    from pickflow_mcp_server.evaluation import validate_sample_records

    result = validate_sample_records(
        [{"asin": "B012345678", "keyword": "synthetic gift"}],
        require_v1=True,
    )

    errors = result["profile"]["validation_errors"]
    assert errors == {
        "missing_dataset_split": 1,
        "missing_expected_label": 1,
        "missing_label_status": 1,
        "missing_v1_schema_version": 1,
    }


def test_explicit_unknown_is_a_real_stage_label() -> None:
    from pickflow_mcp_server.evaluation import validate_sample_records

    result = validate_sample_records(
        [
            {
                "asin": "B012345678",
                "keyword": "synthetic gift",
                "schema_version": "1.0",
                "dataset_split": "disputed",
                "label_status": "disputed",
                "expected_hard_filter": "unknown",
            }
        ],
        require_v1=True,
    )

    assert result["profile"]["labeled_records"] == 1
    assert result["profile"]["v1_complete_records"] == 1
    assert result["profile"]["strict_ready"] is True


def test_evaluator_reports_stage_agreement_without_identifiers() -> None:
    from pickflow_mcp_server.api import TrafficTermsResult
    from pickflow_mcp_server.evaluation import evaluate_records
    from pickflow_mcp_server.scoring import score_detailed

    expected_tier = score_detailed(_detail(), 1, 0.0).tier
    records = [
        {
            "asin": "B012345678",
            "keyword": "synthetic gift",
            "schema_version": "1.0",
            "dataset_split": "validation",
            "label_status": "confirmed",
            "expected_discovery": "found",
            "expected_hard_filter": "pass",
            "expected_tier": expected_tier,
            "expected_outcome": "select",
        }
    ]

    async def fake_search(keyword, **kwargs):
        return {"data": [{"asin": "B012345678"}]}

    async def fake_detail(asin):
        return {"data": _detail()}

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
                search_fn=fake_search,
                detail_fn=fake_detail,
                traffic_fn=fake_traffic,
                cache_db=Path(temp_dir) / "missing.db",
            )
        )

    agreement = result["summary"]["stage_agreement"]
    assert agreement["discovery"]["agreement_pct"] == 100.0
    assert agreement["hard_filter"]["agreement_pct"] == 100.0
    assert agreement["tier"]["agreement_pct"] == 100.0
    assert "B012345678" not in repr(result)


def test_evaluator_propagates_non_us_marketplace() -> None:
    from pickflow_mcp_server.api import TrafficTermsResult
    from pickflow_mcp_server.evaluation import evaluate_records

    observed: dict[str, str] = {}
    records = [{"asin": "B012345678", "keyword": "synthetic", "marketplace": "DE"}]

    async def fake_search(keyword, **kwargs):
        observed["search"] = kwargs["amz_site"]
        return {"data": []}

    async def fake_detail(asin, site="US"):
        observed["detail"] = site
        return {"data": _detail()}

    async def fake_traffic(asin, max_pages=2, site="US"):
        observed["traffic"] = site
        return TrafficTermsResult([], 1, 1, [], True, 0)

    with TemporaryDirectory() as temp_dir:
        asyncio.run(
            evaluate_records(
                records,
                search_fn=fake_search,
                detail_fn=fake_detail,
                traffic_fn=fake_traffic,
                cache_db=Path(temp_dir) / "missing.db",
            )
        )

    assert observed == {"search": "DE", "detail": "DE", "traffic": "DE"}
