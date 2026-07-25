"""
PickFlow MCP Server — Sorftime-powered Amazon product discovery.
"""
import asyncio
import json
import re
from typing import Any

from mcp.server.fastmcp import FastMCP

from .config import SWEETSPOT, FILTER_WORDS, get_api_url, CACHE_DB
from .api import (
    keyword_detail,
    keyword_list,
    product_search,
    product_detail,
    product_traffic_terms,
)

def _parse_review_stats(stats: str) -> tuple[float, float, float, float]:
    """Shared parser for search_result_first_page_stats. Returns (rev100, rev300, rev500, non_bs)."""
    rev100 = rev300 = rev500 = non_bs = 0.0
    if not stats:
        return 0.0, 0.0, 0.0, 0.0
    m = re.search(r"Organic review-count below 100/300/500.*?:\s*([\d.]+)%/([\d.]+)%/([\d.]+)%", stats)
    if m:
        rev100, rev300, rev500 = float(m.group(1)), float(m.group(2)), float(m.group(3))
    m = re.search(r"Organic non-Best-Seller.*?share:\s*([\d.]+)%", stats)
    if m:
        non_bs = float(m.group(1))
    return rev100, rev300, rev500, non_bs
from .cache import (
    store_page,
    get_cache_status,
    query_pool,
    term_distribution,
    clear_cache,
)
from .scoring import score, parse_exposure_items, check_hard_filters

mcp = FastMCP("PickFlow")


# ══════════════════════════════════════════
# LAYER 0: Cache Tools (offline, no API)
# ══════════════════════════════════════════

@mcp.tool()
async def cache_aba_pull(pages: str = "1-500") -> dict:
    """
    Pull and cache ABA keyword_list pages.
    Specify pages as range like '1-500' or '750-2500'.

    USE THIS TOOL WHEN: Building local keyword cache for offline analysis.
    Run once per page range. Subsequent calls will skip duplicates.
    """
    parts = pages.split("-")
    start = int(parts[0])
    end = int(parts[1]) if len(parts) > 1 else start

    total_stored = 0
    errors = 0
    for page in range(start, end + 1):
        try:
            result = await keyword_list(page)
        except Exception:
            errors += 1
            continue

        if not result or "_error" in result:
            continue

        items = result.get("data", [])
        if isinstance(items, dict):
            items = [items]

        if items:
            stored = store_page(page, items)
            total_stored += stored

        if page % 100 == 0:
            await asyncio.sleep(0.1)  # yield

    status = get_cache_status()
    return {
        "success": True,
        "pages_pulled": f"{start}-{end}",
        "new_keywords_stored": total_stored,
        "errors": errors,
        "cache_status": status,
    }


@mcp.tool()
async def cache_status() -> dict:
    """
    Show current ABA cache state: page coverage, total keywords, category breakdown.

    USE THIS TOOL WHEN: Checking if cache covers the page ranges needed for research.
    """
    status = get_cache_status()
    status["db_location"] = str(CACHE_DB)
    return status


@mcp.tool()
async def cache_query(categories: str = "",
                       min_search_volume: int = 2000,
                       limit: int = 500) -> dict:
    """
    Query cached keywords by category and min search volume.

    Args:
        categories: Comma-separated filter words (e.g. 'bulk,gift,wedding')
        min_search_volume: Minimum 30-day search volume
        limit: Max results

    USE THIS TOOL WHEN: Exploring what's in the cache for specific product lines.
    """
    cat_list = [c.strip().lower() for c in categories.split(",") if c.strip()] if categories else None
    keywords = query_pool(categories=cat_list, min_sv=min_search_volume, limit=limit)

    return {
        "success": True,
        "filters": {"categories": cat_list or FILTER_WORDS, "min_sv": min_search_volume},
        "count": len(keywords),
        "keywords": keywords[:50],  # Return first 50, full list would be too large
        "total_matching": len(keywords),
    }


@mcp.tool()
async def cache_term_distribution(term: str) -> dict:
    """
    Show how a product term distributes across cached ABA pages.

    Args:
        term: A category word like 'christmas', 'bulk', 'wedding'

    USE THIS TOOL WHEN: Deciding which ABA page ranges to pull for a specific category.
    """
    return term_distribution(term)


