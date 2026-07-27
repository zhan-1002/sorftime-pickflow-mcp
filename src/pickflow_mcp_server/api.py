"""Async Sorftime MCP API client with retries and normalized pagination."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass
from typing import Any

import httpx

from .config import SWEETSPOT, get_api_url

# Sorftime authenticates through the endpoint query string. httpx's INFO
# request log includes the complete URL, so keep transport logging at WARNING
# even when the MCP host configures the root logger at INFO.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

DEFAULT_TIMEOUT_SECONDS = 25.0
DEFAULT_RETRIES = 3
NO_DATA_MARKERS = (
    "no relevant data",
    "no related products found",
    "no data available",
    "暂无数据",
    "没有相关数据",
)


class SorftimeAPIError(RuntimeError):
    """Raised when the remote MCP endpoint cannot return a usable response."""


@dataclass(frozen=True)
class TrafficTermsResult:
    items: list[dict]
    pages_requested: int
    pages_succeeded: int
    page_errors: list[dict[str, Any]]
    complete: bool
    duplicates_removed: int

    @property
    def available(self) -> bool:
        return self.pages_succeeded > 0

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["available"] = self.available
        return result


def is_no_data_result(result: dict | None) -> bool:
    """Return true when a tool reports an empty page as plain-text content."""
    if not isinstance(result, dict):
        return False
    error = result.get("_error")
    if not isinstance(error, dict) or error.get("code") != "NON_JSON_TEXT":
        return False
    message = str(error.get("message", "")).strip().casefold()
    return any(marker in message for marker in NO_DATA_MARKERS)


def _parse_mcp_response(raw: str) -> dict | None:
    for line in raw.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        decoded = json.loads(payload)
        if "error" in decoded:
            return {"_error": decoded["error"]}
        for item in decoded.get("result", {}).get("content", []):
            if item.get("type") == "text":
                text = item.get("text", "")
                if not text:
                    return None
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return {
                        "_error": {
                            "code": "NON_JSON_TEXT",
                            "message": text[:500],
                        }
                    }
    return None


async def call(
    method: str,
    params: dict,
    rid: int = 1,
    *,
    retries: int = DEFAULT_RETRIES,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict | None:
    """Call the Sorftime MCP endpoint and retry transient transport failures."""
    api_url = get_api_url()
    if not api_url:
        raise RuntimeError("Sorftime API not configured")

    payload = {
        "jsonrpc": "2.0",
        "id": rid,
        "method": "tools/call",
        "params": {"name": method, "arguments": params},
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    last_error: Exception | None = None
    attempts = max(1, retries)

    for attempt in range(attempts):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(api_url, json=payload, headers=headers)
                response.raise_for_status()
            parsed = _parse_mcp_response(response.text)
            if parsed is None:
                raise SorftimeAPIError("Sorftime returned no JSON-RPC data payload")
            return parsed
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError, SorftimeAPIError) as exc:
            last_error = exc
            if attempt + 1 >= attempts:
                break
            await asyncio.sleep(0.4 * (2**attempt))
        except httpx.HTTPStatusError as exc:
            last_error = exc
            status = exc.response.status_code
            if status not in {408, 425, 429, 500, 502, 503, 504} or attempt + 1 >= attempts:
                break
            retry_after = exc.response.headers.get("Retry-After")
            try:
                delay = min(float(retry_after), 5.0) if retry_after else 0.4 * (2**attempt)
            except ValueError:
                delay = 0.4 * (2**attempt)
            await asyncio.sleep(delay)

    raise SorftimeAPIError(f"Sorftime call {method!r} failed after {attempts} attempt(s)") from last_error


# ---------- Convenience wrappers ----------

async def keyword_detail(kw: str, site: str = "US") -> dict | None:
    return await call("keyword_detail", {"keyword": kw, "keyword_support_site": site})


async def keyword_list(page: int, site: str = "US") -> dict | None:
    return await call("keyword_list", {"keyword_support_site": site, "page": page})


async def product_search(search_name: str, **overrides) -> dict | None:
    params = {**SWEETSPOT, "search_name": search_name, "page": 1}
    params.update(overrides)
    return await call("product_search", params)


async def product_detail(asin: str, site: str = "US") -> dict | None:
    return await call("product_detail", {"asin": asin, "amz_site": site})


async def product_traffic_terms(asin: str, page: int = 1, site: str = "US") -> dict | None:
    return await call("product_traffic_terms", {"asin": asin, "amz_site": site, "page": page})


def _items_from_result(result: dict | None) -> list[dict] | None:
    if is_no_data_result(result):
        return []
    if result is None or result.get("_error"):
        return None
    data = result.get("data", [])
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("items", "list", "rows", "data"):
            items = data.get(key)
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
    return []


def _traffic_key(item: dict) -> str:
    for key in ("keyword", "keyword_name", "search_term", "name"):
        value = item.get(key)
        if value:
            return f"keyword:{str(value).strip().casefold()}"
    return "row:" + json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)


async def product_traffic_terms_all(
    asin: str,
    *,
    max_pages: int = 2,
    site: str = "US",
) -> TrafficTermsResult:
    """Fetch, combine and de-duplicate traffic terms across pages.

    Partial results remain usable, but ``complete`` is false and page failures
    are exposed so scoring can distinguish missing data from a true empty list.
    """
    if max_pages < 1:
        raise ValueError("max_pages must be at least 1")

    combined: list[dict] = []
    seen: set[str] = set()
    errors: list[dict[str, Any]] = []
    pages_requested = 0
    pages_succeeded = 0
    duplicates_removed = 0

    for page in range(1, max_pages + 1):
        pages_requested += 1
        try:
            result = await product_traffic_terms(asin, page=page, site=site)
            items = _items_from_result(result)
            if items is None:
                error = result.get("_error") if isinstance(result, dict) else "empty response"
                errors.append({"page": page, "error": error})
                continue
            pages_succeeded += 1
            for item in items:
                key = _traffic_key(item)
                if key in seen:
                    duplicates_removed += 1
                    continue
                seen.add(key)
                combined.append(item)
            if not items:
                break
        except Exception as exc:
            errors.append({"page": page, "error": type(exc).__name__})

    return TrafficTermsResult(
        items=combined,
        pages_requested=pages_requested,
        pages_succeeded=pages_succeeded,
        page_errors=errors,
        complete=not errors,
        duplicates_removed=duplicates_removed,
    )


async def keyword_extends(kw: str, page: int = 1, site: str = "US") -> dict | None:
    return await call("keyword_extends", {"keyword": kw, "keyword_support_site": site, "page": page})


# ---------- 1688 sourcing endpoints ----------

async def ali1688_similar_product(search_name: str, page: int = 1, site: str = "1688") -> dict | None:
    """Search 1688.com for supplier listings matching a product keyword."""
    return await call("ali1688_similar_product", {"search_name": search_name, "page": page, "site": site})


async def ali1688_product_search_from_image(image_url: str, page: int = 1, site: str = "1688") -> dict | None:
    """Search 1688.com by image URL for visually similar supplier listings."""
    return await call("ali1688_product_search_from_image", {"image_url": image_url, "page": page, "site": site})
