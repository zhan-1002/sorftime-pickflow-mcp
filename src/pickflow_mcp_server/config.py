"""
PickFlow MCP Server configuration.
API keys loaded from ~/.mcp.json or PICKFLOW_API_KEY env var.
"""
import json
import os
from pathlib import Path

# Default sweetspot parameters
SWEETSPOT = {
    "price_min": 10,
    "price_max": 50,
    "ratings_count_max": 150,
    "month_sales_volume_min": 50,
    "delivery_type": "FBA",
    "amz_site": "US",
}

# Category filter words
FILTER_WORDS = [
    "bulk", "gift", "party", "wedding", "christmas", "halloween", "favor",
    "supplies", "bags", "decorations", "team", "christian", "employee",
    "appreciation", "napkin", "candle", "basket", "ornament", "new year",
    "easter", "valentine", "thanksgiving", "goodie", "goody", "stuffers",
    "baseball", "soccer", "cheer", "sports", "fans", "tablecloth",
    "centerpiece", "table runner", "stocking", "advent", "nativity",
    "wreath", "garland", "bridal", "bridesmaid", "baby shower",
    "church", "religious", "prayer", "journal", "notebook",
    "tote bag", "gift bag", "gift box", "trophy", "medal", "award",
    "graduation", "housewarming", "house warming", "new home",
    "classroom", "teacher", "back to school",
    "kitchen", "farmhouse", "rustic", "boho", "vintage",
    "personalized", "custom", "diy", "craft", "handmade",
]

BLACKLIST = [
    "amazon gift card", "egift", "printable gift card", "e gift card",
    "roblox gift card", "playstation gift card", "xbox gift card",
]

MIN_KEYWORD_LENGTH = 8

# ABA cache DB location
CACHE_DIR = Path.home() / ".pickflow"
CACHE_DB = CACHE_DIR / "aba_cache.db"


def get_api_url():
    """Read Sorftime API URL from ~/.mcp.json or environment."""
    mcp_path = Path.home() / ".mcp.json"
    if mcp_path.exists():
        with open(mcp_path) as f:
            config = json.load(f)
            sorftime = config.get("sorftime-mcp", {})
            url = sorftime.get("url", "")
            if url:
                return url
    return os.environ.get("PICKFLOW_API_URL", "")


def match_keyword(kw: str) -> bool:
    """Check if keyword matches target product categories."""
    kw_lower = kw.lower()
    if len(kw) < MIN_KEYWORD_LENGTH:
        return False
    for b in BLACKLIST:
        if b in kw_lower:
            return False
    for w in FILTER_WORDS:
        if w in kw_lower:
            return True
    return False