@mcp.tool()
async def cache_clear(pages: str = "") -> dict:
    """
    Clear cached keywords. If pages specified (e.g. '1-500'), clears only those.

    USE THIS TOOL WHEN: Refreshing stale cache or freeing space.
    """
    if pages:
        parts = pages.split("-")
        start, end = int(parts[0]), int(parts[1]) if len(parts) > 1 else int(parts[0])
    else:
        start, end = None, None

    deleted = clear_cache(start, end)
    return {"success": True, "deleted": deleted, "pages": pages or "all"}


# ══════════════════════════════════════════
# LAYER 1: Pipeline Tools (API + cache)
# ══════════════════════════════════════════

@mcp.tool()
async def pool_build(categories: str = "",
                      min_search_volume: int = 2000,
                      limit: int = 1000) -> dict:
    """
    Build a keyword pool from cached ABA data. No API calls.

    Args:
        categories: Comma-separated filter terms (default: all bulk/gift/party/wedding/...)
        min_search_volume: Minimum 30d search volume
        limit: Max pool size

    USE THIS TOOL WHEN: Starting a new research session. Must have cache_aba_pull run first.
    """
    cat_list = [c.strip().lower() for c in categories.split(",") if c.strip()] if categories else None
    keywords = query_pool(categories=cat_list, min_sv=min_search_volume, limit=limit)

    return {
        "success": True,
        "pool_size": len(keywords),
        "volume_range": {
            "min": min(k["search_volume_30d"] for k in keywords) if keywords else 0,
            "max": max(k["search_volume_30d"] for k in keywords) if keywords else 0,
        },
        "sample": keywords[:10],
    }


@mcp.tool()
async def market_screen(pool_keywords: str, limit: int = 80,
                         min_search_volume: int = 3000,
                         concurrency: int = 5) -> dict:
    """
    Run keyword_detail on top pool keywords for market screening.
    Uses caching + concurrent API calls + smart skip for speed.

    Args:
        pool_keywords: JSON array of keyword objects from pool_build, or 'cached_top'
        limit: Max keywords to analyze
        min_search_volume: Skip keywords below this monthly search volume
        concurrency: Max parallel API calls (1-10)

    USE THIS TOOL WHEN: Evaluating which markets have best new-seller opportunity.
    """
    # Smart skip: filter by volume before making API calls
    sem = asyncio.Semaphore(max(1, min(concurrency, 10)))

    if pool_keywords == "cached_top":
        keywords = query_pool(categories=None, min_sv=5000, limit=limit)
    else:
        try:
            keywords = json.loads(pool_keywords)[:limit]
        except json.JSONDecodeError:
            return {"error": True, "message": "Invalid pool_keywords JSON"}

    if not keywords:
        return {"error": True, "message": "No keywords provided. Run pool_build first or use 'cached_top'."}

    # Smart skip: pre-filter
    import time
    t0 = time.time()
    eligible = []
    skipped = 0
    for k in keywords[:limit]:
        kw = k["keyword"] if isinstance(k, dict) else str(k)
        sv = k.get("search_volume_30d", 0) if isinstance(k, dict) else 0
        if sv < min_search_volume:
            skipped += 1
            continue
        eligible.append(k)

    if not eligible:
        return {"error": True, "message": f"All {len(keywords[:limit])} keywords below volume threshold ({min_search_volume}). Lower min_search_volume."}

    async def screen_one(kw_dict):
        kw_text = kw_dict["keyword"] if isinstance(kw_dict, dict) else str(kw_dict)

        # Cache check
        from .cache import get_market_cache, store_market_cache
        hit, cached = get_market_cache(kw_text)
        if hit:
            return {
                "keyword": kw_text, "monthly_search_volume": cached["monthly_sv"],
                "cpc": cached["cpc"], "competitors": cached["competitors"],
                "rev_below_100_pct": cached["rev100"], "non_amazon_pct": cached["non_bs"],
                "peak_season": "", "market_score": _calc_market_score(cached["monthly_sv"], cached["rev100"], cached["non_bs"]),
                "tier": _calc_tier(cached["monthly_sv"], cached["rev100"], cached["non_bs"]),
                "cached": True,
            }

        # API call with semaphore
        async with sem:
            try:
                detail = await keyword_detail(kw_text)
            except Exception:
                return None

        if not detail or "_error" in detail:
            return None

        d = detail.get("data", {})
        rev100, _, _, non_bs = _parse_review_stats(d.get("search_result_first_page_stats", ""))
        ms = int(d.get("monthly_search_volume", 0) or 0)
        cpc = float(d.get("recommended_cpc_bid", 0) or 0)
        comp = int(d.get("search_result_competitor_count", 0) or 0)
        peak = d.get("search_volume_peak_season", "")

        # Store cache
        store_market_cache(kw_text, ms, cpc, comp, rev100, non_bs)

        return {
            "keyword": kw_text, "monthly_search_volume": ms, "cpc": cpc,
            "competitors": comp, "rev_below_100_pct": rev100,
            "non_amazon_pct": non_bs, "peak_season": peak,
            "market_score": _calc_market_score(ms, rev100, non_bs),
            "tier": _calc_tier(ms, rev100, non_bs),
            "cached": False,
        }

    results = await asyncio.gather(*[screen_one(kw) for kw in eligible])
    markets = [r for r in results if r is not None]

    s_count = sum(1 for m in markets if m["tier"] == "S")
    a_count = sum(1 for m in markets if m["tier"] == "A")
    api_calls = sum(1 for m in markets if not m.get("cached", False))
    cache_hits = sum(1 for m in markets if m.get("cached", False))
    elapsed = round(time.time() - t0, 1)

    return {
        "success": True,
        "markets_analyzed": len(markets),
        "skipped_low_volume": skipped,
        "s_tier": s_count,
        "a_tier": a_count,
        "api_calls": api_calls,
        "cache_hits": cache_hits,
        "concurrency": concurrency,
        "elapsed_seconds": elapsed,
        "top_markets": sorted(markets, key=lambda m: m["market_score"], reverse=True)[:20],
    }


