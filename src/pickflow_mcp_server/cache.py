"""
SQLite cache for ABA keyword_list results.
Stores raw keywords by page, supports filtering and analytics.
"""
import sqlite3
import time
from pathlib import Path
from .config import CACHE_DIR, CACHE_DB, match_keyword


def _ensure_db():
    """Create cache directory and database if not exists."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(CACHE_DB))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS aba_keywords (
            keyword TEXT NOT NULL,
            weekly_search_rank INTEGER,
            search_volume_30d INTEGER,
            purchase_volume_90d INTEGER,
            search_conversion_rate_90d REAL,
            click_conversion_rate_90d REAL,
            cpc_exact_bid REAL,
            top3_click_share REAL,
            top3_conversion_share REAL,
            peak_season TEXT,
            page INTEGER NOT NULL,
            cached_at REAL NOT NULL,
            UNIQUE(keyword)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cache_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.commit()
    return conn


def store_page(page: int, keywords: list[dict]) -> int:
    """Store a single ABA page of keywords. Returns count of new keywords."""
    conn = _ensure_db()
    now = time.time()
    count = 0
    before = conn.execute("SELECT COUNT(*) FROM aba_keywords").fetchone()[0]
    for kw in keywords:
        try:
            conn.execute("""
                INSERT OR IGNORE INTO aba_keywords
                (keyword, weekly_search_rank, search_volume_30d, purchase_volume_90d,
                 search_conversion_rate_90d, click_conversion_rate_90d, cpc_exact_bid,
                 top3_click_share, top3_conversion_share, peak_season, page, cached_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                kw.get("keyword", ""),
                kw.get("weekly_search_rank"),
                kw.get("search_volume_30d"),
                kw.get("purchase_volume_after_search_90d"),
                kw.get("search_conversion_rate_90d"),
                kw.get("click_conversion_rate_90d"),
                kw.get("cpc_exact_bid"),
                kw.get("top3_product_click_share"),
                kw.get("top3_product_conversion_share"),
                kw.get("peak_season"),
                page,
                now,
            ))
        except Exception:
            pass
    after = conn.execute("SELECT COUNT(*) FROM aba_keywords").fetchone()[0]
    count = after - before
    conn.commit()
    conn.close()
    return count


def get_cache_status() -> dict:
    """Return cache status: pages, total keywords, breakdown."""
    conn = _ensure_db()
    pages = conn.execute("SELECT MIN(page), MAX(page), COUNT(DISTINCT page) FROM aba_keywords").fetchone()
    total = conn.execute("SELECT COUNT(*) FROM aba_keywords").fetchone()[0]

    # Count by category (filter word match)
    from collections import Counter
    from .config import FILTER_WORDS

    categories = Counter()
    rows = conn.execute("SELECT keyword FROM aba_keywords").fetchall()
    for (kw,) in rows:
        kw_lower = kw.lower()
        for w in FILTER_WORDS:
            if w in kw_lower:
                categories[w] += 1

    # Top categories
    top_cats = categories.most_common(15)

    conn.close()
    return {
        "total_keywords": total,
        "pages_cached": pages[2] or 0,
        "page_range": f"{pages[0]}-{pages[1]}" if pages[0] else "empty",
        "top_categories": [{"term": t, "count": c} for t, c in top_cats],
    }


def query_pool(categories: list[str] | None = None,
               min_sv: int = 0,
               limit: int = 2000) -> list[dict]:
    """Query cached keywords matching categories. Returns sorted by search volume."""
    conn = _ensure_db()
    rows = conn.execute(
        "SELECT keyword, weekly_search_rank, search_volume_30d, cpc_exact_bid, peak_season, page "
        "FROM aba_keywords WHERE search_volume_30d >= ? ORDER BY search_volume_30d DESC LIMIT ?",
        (min_sv, limit)
    ).fetchall()

    keywords = []
    for row in rows:
        kw = row[0]
        if categories:
            kw_lower = kw.lower()
            if not any(c in kw_lower for c in categories):
                continue
        if not match_keyword(kw):
            continue
        keywords.append({
            "keyword": kw,
            "weekly_search_rank": row[1],
            "search_volume_30d": row[2],
            "cpc_exact_bid": row[3],
            "peak_season": row[4],
            "source_page": row[5],
        })
    conn.close()
    return keywords


def term_distribution(term: str) -> dict:
    """Show how a term distributes across cached ABA pages."""
    conn = _ensure_db()
    rows = conn.execute(
        "SELECT page FROM aba_keywords WHERE LOWER(keyword) LIKE ? ORDER BY page",
        (f"%{term.lower()}%",)
    ).fetchall()

    pages = [r[0] for r in rows]
    if not pages:
        conn.close()
        return {"term": term, "count": 0, "density": 0, "recommendation": "No data cached for this term"}

    from collections import Counter
    page_counts = Counter(pages)

    # Group by 100-page buckets
    min_p, max_p = min(pages), max(pages)
    buckets = {}
    for start in range((min_p // 100) * 100, ((max_p // 100) + 1) * 100, 100):
        count = sum(page_counts.get(p, 0) for p in range(start, start + 100))
        if count > 0:
            buckets[f"p{start}-{start+99}"] = count

    density = len(pages) / max(max_p - min_p + 1, 1)
    peak_bucket = max(buckets, key=buckets.get)

    conn.close()
    return {
        "term": term,
        "total_matches": len(pages),
        "page_range": f"p{min_p}-{max_p}",
        "density_per_page": round(density, 3),
        "peak_bucket": f"{peak_bucket} ({buckets[peak_bucket]} hits)",
        "buckets": buckets,
        "recommendation": _recommend(term, buckets, min_p, max_p),
    }


def _recommend(term, buckets, min_p, max_p):
    """Generate recommendation for page ranges to pull."""
    if max_p - min_p < 100:
        return f"Covered: p{min_p}-{max_p}. Pull more pages beyond p{max_p}."
    return f"Strongest in {max(buckets, key=buckets.get)}. Pull p{min_p}-{max_p} for full coverage."


def clear_cache(page_start: int | None = None, page_end: int | None = None) -> int:
    """Clear cached keywords. If page range given, clears only those pages."""
    conn = _ensure_db()
    if page_start is not None and page_end is not None:
        deleted = conn.execute(
            "DELETE FROM aba_keywords WHERE page BETWEEN ? AND ?",
            (page_start, page_end)
        ).rowcount
    else:
        deleted = conn.execute("DELETE FROM aba_keywords").rowcount
    conn.commit()
    conn.close()
    return deleted
