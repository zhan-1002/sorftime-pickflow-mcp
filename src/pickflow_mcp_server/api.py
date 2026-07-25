"""
Sorftime MCP API client — async HTTP calls via httpx.
"""
import json
import httpx
from .config import get_api_url

API_URL = get_api_url()

async def call(method: str, params: dict, rid: int = 1) -> dict | None:
    """Call Sorftime MCP API. Returns parsed data dict or None."""
    if not API_URL:
        raise RuntimeError("Sorftime API not configured")

    payload = {
        "jsonrpc": "2.0",
        "id": rid,
        "method": "tools/call",
        "params": {"name": method, "arguments": params},
    }

    async with httpx.AsyncClient(timeout=25.0) as client:
        resp = await client.post(
            API_URL,
            json=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
        )
        raw = resp.text

    for line in raw.split("\n"):
        if line.startswith("data: "):
            d = json.loads(line[6:])
            if "result" in d:
                for item in d["result"].get("content", []):
                    if item.get("type") == "text":
                        return json.loads(item["text"])
            elif "error" in d:
                return {"_error": d["error"]}
    return None


# ---------- Convenience wrappers ----------

async def keyword_detail(kw: str, site: str = "US") -> dict | None:
    return await call("keyword_detail", {"keyword": kw, "keyword_support_site": site})

async def keyword_list(page: int, site: str = "US") -> dict | None:
    return await call("keyword_list", {"keyword_support_site": site, "page": page})

async def product_search(search_name: str, **overrides) -> dict | None:
    from .config import SWEETSPOT
    params = {**SWEETSPOT, "search_name": search_name, "page": 1}
    params.update(overrides)
    return await call("product_search", params)

async def product_detail(asin: str, site: str = "US") -> dict | None:
    return await call("product_detail", {"asin": asin, "amz_site": site})

async def product_traffic_terms(asin: str, page: int = 1, site: str = "US") -> dict | None:
    return await call("product_traffic_terms", {"asin": asin, "amz_site": site, "page": page})

async def keyword_extends(kw: str, page: int = 1, site: str = "US") -> dict | None:
    return await call("keyword_extends", {"keyword": kw, "keyword_support_site": site, "page": page})