def _calc_market_score(ms: int, rev100: float, non_bs: float) -> float:
    demand = min(10, ms / 20000)
    newbie = rev100 / 100 * 10
    amazon = non_bs / 100 * 10
    return round(demand * 0.4 + newbie * 0.35 + amazon * 0.25, 1)


def _calc_tier(ms: int, rev100: float, non_bs: float) -> str:
    score = _calc_market_score(ms, rev100, non_bs)
    return "S" if score >= 5 else "A" if score >= 3.5 else "B"


@mcp.tool()
async def asin_discover(markets_json: str, price_min: int = 0, price_max: int = 0,
                         reviews_max: int = 0, fba_only: bool = True) -> dict:
    """
    Run product_search on candidate markets to discover ASINs.

    Args:
        markets_json: JSON array of market objects from market_screen, or 's_tier'/'a_tier' to use tier filter
        price_min/max: Override sweetspot price range (0 = use default)
        reviews_max: Override max reviews (0 = use default 150)
        fba_only: Only show FBA products

    USE THIS TOOL WHEN: Finding specific ASINs in validated markets.
    """
    if markets_json in ("s_tier", "a_tier"):
        return {"error": True, "message": "Use markets from market_screen output. Pass the JSON array."}

    try:
        markets = json.loads(markets_json)
    except json.JSONDecodeError:
        return {"error": True, "message": "Invalid markets_json"}

    params = {
        "price_min": price_min or SWEETSPOT["price_min"],
        "price_max": price_max or SWEETSPOT["price_max"],
        "ratings_count_max": reviews_max or SWEETSPOT["ratings_count_max"],
        "delivery_type": "FBA" if fba_only else "Both",
        "month_sales_volume_min": SWEETSPOT["month_sales_volume_min"],
        "amz_site": "US",
        "page": 1,
    }

    all_asins = []
    for mkt in markets[:25]:
        kw = mkt["keyword"] if isinstance(mkt, dict) else str(mkt)
        try:
            result = await product_search(kw, **params)
        except Exception:
            continue

        if not result or "_error" in result:
            continue

        items = result.get("data", [])
        if isinstance(items, dict):
            items = [items]

        for p in items:
            all_asins.append({
                "market": kw,
                "market_score": mkt.get("market_score", 0) if isinstance(mkt, dict) else 0,
                "asin": p.get("asin", ""),
                "title": (p.get("title", "") or "")[:80],
                "price": p.get("price", ""),
                "monthly_sales": p.get("monthly_sales_volume", ""),
                "reviews": p.get("review_count", ""),
                "stars": p.get("star_rating", ""),
                "brand": p.get("brand", ""),
            })

        await asyncio.sleep(0.15)

    return {
        "success": True,
        "markets_searched": len(markets[:25]),
        "asins_found": len(all_asins),
        "asins": all_asins,
    }


