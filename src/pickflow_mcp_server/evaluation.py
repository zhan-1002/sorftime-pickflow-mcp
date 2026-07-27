"""Private, local evaluation runner for known-good ASIN datasets.

The evaluator never ships a dataset with the package and prints aggregate data
only. Per-case diagnostics use row identifiers (``case-0001``) rather than ASINs
or keywords, so normal test output is safe to share.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sqlite3
from collections import Counter
from pathlib import Path
from statistics import mean
from typing import Any, Awaitable, Callable

from .api import (
    TrafficTermsResult,
    is_no_data_result,
    product_detail,
    product_search,
    product_traffic_terms_all,
)
from .config import CACHE_DB
from .scoring import DEFAULT_SCORING_VERSION, SUPPORTED_SCORING_VERSIONS, parse_exposure_items, score_detailed

SearchFn = Callable[..., Awaitable[dict | None]]
DetailFn = Callable[..., Awaitable[dict | None]]
TrafficFn = Callable[..., Awaitable[TrafficTermsResult]]


def _list_data(result: dict | None) -> list[dict] | None:
    if is_no_data_result(result):
        return []
    if result is None or result.get("_error"):
        return None
    data = result.get("data", [])
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("items", "list", "rows", "data"):
            nested = data.get(key)
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]
        return [data] if data else []
    return []


def _cache_snapshot(keywords: list[str], cache_db: Path = CACHE_DB) -> dict[str, Any]:
    """Read cache coverage without creating or mutating the cache database."""
    usable = [keyword for keyword in keywords if keyword]
    if not cache_db.exists():
        return {"status": "missing", "cached_keywords": 0, "covered_cases": 0}
    try:
        connection = sqlite3.connect(f"file:{cache_db}?mode=ro", uri=True)
        total = connection.execute("SELECT COUNT(*) FROM aba_keywords").fetchone()[0]
        covered = 0
        for keyword in usable:
            row = connection.execute(
                "SELECT 1 FROM aba_keywords WHERE LOWER(keyword) = LOWER(?) LIMIT 1",
                (keyword,),
            ).fetchone()
            covered += int(row is not None)
        connection.close()
        return {
            "status": "available" if total else "empty",
            "cached_keywords": total,
            "covered_cases": covered,
            "coverage_pct": round(covered / len(usable) * 100, 1) if usable else 0.0,
        }
    except (sqlite3.Error, OSError) as exc:
        return {
            "status": "unreadable",
            "cached_keywords": 0,
            "covered_cases": 0,
            "error": type(exc).__name__,
        }


async def evaluate_records(
    records: list[dict[str, str]],
    *,
    search_pages: int = 2,
    traffic_pages: int = 2,
    concurrency: int = 3,
    scoring_version: str = DEFAULT_SCORING_VERSION,
    search_fn: SearchFn = product_search,
    detail_fn: DetailFn = product_detail,
    traffic_fn: TrafficFn = product_traffic_terms_all,
    cache_db: Path = CACHE_DB,
) -> dict[str, Any]:
    """Evaluate discovery recall, API failure stages and scoring data quality."""
    if scoring_version not in SUPPORTED_SCORING_VERSIONS:
        raise ValueError(f"unsupported scoring version: {scoring_version}")
    if search_pages < 1 or traffic_pages < 1:
        raise ValueError("search_pages and traffic_pages must be at least 1")

    semaphore = asyncio.Semaphore(max(1, min(concurrency, 10)))

    async def evaluate_one(index: int, record: dict[str, str]) -> dict[str, Any]:
        asin = str(record.get("asin", "")).strip()
        keyword = str(record.get("keyword", "")).strip()
        case: dict[str, Any] = {
            "case_id": f"case-{index + 1:04d}",
            "discovery_status": "not_run",
            "search_rank": None,
            "detail_status": "not_run",
            "traffic_status": "not_run",
            "score_status": "not_run",
        }
        if not asin:
            case["discovery_status"] = "invalid_asin"
            return case

        async with semaphore:
            if not keyword:
                case["discovery_status"] = "no_keyword"
            else:
                rank_offset = 0
                for page in range(1, search_pages + 1):
                    try:
                        search_result = await search_fn(
                            keyword,
                            price_min=0,
                            price_max=9999,
                            ratings_count_max=9999999,
                            month_sales_volume_min=0,
                            delivery_type="Both",
                            amz_site="US",
                            page=page,
                        )
                        items = _list_data(search_result)
                    except Exception:
                        items = None
                    if items is None:
                        case["discovery_status"] = "search_error"
                        break
                    match_rank = next(
                        (
                            rank_offset + item_index + 1
                            for item_index, item in enumerate(items)
                            if str(item.get("asin", "")).strip().upper() == asin.upper()
                        ),
                        None,
                    )
                    if match_rank is not None:
                        case["search_rank"] = match_rank
                        case["discovery_status"] = "found"
                        break
                    rank_offset += len(items)
                    if not items:
                        break
                if case["discovery_status"] == "not_run":
                    case["discovery_status"] = "not_in_search_results"

            try:
                detail_result = await detail_fn(asin)
                detail = detail_result.get("data", {}) if detail_result and not detail_result.get("_error") else None
                if not isinstance(detail, dict) or not detail:
                    detail = None
            except Exception:
                detail = None
            if detail is None:
                case["detail_status"] = "detail_error"
                return case
            case["detail_status"] = "available"

            try:
                traffic = await traffic_fn(asin, max_pages=traffic_pages, site="US")
            except Exception:
                traffic = TrafficTermsResult([], 0, 0, [{"page": 0, "error": "exception"}], False, 0)

            if traffic.available:
                traffic_count, ad_pct = parse_exposure_items(traffic.items)
                case["traffic_status"] = "complete" if traffic.complete else "partial"
            else:
                traffic_count, ad_pct = None, None
                case["traffic_status"] = "unavailable"

            scored = score_detailed(detail, traffic_count, ad_pct, scoring_version)
            case.update(
                {
                    "score_status": "scored",
                    "tier": scored.tier,
                    "total_score": scored.total_score,
                    "data_completeness": scored.data_completeness,
                    "score_confidence": scored.score_confidence,
                    "missing_dimension_count": len(scored.missing_dimensions),
                }
            )
            return case

    cases = await asyncio.gather(
        *(evaluate_one(index, record) for index, record in enumerate(records))
    )
    discovery_counts = Counter(case["discovery_status"] for case in cases)
    detail_counts = Counter(case["detail_status"] for case in cases)
    traffic_counts = Counter(case["traffic_status"] for case in cases)
    scored_cases = [case for case in cases if case["score_status"] == "scored"]
    ranks = [case["search_rank"] for case in cases if case["search_rank"] is not None]
    tiers = Counter(case["tier"] for case in scored_cases)
    total = len(cases)

    summary = {
        "total_cases": total,
        "scoring_version": scoring_version,
        "search_pages": search_pages,
        "traffic_pages": traffic_pages,
        "discovery_stages": dict(sorted(discovery_counts.items())),
        "detail_stages": dict(sorted(detail_counts.items())),
        "traffic_stages": dict(sorted(traffic_counts.items())),
        "found_cases": len(ranks),
        "recall_pct": round(len(ranks) / total * 100, 1) if total else 0.0,
        "recall_at_20_pct": round(sum(rank <= 20 for rank in ranks) / total * 100, 1) if total else 0.0,
        "recall_at_40_pct": round(sum(rank <= 40 for rank in ranks) / total * 100, 1) if total else 0.0,
        "recall_at_100_pct": round(sum(rank <= 100 for rank in ranks) / total * 100, 1) if total else 0.0,
        "scored_cases": len(scored_cases),
        "tier_distribution": dict(sorted(tiers.items())),
        "average_data_completeness": round(mean(case["data_completeness"] for case in scored_cases), 1) if scored_cases else 0.0,
        "average_score_confidence": round(mean(case["score_confidence"] for case in scored_cases), 1) if scored_cases else 0.0,
        "cache_pool": _cache_snapshot(
            [str(record.get("keyword", "")).strip() for record in records], cache_db
        ),
    }
    return {"summary": summary, "cases": cases}


def _load_records(path: Path, limit: int) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        records = list(csv.DictReader(handle))
    if not {"asin", "keyword"}.issubset(records[0].keys() if records else set()):
        raise ValueError("evaluation CSV must contain asin and keyword columns")
    return records[:limit] if limit > 0 else records


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a private PickFlow evaluation")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.environ["PICKFLOW_TEST_DATA_DIR"]) if os.environ.get("PICKFLOW_TEST_DATA_DIR") else None,
        help="private directory containing test_set_parsed.csv",
    )
    parser.add_argument("--limit", type=int, default=5, help="cases to run; 0 means all")
    parser.add_argument("--search-pages", type=int, default=2)
    parser.add_argument("--traffic-pages", type=int, default=2)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--scoring-version", choices=SUPPORTED_SCORING_VERSIONS, default=DEFAULT_SCORING_VERSION)
    parser.add_argument("--output", type=Path, help="optional private JSON with anonymous per-case diagnostics")
    args = parser.parse_args()

    if args.data_dir is None:
        parser.error("pass --data-dir or set PICKFLOW_TEST_DATA_DIR")
    input_path = args.data_dir / "test_set_parsed.csv"
    if not input_path.is_file():
        parser.error(f"missing {input_path}")

    records = _load_records(input_path, args.limit)
    result = asyncio.run(
        evaluate_records(
            records,
            search_pages=args.search_pages,
            traffic_pages=args.traffic_pages,
            concurrency=args.concurrency,
            scoring_version=args.scoring_version,
        )
    )
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
