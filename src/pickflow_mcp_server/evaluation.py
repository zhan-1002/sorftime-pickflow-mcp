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
import re
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
from .scoring import (
    DEFAULT_SCORING_VERSION,
    SUPPORTED_SCORING_VERSIONS,
    evaluate_hard_filters,
    parse_exposure_items,
    score_detailed,
)

SearchFn = Callable[..., Awaitable[dict | None]]
DetailFn = Callable[..., Awaitable[dict | None]]
TrafficFn = Callable[..., Awaitable[TrafficTermsResult]]

SAMPLE_SCHEMA_VERSION = "1.0"
DATASET_SPLITS = {"unassigned", "calibration", "validation", "disputed"}
LABEL_STATUSES = {"unlabeled", "confirmed", "disputed"}
EXPECTED_DISCOVERY = {"unknown", "found", "not_found"}
EXPECTED_HARD_FILTER = {"unknown", "pass", "fail"}
EXPECTED_TIERS = {"unknown", "S", "A", "B", "C"}
EXPECTED_OUTCOMES = {"unknown", "select", "reject", "review"}
MARKETPLACES = {
    "US", "GB", "DE", "FR", "IN", "CA", "JP",
    "ES", "IT", "MX", "AE", "AU", "BR", "SA",
}
_ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")
_SAFE_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _tokens(value: Any) -> list[str]:
    """Split flat CSV tags without exposing row contents in diagnostics."""
    return [
        token.strip().lower()
        for token in re.split(r"[|,;]", str(value or ""))
        if token.strip()
    ]


def _label(record: dict[str, str], key: str, default: str) -> str:
    value = str(record.get(key, "")).strip()
    return value if value else default


def _optional_label(record: dict[str, str], key: str) -> str | None:
    value = str(record.get(key, "")).strip()
    return value if value else None