@mcp.tool()
async def asin_score(asin: str, include_detail: bool = False) -> dict:
    """
    Score a single ASIN on nine dimensions.

    Returns total score (0-100), tier (S/A/B/C), and dimension breakdown.
    Set include_detail=True to get full product detail fields.

    USE THIS TOOL WHEN: Evaluating a specific ASIN's viability.
    """
    try:
        d_result = await product_detail(asin)
        t_result = await product_traffic_terms(asin, page=1)
    except Exception as e:
        return {"error": True, "message": str(e), "asin": asin}

    if not d_result or "_error" in d_result:
        return {"error": True, "message": "product_detail failed", "asin": asin}

    detail = d_result.get("data", {})
    t_items = []
    if t_result and "_error" not in t_result:
        t_items = t_result.get("data", [])
        if isinstance(t_items, dict):
            t_items = [t_items]

    traffic_count, ad_pct = parse_exposure_items(t_items)
    total, tier, scores = score(detail, traffic_count, ad_pct)
    filter_pass, filter_fails = check_hard_filters(detail)

    result = {
        "asin": asin,
        "total_score": total,
        "tier": tier,
        "tier_final": tier if filter_pass else "F",
        "hard_filters": {"passed": filter_pass, "failures": filter_fails},
        "dimensions": scores,
        "summary": {
            "title": detail.get("title", "")[:80],
            "price": detail.get("price", ""),
            "monthly_sales": detail.get("monthly_sales_volume", ""),
            "reviews": detail.get("review_count", ""),
            "stars": detail.get("star_rating", ""),
            "brand": detail.get("brand", ""),
            "days_on_shelf": detail.get("days_on_shelf", ""),
            "profit_rate": detail.get("gross_profit_rate", ""),
        },
        "traffic": {
            "total_keywords": traffic_count,
            "ad_dependency_pct": ad_pct,
            "organic_pct": round(100 - ad_pct, 1),
        },
        "weights_used": {k: round(v, 1) for k, v in scoring.WEIGHTS.items()},
    }

    if include_detail:
        result["detail"] = detail

    return result


@mcp.tool()
async def asin_score_batch(asins_json: str, limit: int = 50,
                            concurrency: int = 5) -> dict:
    """
    Score multiple ASINs and rank by total score. Uses concurrent API calls.

    Args:
        asins_json: JSON array of ASIN objects (from asin_discover) or '["ASIN1","ASIN2",...]'
        limit: Max ASINs to score
        concurrency: Max parallel API calls (1-10)

    USE THIS TOOL WHEN: Scoring all ASINs discovered by asin_discover.
    """
    sem = asyncio.Semaphore(max(1, min(concurrency, 10)))

    try:
        asins = json.loads(asins_json)
    except json.JSONDecodeError:
        return {"error": True, "message": "Invalid ASINs JSON"}

    # Extract ASIN strings
    asin_list = []
    for a in asins[:limit]:
        if isinstance(a, dict):
            asin_list.append(a.get("asin", ""))
        elif isinstance(a, str):
            asin_list.append(a)

    asin_list = [a for a in asin_list if a][:limit]

    import time
    t0 = time.time()

    async def score_one(asin):
        async with sem:
            try:
                d_result = await product_detail(asin)
                t_result = await product_traffic_terms(asin, page=1)
            except Exception:
                return None

            if not d_result or "_error" in d_result:
                return None

            detail = d_result.get("data", {})
            t_items = []
            if t_result and "_error" not in t_result:
                t_items = t_result.get("data", [])
                if isinstance(t_items, dict):
                    t_items = [t_items]

            traffic_count, ad_pct = parse_exposure_items(t_items)
            total, tier, scores = score(detail, traffic_count, ad_pct)
            filter_pass, filter_fails = check_hard_filters(detail)

            return {
                "asin": asin, "title": detail.get("title", "")[:80],
                "price": detail.get("price", ""),
                "monthly_sales": detail.get("monthly_sales_volume", ""),
                "reviews": detail.get("review_count", ""),
                "stars": detail.get("star_rating", ""),
                "profit_rate": detail.get("gross_profit_rate", ""),
                "ad_pct": ad_pct, "total_score": total,
                "tier": tier, "tier_final": tier if filter_pass else "F",
                "hard_filters": {"passed": filter_pass, "failures": filter_fails},
                "scores": {k: v for k, v in scores.items()},
            }

    results_raw = await asyncio.gather(*[score_one(asin) for asin in asin_list])
    results = [r for r in results_raw if r is not None]
    results.sort(key=lambda r: r["total_score"], reverse=True)

    s_count = sum(1 for r in results if r["tier"] == "S")
    a_count = sum(1 for r in results if r["tier"] == "A")
    elapsed = round(time.time() - t0, 1)

    return {
        "success": True,
        "scored": len(results),
        "s_tier": s_count,
        "a_tier": a_count,
        "concurrency": concurrency,
        "elapsed_seconds": elapsed,
        "top": results[:30],
    }


