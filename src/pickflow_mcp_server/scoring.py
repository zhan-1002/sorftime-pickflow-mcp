"""Nine-dimension ASIN scoring with explicit version and data quality metadata."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Literal

SCORING_VERSION_LEGACY = "v1_legacy"
SCORING_VERSION_SEMANTIC = "v2_semantic"
DEFAULT_SCORING_VERSION = SCORING_VERSION_SEMANTIC
SUPPORTED_SCORING_VERSIONS = (SCORING_VERSION_LEGACY, SCORING_VERSION_SEMANTIC)

WEIGHTS = {
    "sales_velocity": 2.0,
    "profit": 1.5,
    "entry_barrier": 1.5,
    "organic_health": 1.2,
    "growth_potential": 1.2,
    "price_sweetspot": 1.0,
    "traffic_breadth": 1.0,
    "competitive_position": 0.8,
    "listing_quality": 0.5,
}

TIERS = {"S": 70, "A": 55, "B": 40, "C": 0}
FilterState = Literal["pass", "fail", "unknown"]


@dataclass(frozen=True)
class ScoreResult:
    """A score plus enough metadata to judge whether it is trustworthy."""

    total_score: float
    tier: str
    dimensions: dict[str, float | None]
    scoring_version: str
    data_completeness: float
    score_confidence: float
    missing_dimensions: list[str]
    warnings: list[str]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_bsr(bsr_text: str) -> int | None:
    """Extract BSR from strings such as ``Home & Kitchen (Rank: 168100)``."""
    match = re.search(r"Rank:\s*([\d,]+)", str(bsr_text))
    return int(match.group(1).replace(",", "")) if match else None


def parse_exposure_items(items: list[dict]) -> tuple[int, float]:
    """Count traffic keywords and ad dependency from successful traffic data.

    An empty list means a successful response with no traffic terms. Callers that
    could not retrieve traffic data must pass ``None`` to :func:`score_detailed`
    instead of converting a transport failure to an empty list.
    """
    if not items:
        return 0, 0.0
    total = len(items)
    ad_count = sum(
        1 for item in items if "ad" in str(item.get("exposure_position", "")).lower()
    )
    return total, round(ad_count / total * 100, 1)


def _present(data: dict, key: str) -> bool:
    return key in data and data[key] is not None and str(data[key]).strip() != ""


def _number(data: dict, key: str, cast: type[int] | type[float]) -> int | float | None:
    if not _present(data, key):
        return None
    try:
        return cast(data[key])
    except (TypeError, ValueError):
        return None


def _filter_result(state: FilterState, reason: str) -> dict[str, str]:
    return {"state": state, "reason": reason}


def evaluate_hard_filters(detail_data: dict) -> dict[str, Any]:
    """Evaluate hard filters without confusing missing fields with real zeros."""
    price = _number(detail_data, "price", float)
    sales = _number(detail_data, "monthly_sales_volume", int)
    fee = _number(detail_data, "fba_fee", float)
    reviews = _number(detail_data, "review_count", int)
    stars = _number(detail_data, "star_rating", float)
    fulfillment = str(
        detail_data.get("delivery_type")
        or detail_data.get("fulfillment_type")
        or ""
    ).upper()

    filters: dict[str, dict[str, str]] = {}
    if price is None or price <= 0:
        filters["price_too_low"] = _filter_result("unknown", "price is missing or invalid")
    elif price < 10:
        filters["price_too_low"] = _filter_result("fail", "price is below $10")
    else:
        filters["price_too_low"] = _filter_result("pass", "price is at least $10")

    if sales is None:
        filters["zero_sales"] = _filter_result("unknown", "monthly sales is missing")
    elif sales <= 0:
        filters["zero_sales"] = _filter_result("fail", "monthly sales is zero")
    else:
        filters["zero_sales"] = _filter_result("pass", "monthly sales is positive")

    if fulfillment == "FBM" or "MERCHANT" in fulfillment:
        filters["not_fba"] = _filter_result("fail", "fulfillment is explicitly merchant fulfilled")
    elif fee is not None and fee > 0:
        filters["not_fba"] = _filter_result("pass", "positive FBA fee is present")
    else:
        filters["not_fba"] = _filter_result("unknown", "FBA status cannot be confirmed")

    if reviews is None:
        filters["review_velocity_trap"] = _filter_result("unknown", "review count is missing")
    elif reviews <= 500:
        filters["review_velocity_trap"] = _filter_result("pass", "review count is not above 500")
    elif stars is None or stars <= 0:
        filters["review_velocity_trap"] = _filter_result("unknown", "star rating is missing")
    elif stars < 3.8:
        filters["review_velocity_trap"] = _filter_result(
            "fail", "reviews exceed 500 while star rating is below 3.8"
        )
    else:
        filters["review_velocity_trap"] = _filter_result("pass", "rating avoids the review trap")

    states = [result["state"] for result in filters.values()]
    status: FilterState = "fail" if "fail" in states else "unknown" if "unknown" in states else "pass"
    return {
        "status": status,
        "filters": filters,
        "failed_filters": [name for name, result in filters.items() if result["state"] == "fail"],
        "unknown_filters": [name for name, result in filters.items() if result["state"] == "unknown"],
    }


def check_hard_filters(detail_data: dict) -> tuple[bool, list[str]]:
    """Compatibility wrapper returning ``(definite_pass, failed_descriptions)``."""
    evaluation = evaluate_hard_filters(detail_data)
    failed = [
        f"{name}: {evaluation['filters'][name]['reason']}"
        for name in evaluation["failed_filters"]
    ]
    return evaluation["status"] == "pass", failed


def _dimension_scores(
    detail_data: dict,
    traffic_count: int | None,
    ad_pct: float | None,
    scoring_version: str,
) -> tuple[dict[str, float | None], list[str]]:
    legacy = scoring_version == SCORING_VERSION_LEGACY
    data = detail_data
    price = _number(data, "price", float)
    reviews = _number(data, "review_count", int)
    sales = _number(data, "monthly_sales_volume", int)
    shelf = _number(data, "days_on_shelf", int)
    profit_rate = _number(data, "gross_profit_rate", float)
    bsr_num = parse_bsr(data.get("top_category", ""))
    variations = _number(data, "variation_count", int)
    aplus_present = _present(data, "a_plus")
    aplus = bool(data.get("a_plus", False))
    warnings: list[str] = []

    if legacy:
        price = price or 0.0
        reviews = reviews or 0
        sales = sales or 0
        shelf = shelf or 0
        profit_rate = profit_rate or 0.0
        variations = variations or 0
        traffic_count = traffic_count or 0
        ad_pct = ad_pct or 0.0
        daily = sales / max(shelf, 1)
    else:
        daily = sales / 30 if sales is not None and sales >= 0 else None

    scores: dict[str, float | None] = {name: None for name in WEIGHTS}

    if daily is not None:
        scores["sales_velocity"] = 10 if daily >= 10 else 8 if daily >= 5 else 6 if daily >= 2 else 4 if daily >= 1 else 2 if daily >= 0.5 else 1

    if profit_rate is not None and (legacy or profit_rate > 0):
        scores["profit"] = 10 if profit_rate >= 70 else 8 if profit_rate >= 60 else 6 if profit_rate >= 50 else 4 if profit_rate >= 40 else 2

    if reviews is not None:
        scores["entry_barrier"] = 10 if reviews <= 10 else 9 if reviews <= 30 else 7 if reviews <= 50 else 5 if reviews <= 100 else 3

    if ad_pct is not None:
        organic = 100 - ad_pct
        scores["organic_health"] = 10 if organic >= 90 else 8 if organic >= 70 else 6 if organic >= 50 else 4 if organic >= 30 else 2

    if price is not None and (legacy or price > 0):
        scores["price_sweetspot"] = 10 if 25 <= price <= 35 else 8 if 20 <= price <= 45 else 6 if 15 <= price <= 50 else 4 if 10 <= price <= 60 else 2

    if traffic_count is not None:
        scores["traffic_breadth"] = 10 if traffic_count >= 40 else 8 if traffic_count >= 25 else 6 if traffic_count >= 10 else 4 if traffic_count >= 5 else 2

    if bsr_num is not None or legacy:
        bsr = bsr_num or 999999
        scores["competitive_position"] = 10 if bsr <= 30000 else 8 if bsr <= 100000 else 6 if bsr <= 300000 else 4 if bsr <= 1000000 else 2

    if shelf is not None and daily is not None:
        if shelf <= 0:
            scores["growth_potential"] = 5 if legacy else None
        elif shelf <= 90 and daily >= 1:
            scores["growth_potential"] = 10
        elif shelf <= 90 and daily >= 0.3:
            scores["growth_potential"] = 8
        elif shelf <= 180 and daily >= 1:
            scores["growth_potential"] = 9
        elif shelf <= 180 and daily >= 0.3:
            scores["growth_potential"] = 7
        elif shelf <= 365 and daily >= 3:
            scores["growth_potential"] = 8
        elif shelf <= 365 and daily >= 1:
            scores["growth_potential"] = 6
        else:
            scores["growth_potential"] = 4

    if legacy or variations is not None or aplus_present:
        listing_score = 0
        variations = variations or 0
        if variations >= 5:
            listing_score += 4
        elif variations >= 2:
            listing_score += 3
        elif variations >= 1:
            listing_score += 2
        if aplus:
            listing_score += 4
        scores["listing_quality"] = min(10, listing_score + 2)

    if legacy:
        warnings.append(
            "v1_legacy reproduces the original calibrated formula and divides monthly sales by listing age"
        )
    return scores, warnings


def _tier(score_value: float) -> str:
    tier = "C"
    for name, threshold in sorted(TIERS.items(), key=lambda item: item[1]):
        if score_value >= threshold:
            tier = name
    return tier


def score_detailed(
    detail_data: dict,
    traffic_count: int | None,
    ad_pct: float | None,
    scoring_version: str = DEFAULT_SCORING_VERSION,
) -> ScoreResult:
    """Score an ASIN and expose version, missing dimensions and confidence.

    ``v1_legacy`` preserves the original historical formula used by the 97-ASIN
    calibration. ``v2_semantic`` treats monthly sales as a monthly estimate,
    uses ``sales / 30`` for daily velocity and never silently turns unavailable
    inputs into real zeros.
    """
    if scoring_version not in SUPPORTED_SCORING_VERSIONS:
        supported = ", ".join(SUPPORTED_SCORING_VERSIONS)
        raise ValueError(f"unsupported scoring version {scoring_version!r}; choose {supported}")

    dimensions, warnings = _dimension_scores(
        detail_data, traffic_count, ad_pct, scoring_version
    )
    available = {name: value for name, value in dimensions.items() if value is not None}
    available_weight = sum(WEIGHTS[name] for name in available)
    total_weight = sum(WEIGHTS.values())
    missing = [name for name, value in dimensions.items() if value is None]

    if available_weight:
        weighted_total = sum(float(value) * WEIGHTS[name] for name, value in available.items())
        final = round(weighted_total / (10 * available_weight) * 100, 1)
    else:
        final = 0.0
        warnings.append("no scorable dimensions are available")

    completeness = round(len(available) / len(WEIGHTS) * 100, 1)
    confidence = round(available_weight / total_weight * 100, 1)
    if missing:
        warnings.append("score is normalized over available dimensions")

    return ScoreResult(
        total_score=final,
        tier=_tier(final),
        dimensions=dimensions,
        scoring_version=scoring_version,
        data_completeness=completeness,
        score_confidence=confidence,
        missing_dimensions=missing,
        warnings=warnings,
    )


def score(
    detail_data: dict,
    traffic_count: int | None,
    ad_pct: float | None,
    scoring_version: str = DEFAULT_SCORING_VERSION,
) -> tuple[float, str, dict[str, float | None]]:
    """Compatibility wrapper for callers expecting the historical tuple shape."""
    result = score_detailed(detail_data, traffic_count, ad_pct, scoring_version)
    return result.total_score, result.tier, result.dimensions