def validate_sample_records(
    records: list[dict[str, str]],
    *,
    require_v1: bool = False,
) -> dict[str, Any]:
    """Validate private sample metadata and return anonymous aggregate diagnostics.

    Legacy ``asin,keyword`` files remain valid in non-strict mode. V1 adds
    split, annotation status and stage expectations so calibration and held-out
    validation results cannot be mixed accidentally.
    """
    issues: list[dict[str, str]] = []
    split_counts: Counter[str] = Counter()
    label_counts: Counter[str] = Counter()
    outcome_counts: Counter[str] = Counter()
    tag_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    seen_pairs: set[tuple[str, str]] = set()
    labeled_records = 0
    v1_complete_records = 0

    def issue(index: int, level: str, code: str, field: str) -> None:
        issues.append(
            {
                "case_id": f"case-{index + 1:04d}",
                "level": level,
                "code": code,
                "field": field,
            }
        )

    if not records:
        issues.append(
            {
                "case_id": "dataset",
                "level": "error",
                "code": "empty_dataset",
                "field": "rows",
            }
        )

    for index, record in enumerate(records):
        asin = str(record.get("asin", "")).strip().upper()
        keyword = str(record.get("keyword", "")).strip()
        marketplace = _label(record, "marketplace", "US").upper()
        schema_version = _label(record, "schema_version", "legacy")
        dataset_split = _label(record, "dataset_split", "unassigned").lower()
        label_status = _label(record, "label_status", "unlabeled").lower()
        expected_discovery_raw = _optional_label(record, "expected_discovery")
        expected_filter_raw = _optional_label(record, "expected_hard_filter")
        expected_tier_raw = _optional_label(record, "expected_tier")
        expected_outcome_raw = _optional_label(record, "expected_outcome")
        expected_discovery = expected_discovery_raw.lower() if expected_discovery_raw else None
        expected_filter = expected_filter_raw.lower() if expected_filter_raw else None
        expected_tier = (
            expected_tier_raw.upper()
            if expected_tier_raw and expected_tier_raw.lower() != "unknown"
            else expected_tier_raw
        )
        expected_outcome = expected_outcome_raw.lower() if expected_outcome_raw else None

        if not _ASIN_RE.fullmatch(asin):
            issue(index, "error", "invalid_asin", "asin")
        if not keyword:
            issue(index, "warning", "missing_keyword", "keyword")
        if marketplace not in MARKETPLACES:
            issue(index, "error", "unsupported_marketplace", "marketplace")
        if dataset_split not in DATASET_SPLITS:
            issue(index, "error", "invalid_dataset_split", "dataset_split")
            dataset_split = "unassigned"
        if label_status not in LABEL_STATUSES:
            issue(index, "error", "invalid_label_status", "label_status")
            label_status = "unlabeled"
        if expected_discovery is not None and expected_discovery not in EXPECTED_DISCOVERY:
            issue(index, "error", "invalid_expected_discovery", "expected_discovery")
            expected_discovery = None
        if expected_filter is not None and expected_filter not in EXPECTED_HARD_FILTER:
            issue(index, "error", "invalid_expected_hard_filter", "expected_hard_filter")
            expected_filter = None
        if expected_tier is not None and expected_tier not in EXPECTED_TIERS:
            issue(index, "error", "invalid_expected_tier", "expected_tier")
            expected_tier = None
        if expected_outcome is not None and expected_outcome not in EXPECTED_OUTCOMES:
            issue(index, "error", "invalid_expected_outcome", "expected_outcome")
            expected_outcome = None

        pair = (asin, keyword.casefold())
        if pair in seen_pairs:
            issue(index, "error", "duplicate_asin_keyword", "asin,keyword")
        seen_pairs.add(pair)

        expectations = (
            expected_discovery,
            expected_filter,
            expected_tier,
            expected_outcome,
        )
        has_expected_label = any(value is not None for value in expectations)
        labeled_records += int(has_expected_label)
        is_v1_complete = (
            schema_version == SAMPLE_SCHEMA_VERSION
            and dataset_split != "unassigned"
            and label_status != "unlabeled"
            and has_expected_label
        )
        v1_complete_records += int(is_v1_complete)

        if require_v1:
            if schema_version != SAMPLE_SCHEMA_VERSION:
                issue(index, "error", "missing_v1_schema_version", "schema_version")
            if dataset_split == "unassigned":
                issue(index, "error", "missing_dataset_split", "dataset_split")
            if label_status == "unlabeled":
                issue(index, "error", "missing_label_status", "label_status")
            if not has_expected_label:
                issue(index, "error", "missing_expected_label", "expected_*")

        split_counts[dataset_split] += 1
        label_counts[label_status] += 1
        outcome_counts[expected_outcome or "unlabeled"] += 1
        for token in _tokens(record.get("product_tags")):
            if _SAFE_CODE_RE.fullmatch(token):
                tag_counts[token] += 1
            else:
                issue(index, "warning", "invalid_product_tag", "product_tags")
        for token in _tokens(record.get("reason_codes")):
            if _SAFE_CODE_RE.fullmatch(token):
                reason_counts[token] += 1
            else:
                issue(index, "warning", "invalid_reason_code", "reason_codes")

    error_counts = Counter(
        item["code"] for item in issues if item["level"] == "error"
    )
    warning_counts = Counter(
        item["code"] for item in issues if item["level"] == "warning"
    )
    total = len(records)
    profile = {
        "schema_version_expected": SAMPLE_SCHEMA_VERSION,
        "total_records": total,
        "v1_complete_records": v1_complete_records,
        "v1_completeness_pct": round(v1_complete_records / total * 100, 1) if total else 0.0,
        "labeled_records": labeled_records,
        "label_coverage_pct": round(labeled_records / total * 100, 1) if total else 0.0,
        "dataset_splits": dict(sorted(split_counts.items())),
        "label_statuses": dict(sorted(label_counts.items())),
        "expected_outcomes": dict(sorted(outcome_counts.items())),
        "product_tags": dict(sorted(tag_counts.items())),
        "reason_codes": dict(sorted(reason_counts.items())),
        "validation_errors": dict(sorted(error_counts.items())),
        "validation_warnings": dict(sorted(warning_counts.items())),
        "strict_ready": not error_counts and v1_complete_records == total,
    }
    return {"profile": profile, "issues": issues}