# ══════════════════════════════════════════
# LAYER 2: Analysis Tools
# ══════════════════════════════════════════

@mcp.tool()
async def keyword_analyze(keyword: str) -> dict:
    """
    Deep single-keyword analysis: market metrics, top competitors, seasonality.

    USE THIS TOOL WHEN: Evaluating a specific keyword before committing to a product line.
    """
    try:
        result = await keyword_detail(keyword)
    except Exception as e:
        return {"error": True, "message": str(e)}

    if not result or "_error" in result:
        return {"error": True, "message": "No data for this keyword", "keyword": keyword}

    d = result.get("data", {})
    top100 = d.get("recent_15d_top3_pages_organic_top100_stats", {})
    rev100, rev300, rev500, non_bs = _parse_review_stats(d.get("search_result_first_page_stats", ""))

    top_products = []
    for p in top100.get("top5_product", [])[:5]:
        top_products.append({
            "asin": p.get("asin", ""),
            "title": (p.get("title", "") or "")[:80],
            "price": p.get("price", ""),
            "monthly_sales": p.get("monthly_sales", ""),
            "brand": p.get("brand", ""),
        })

    return {
        "keyword": keyword,
        "metrics": {
            "monthly_search_volume": int(d.get("monthly_search_volume", 0) or 0),
            "weekly_search_rank": int(d.get("weekly_search_rank", 0) or 0),
            "cpc_bid": float(d.get("recommended_cpc_bid", 0) or 0),
            "competitor_count": int(d.get("search_result_competitor_count", 0) or 0),
            "peak_season": d.get("search_volume_peak_season", ""),
        },
        "new_seller_opportunity": {
            "review_below_100_pct": rev100,
            "review_below_300_pct": rev300,
            "review_below_500_pct": rev500,
            "non_amazon_share_pct": non_bs,
        },
        "top_competitors": top_products,
    }


@mcp.tool()
async def asin_reverse_traffic(asin: str) -> dict:
    """
    Reverse-lookup traffic keywords for an ASIN and check cache coverage.

    USE THIS TOOL WHEN: Checking if an existing product's keywords are covered by the ABA cache.
    """
    traffic_kws = []
    for page in [1, 2]:
        try:
            result = await product_traffic_terms(asin, page=page)
        except Exception:
            continue
        if result and "_error" not in result:
            items = result.get("data", [])
            if isinstance(items, dict):
                items = [items]
            for t in items:
                traffic_kws.append({
                    "keyword": t.get("keyword", ""),
                    "monthly_sv": t.get("monthly_search_volume", 0),
                    "exposure": t.get("exposure_position", ""),
                })

    ad_count = sum(1 for k in traffic_kws if "Ad" in (k["exposure"] or ""))

    # Check cache coverage
    from .cache import _ensure_db
    conn = _ensure_db()
    cached = []
    for k in traffic_kws:
        row = conn.execute("SELECT page FROM aba_keywords WHERE keyword = ?", (k["keyword"],)).fetchone()
        if row:
            cached.append(k["keyword"])
    conn.close()

    return {
        "asin": asin,
        "traffic_keywords": len(traffic_kws),
        "ad_keywords": ad_count,
        "ad_dependency_pct": round(ad_count / max(len(traffic_kws), 1) * 100, 1),
        "cached_coverage": f"{len(cached)}/{len(traffic_kws)} keywords in ABA cache",
        "cached_keywords": cached[:20],
        "uncached_top": [k["keyword"] for k in traffic_kws if k["keyword"] not in cached][:10],
        "top_traffic": sorted(traffic_kws, key=lambda k: k["monthly_sv"] or 0, reverse=True)[:10],
    }


