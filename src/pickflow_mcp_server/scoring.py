"""
Nine-dimension weighted ASIN scoring.
Validated on 97 known-good ASINs: S+A = 79.4% coverage.
"""
import re

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


def parse_bsr(bsr_text: str) -> int | None:
    """Extract BSR from 'Home & Kitchen (Rank: 168100)' format."""
    m = re.search(r'Rank:\s*([\d,]+)', str(bsr_text))
    return int(m.group(1).replace(",", "")) if m else None


def parse_exposure_items(items: list[dict]) -> tuple[int, float]:
    """Count traffic keywords and ad dependency from traffic_terms items."""
    if not items:
        return 0, 0.0
    total = len(items)
    ad_count = sum(1 for x in items if "Ad" in (x.get("exposure_position", "") or ""))
    ad_pct = round(ad_count / max(total, 1) * 100, 1)
    return total, ad_pct


def score(detail_data: dict, traffic_count: int, ad_pct: float) -> tuple[float, str, dict]:
    """
    Score an ASIN on nine dimensions.

    Args:
        detail_data: product_detail API response
        traffic_count: total traffic keywords
        ad_pct: ad dependency percentage

    Returns:
        (score_0_100, tier, dimension_scores)
    """
    d = detail_data
    price = float(d.get("price", 0) or 0)
    reviews = int(d.get("review_count", 0) or 0)
    sales = int(d.get("monthly_sales_volume", 0) or 0)
    shelf = int(d.get("days_on_shelf", 0) or 0)
    profit_rate = float(d.get("gross_profit_rate", 0) or 0)
    bsr_num = parse_bsr(d.get("top_category", ""))
    variations = int(d.get("variation_count", 0) or 0)
    aplus = d.get("a_plus", False)
    daily = sales / max(shelf, 1) if shelf > 0 else sales
    organic = 100 - ad_pct

    scores = {}

    # Sales velocity
    ds = daily
    scores["sales_velocity"] = 10 if ds >= 10 else 8 if ds >= 5 else 6 if ds >= 2 else 4 if ds >= 1 else 2 if ds >= 0.5 else 1

    # Profit
    scores["profit"] = 10 if profit_rate >= 70 else 8 if profit_rate >= 60 else 6 if profit_rate >= 50 else 4 if profit_rate >= 40 else 2

    # Entry barrier
    scores["entry_barrier"] = 10 if reviews <= 10 else 9 if reviews <= 30 else 7 if reviews <= 50 else 5 if reviews <= 100 else 3

    # Organic health
    scores["organic_health"] = 10 if organic >= 90 else 8 if organic >= 70 else 6 if organic >= 50 else 4 if organic >= 30 else 2

    # Price sweetspot
    scores["price_sweetspot"] = 10 if 25 <= price <= 35 else 8 if 20 <= price <= 45 else 6 if 15 <= price <= 50 else 4 if 10 <= price <= 60 else 2

    # Traffic breadth
    scores["traffic_breadth"] = 10 if traffic_count >= 40 else 8 if traffic_count >= 25 else 6 if traffic_count >= 10 else 4 if traffic_count >= 5 else 2

    # Competitive position
    bsr = bsr_num or 999999
    scores["competitive_position"] = 10 if bsr <= 30000 else 8 if bsr <= 100000 else 6 if bsr <= 300000 else 4 if bsr <= 1000000 else 2

    # Growth potential
    if shelf <= 0:
        scores["growth_potential"] = 5
    elif shelf <= 90 and ds >= 1:
        scores["growth_potential"] = 10
    elif shelf <= 90 and ds >= 0.3:
        scores["growth_potential"] = 8
    elif shelf <= 180 and ds >= 1:
        scores["growth_potential"] = 9
    elif shelf <= 180 and ds >= 0.3:
        scores["growth_potential"] = 7
    elif shelf <= 365 and ds >= 3:
        scores["growth_potential"] = 8
    elif shelf <= 365 and ds >= 1:
        scores["growth_potential"] = 6
    else:
        scores["growth_potential"] = 4

    # Listing quality
    s = 0
    if variations >= 5:
        s += 4
    elif variations >= 2:
        s += 3
    elif variations >= 1:
        s += 2
    if aplus:
        s += 4
    scores["listing_quality"] = min(10, s + 2)

    total = sum(scores[k] * WEIGHTS[k] for k in WEIGHTS)
    max_score = sum(10 * WEIGHTS[k] for k in WEIGHTS)
    final = round(total / max_score * 100, 1)

    tier = "C"
    for t, threshold in sorted(TIERS.items(), key=lambda x: x[1]):
        if final >= threshold:
            tier = t

    return final, tier, scores