def _stage_agreement(
    cases: list[dict[str, Any]],
    records: list[dict[str, str]],
) -> dict[str, Any]:
    """Compare only explicit stage labels; unknown/unavailable cases are excluded."""
    definitions = {
        "discovery": ("expected_discovery", "observed_discovery"),
        "hard_filter": ("expected_hard_filter", "hard_filter_status"),
        "tier": ("expected_tier", "tier"),
    }
    metrics: dict[str, Any] = {}
    for name, (expected_key, observed_key) in definitions.items():
        compared = 0
        matched = 0
        unavailable = 0
        for record, case in zip(records, cases):
            expected = _optional_label(record, expected_key)
            if expected is None:
                continue
            expected = expected.upper() if expected_key == "expected_tier" and expected.lower() != "unknown" else expected.lower()
            observed = case.get(observed_key)
            if observed in (None, "not_run", "unavailable"):
                unavailable += 1
                continue
            compared += 1
            matched += int(str(observed).lower() == str(expected).lower())
        metrics[name] = {
            "labeled_cases": compared + unavailable,
            "compared_cases": compared,
            "unavailable_cases": unavailable,
            "matched_cases": matched,
            "agreement_pct": round(matched / compared * 100, 1) if compared else None,
        }
    return metrics


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

    dataset_validation = validate_sample_records(records)
    semaphore = asyncio.Semaphore(max(1, min(concurrency, 10)))

    async def evaluate_one(index: int, record: dict[str, str]) -> dict[str, Any]:
        asin = str(record.get("asin", "")).strip()
        keyword = str(record.get("keyword", "")).strip()
        marketplace = _label(record, "marketplace", "US").upper()
        case: dict[str, Any] = {
            "case_id": f"case-{index + 1:04d}",
            "discovery_status": "not_run",
            "search_rank": None,
            "detail_status": "not_run",
            "traffic_status": "not_run",
            "hard_filter_status": "not_run",
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
                            amz_site=marketplace,
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

            if case["discovery_status"] == "found":
                case["observed_discovery"] = "found"
            elif case["discovery_status"] == "not_in_search_results":
                case["observed_discovery"] = "not_found"
            else:
                case["observed_discovery"] = "unavailable"

            try:
                detail_result = (
                    await detail_fn(asin)
                    if marketplace == "US"
                    else await detail_fn(asin, site=marketplace)
                )
                detail = detail_result.get("data", {}) if detail_result and not detail_result.get("_error") else None
                if not isinstance(detail, dict) or not detail:
                    detail = None
            except Exception:
                detail = None
            if detail is None:
                case["detail_status"] = "detail_error"
                return case
            case["detail_status"] = "available"
            case["hard_filter_status"] = evaluate_hard_filters(detail)["status"]

            try:
                traffic = await traffic_fn(
                    asin, max_pages=traffic_pages, site=marketplace
                )
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
        "dataset_profile": dataset_validation["profile"],
        "stage_agreement": _stage_agreement(cases, records),
        "cache_pool": _cache_snapshot(
            [str(record.get("keyword", "")).strip() for record in records], cache_db
        ),
    }
    return {
        "summary": summary,
        "cases": cases,
        "dataset_issues": dataset_validation["issues"],
    }


def _load_records(path: Path, limit: int) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not {"asin", "keyword"}.issubset(reader.fieldnames or []):
            raise ValueError("evaluation CSV must contain asin and keyword columns")
        records = list(reader)
    if not records:
        raise ValueError("evaluation CSV contains no sample rows")
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
    parser.add_argument(
        "--require-v1-labels",
        action="store_true",
        help="fail before API calls unless every row has complete V1 sample labels",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="validate and profile the private CSV without making API calls",
    )
    parser.add_argument("--output", type=Path, help="optional private JSON with anonymous per-case diagnostics")
    args = parser.parse_args()

    if args.data_dir is None:
        parser.error("pass --data-dir or set PICKFLOW_TEST_DATA_DIR")
    input_path = args.data_dir / "test_set_parsed.csv"
    if not input_path.is_file():
        parser.error(f"missing {input_path}")

    records = _load_records(input_path, args.limit)
    validation = validate_sample_records(records, require_v1=args.require_v1_labels)
    if args.validate_only:
        output = {"dataset_profile": validation["profile"], "dataset_issues": validation["issues"]}
        print(json.dumps(output["dataset_profile"], ensure_ascii=False, indent=2))
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
        if validation["profile"]["validation_errors"]:
            raise SystemExit(2)
        return
    if args.require_v1_labels and not validation["profile"]["strict_ready"]:
        parser.error(
            "sample set is not V1-ready; run --validate-only --output in the private data directory"
        )
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