@mcp.tool()
async def asin_compare(asin_a: str, asin_b: str) -> dict:
    """
    Side-by-side nine-dimension comparison of two ASINs.

    USE THIS TOOL WHEN: Deciding between two competitor products to benchmark against.
    """
    results = {}
    for label, asin in [("A", asin_a), ("B", asin_b)]:
        try:
            d = await product_detail(asin)
            t = await product_traffic_terms(asin, page=1)
        except Exception:
            results[label] = {"error": "API call failed", "asin": asin}
            continue

        if not d or "_error" in d:
            results[label] = {"error": "detail failed", "asin": asin}
            continue

        detail = d.get("data", {})
        items = []
        if t and "_error" not in t:
            items = t.get("data", [])
            if isinstance(items, dict):
                items = [items]
        tc, ad = parse_exposure_items(items)
        total, tier, scores = score(detail, tc, ad)

        results[label] = {
            "asin": asin,
            "title": detail.get("title", "")[:80],
            "price": detail.get("price", ""),
            "monthly_sales": detail.get("monthly_sales_volume", ""),
            "reviews": detail.get("review_count", ""),
            "stars": detail.get("star_rating", ""),
            "profit_rate": detail.get("gross_profit_rate", ""),
            "ad_pct": ad,
            "total_score": total,
            "tier": tier,
            "dimensions": scores,
        }

    return {
        "asin_a": results.get("A", {}),
        "asin_b": results.get("B", {}),
        "comparison": {
            "price_diff": _diff(results, "price"),
            "sales_diff": _diff(results, "monthly_sales"),
            "reviews_diff": _diff(results, "reviews"),
            "score_diff": _diff(results, "total_score"),
        } if "error" not in results.get("A", {}) and "error" not in results.get("B", {}) else {},
    }


@mcp.tool()
async def pipeline_validate(test_asins_json: str) -> dict:
    """
    Validate pipeline recall against a list of known-good ASINs.

    Args:
        test_asins_json: JSON array like '["B0XXX","B0YYY",...]'

    USE THIS TOOL WHEN: Measuring how well the cache + pipeline covers existing products.
    """
    try:
        asins = json.loads(test_asins_json)
    except json.JSONDecodeError:
        return {"error": True, "message": "Invalid JSON"}

    results = []
    covered_count = 0
    for asin in asins:
        traffic = await asin_reverse_traffic(asin)
        covered = int(traffic["cached_coverage"].split("/")[0]) if "/" in traffic["cached_coverage"] else 0
        if covered > 0:
            covered_count += 1
        results.append({
            "asin": asin,
            "traffic_keywords": traffic["traffic_keywords"],
            "cached_coverage": traffic["cached_coverage"],
        })

    return {
        "success": True,
        "total_asins": len(asins),
        "asins_with_any_coverage": covered_count,
        "recall_rate_pct": round(covered_count / max(len(asins), 1) * 100, 1),
        "details": results[:20],
    }


# ══════════════════════════════════════════
# LAYER 3: Session Management
# ══════════════════════════════════════════

@mcp.tool()
async def session_status() -> dict:
    """
    Show current session state: cache status and API configuration.

    USE THIS TOOL WHEN: Starting a session or checking configuration.
    """
    cache = get_cache_status()
    cache["db_location"] = str(CACHE_DB)
    return {
        "api_configured": bool(get_api_url()),
        "cache": cache,
        "sweetspot_defaults": SWEETSPOT,
    }


def _diff(results: dict, field: str) -> str:
    """Safe comparison of two result fields."""
    a = results.get("A", {}).get(field, 0)
    b = results.get("B", {}).get(field, 0)
    try:
        a_val = float(a) if a else 0
        b_val = float(b) if b else 0
        delta = a_val - b_val
        if delta > 0:
            return f"A > B by {delta:.1f}"
        elif delta < 0:
            return f"B > A by {abs(delta):.1f}"
        return "equal"
    except (ValueError, TypeError):
        return f"A={a}, B={b}"


def main():
    """Entry point for `pickflow-mcp-server` command."""
    mcp.run()


if __name__ == "__main__":
    main()
