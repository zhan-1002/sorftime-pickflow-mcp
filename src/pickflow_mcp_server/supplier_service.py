"""Deterministic 1688 supplier candidate processing.

Normalises raw Sorftime 1688 API responses, deduplicates candidates, applies
hard mismatch gates, and produces comparisons with a VisualReviewBundle.

This module does not call an LLM, control a browser, or invoke a vision API.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from .supplier_contracts import (
    EvidenceDimension,
    EvidenceSource,
    EvidenceStatus,
    LookupStatus,
    MatchEvidence,
    MatchVerdict,
    PriceComparability,
    PriceTier,
    ProductFingerprint,
    SupplierCandidate,
    SupplierComparison,
    SupplierLookupResult,
    VisualReviewBundle,
    VisualReviewPair,
)

MAX_REVIEW_CANDIDATES = 5


def _digest_id(*parts: str | None) -> str:
    """Deterministic 12-hex-char digest from one or more string-ish inputs."""
    joined = "::".join(p or "" for p in parts)
    return hashlib.md5(joined.encode("utf-8")).hexdigest()[:12]


def _pick_str(data: dict[str, Any], *keys: str) -> str | None:
    """Return the first non-empty string value for any of *keys*."""
    for k in keys:
        v = data.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return None


def _pick_float(data: dict[str, Any], *keys: str) -> float | None:
    """Return the first positive float value for any of *keys*."""
    for k in keys:
        v = data.get(k)
        if v is not None and v != "":
            try:
                f = float(v)
                if f > 0:
                    return f
            except (TypeError, ValueError):
                continue
    return None


def _extract_items(result: dict | None) -> list[dict[str, Any]]:
    """Pull the item list from a Sorftime 1688 API result, tolerating varied shapes."""
    if result is None:
        return []
    if isinstance(result, dict) and result.get("_error"):
        return []
    data = result.get("data") if isinstance(result, dict) else None
    if data is None:
        return []
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("items", "list", "rows", "products", "data"):
            items = data.get(key)
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
    return []


# ---------------------------------------------------------------------------
# Price-tier string helpers
# ---------------------------------------------------------------------------

_PURCHASE_QTY_RE = re.compile(r"(\d+(?:\.\d+)?)")


def _parse_price_str(raw: Any) -> float | None:
    """Parse a price value that may be a string (real field) or number."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw) if float(raw) > 0 else None
    try:
        f = float(str(raw).strip())
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def _parse_purchase_quantity(raw: Any) -> tuple[float | None, str | None]:
    """Parse a purchase_quantity string into (min_numeric, original_label).

    Real values can be "100", "100pcs", ">=100", "100-500", "100~500",
    "100 pieces", ">=100", etc.  Extract the first positive numeric value
    as the tier minimum; preserve the original string as raw_text.
    """
    if raw is None:
        return None, None
    if isinstance(raw, (int, float)):
        f = float(raw)
        return (f if f > 0 else None), str(raw)
    label = str(raw).strip()
    if not label:
        return None, None
    m = _PURCHASE_QTY_RE.search(label)
    if not m:
        return None, label
    try:
        qty = float(m.group(1))
        return (qty if qty > 0 else None), label
    except (TypeError, ValueError):
        return None, label


# ---------------------------------------------------------------------------
# Price-tier normalisation
# ---------------------------------------------------------------------------


def _normalize_price_tiers(raw: Any) -> list[PriceTier]:
    """Convert raw wholesale_price_range data into PriceTier models.

    Real rows use ``wholesale_price_range`` with ``price`` and
    ``purchase_quantity`` per entry -- both may be strings.
    ``purchase_quantity`` can contain prefixes, suffixes, or ranges
    (e.g. ``"100pcs"``, ``"100-500"``, ``">=200"``).  The first
    positive numeric is extracted as ``min_quantity``; the original
    label is preserved in ``raw_text``.
    """
    tiers: list[PriceTier] = []
    if raw is None:
        return tiers

    entries: list[dict] = []
    if isinstance(raw, list):
        entries = raw
    elif isinstance(raw, dict):
        nested = raw.get("tiers") or raw.get("wholesale_price_range") or raw.get("price_range")
        if isinstance(nested, list):
            entries = nested
        else:
            return tiers
    else:
        return tiers

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        price = _parse_price_str(entry.get("price"))
        min_qty, qty_label = _parse_purchase_quantity(entry.get("purchase_quantity"))
        if min_qty is None or price is None:
            continue
        unit = str(entry.get("unit") or entry.get("unit_type") or "").strip() or None
        # Preserve purchase_quantity label as the primary raw_text
        raw_text = qty_label or str(entry.get("raw_text") or entry.get("text") or "").strip() or None
        tiers.append(
            PriceTier(
                min_quantity=min_qty,
                unit_price_cny=price,
                unit=unit,
                raw_text=raw_text,
            )
        )
    return tiers


# ---------------------------------------------------------------------------
# Candidate normalisation
# ---------------------------------------------------------------------------

# Supplier signals extracted from 1688 row (keyword rows + image rows)
_SUPPLIER_SIGNAL_KEYS = (
    "service_score",
    "service_score_detail",
    "online_date",
    "sales_of_30d",
    "repurchase_rate",
    "shipping_origin",
    "review_count",
    "score",
    "sku_count",
    "seller_identities",
    "offer_identities",
    "is_drop_shipping",
)


def _normalize_one(row: dict[str, Any], search_mode: str) -> SupplierCandidate | None:
    """Normalise one raw 1688 item into a SupplierCandidate, or None if unusable.

    Real keyword rows use: title, photo, url, price (legacy), product_id,
    store_name (sometimes absent), service_score, service_score_detail,
    online_date, sales_of_30d, wholesale_price_range, repurchase_rate,
    shipping_origin, review_count, score, sku_count.

    Real image rows may additionally include: seller_identities,
    offer_identities, min_order_quantity, is_drop_shipping.

    ``wholesale_price_range`` entries use ``price`` and ``purchase_quantity``.
    """
    title = _pick_str(row, "title", "product_name", "name", "offer_title")
    if not title:
        return None

    # Real field: url
    detail_url = _pick_str(row, "url", "detail_url", "product_url", "offer_url", "link")

    # Real fields: product_id, store_name (may be absent)
    offer_id = _pick_str(row, "product_id", "offer_id", "id")
    supplier_name = _pick_str(row, "store_name", "supplier_name", "company_name", "seller_name", "shop_name")

    # Real field: min_order_quantity (image rows; keyword rows may not have it)
    moq = _pick_float(row, "min_order_quantity", "moq", "min_order", "minimum_order")

    # Images: real field ``photo``
    image_urls: list[str] = []
    main = _pick_str(row, "photo", "main_image", "image_url", "image", "picture_url")
    if main:
        image_urls.append(main)
    extra = row.get("extra_images") or row.get("images") or row.get("image_list") or []
    if isinstance(extra, list):
        for u in extra:
            if isinstance(u, str) and u.strip() and u.strip() not in image_urls:
                image_urls.append(u.strip())
    elif isinstance(extra, str) and extra.strip() and extra.strip() not in image_urls:
        image_urls.append(extra.strip())

    # Real field: wholesale_price_range
    price_tiers = _normalize_price_tiers(
        row.get("wholesale_price_range") or row.get("price_tiers")
        or row.get("price_range") or row.get("prices") or row.get("price")
    )

    # Pack quantity (rarely available from 1688 API rows)
    pack_qty = _pick_float(row, "pack_quantity", "pack_qty", "unit_count", "pieces_per_lot")
    pack_unit = _pick_str(row, "pack_unit", "unit", "unit_type")

    # Supplier signals
    signals: dict[str, Any] = {}
    for sig_key in _SUPPLIER_SIGNAL_KEYS:
        v = row.get(sig_key)
        if v is not None:
            signals[sig_key] = v

    # --- Price comparability ---
    # The lowest displayed price is never exact/comparable merely because a
    # pack_quantity exists. Real 16-field rows stay AMBIGUOUS.
    # NORMALIZED only when the row carries a synthetic _per_pack_pricing flag
    # and pack_qty is known (divide displayed price by pack_qty).
    price_comp = PriceComparability.UNAVAILABLE
    normalized_price: float | None = None
    if price_tiers:
        price_comp = PriceComparability.AMBIGUOUS
        if pack_qty is not None and pack_qty > 0 and row.get("_per_pack_pricing"):
            lowest_tier = min(price_tiers, key=lambda t: t.unit_price_cny)
            normalized_price = round(lowest_tier.unit_price_cny / pack_qty, 4)
            price_comp = PriceComparability.NORMALIZED

    # Candidate ID from deterministic digest
    candidate_id = _digest_id(detail_url, supplier_name, title)

    # Missing critical fields
    missing_fields: list[str] = []
    if not supplier_name:
        missing_fields.append("supplier_name")
    if not image_urls:
        missing_fields.append("image_urls")
    if not price_tiers:
        missing_fields.append("price_tiers")
    if moq is None:
        missing_fields.append("moq")

    # Data completeness (5 critical fields)
    total_critical = 5  # title, supplier_name, image_urls, moq, price_tiers
    present = 5 - len(missing_fields)
    if not title:
        present -= 1
    completeness = round(present / total_critical * 100, 1)

    # Source fields populated
    source_fields = [k for k in row if k in (
        "photo", "url", "product_id", "store_name", "title", "price",
        "wholesale_price_range", "min_order_quantity", "service_score",
        "service_score_detail", "online_date", "sales_of_30d", "repurchase_rate",
        "shipping_origin", "review_count", "score", "sku_count",
        "seller_identities", "offer_identities", "is_drop_shipping",
        "pack_quantity", "pack_unit",
    ) and row.get(k) is not None]

    return SupplierCandidate(
        candidate_id=candidate_id,
        offer_id=str(offer_id) if offer_id else None,
        title=title,
        detail_url=detail_url,
        supplier_name=supplier_name,
        image_urls=image_urls,
        search_modes=[search_mode],
        price_tiers=price_tiers,
        moq=moq,
        pack_quantity=pack_qty,
        pack_unit=pack_unit,
        normalized_unit_price_cny=normalized_price,
        price_comparability=price_comp,
        supplier_signals=signals,
        source_fields=source_fields,
        missing_fields=missing_fields,
        data_completeness_pct=completeness,
    )


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def normalize_candidates(
    image_results: dict | None,
    keyword_results: dict | None,
) -> list[SupplierCandidate]:
    """Extract, normalise, and deduplicate candidates from both search methods.

    Merges by ``detail_url`` (preferred) or supplier-name + title digest.
    Preserves all search origins in ``search_modes``.
    """
    image_items = _extract_items(image_results)
    keyword_items = _extract_items(keyword_results)

    merged: dict[str, SupplierCandidate] = {}

    for source_items, mode in [
        (image_items, "image_search"),
        (keyword_items, "keyword_search"),
    ]:
        for row in source_items:
            cand = _normalize_one(row, mode)
            if cand is None:
                continue
            key = cand.detail_url or _digest_id(cand.supplier_name, cand.title)
            if key in merged:
                existing = merged[key]
                existing.search_modes = sorted(set(existing.search_modes) | {mode})
                if not existing.image_urls and cand.image_urls:
                    existing.image_urls = cand.image_urls
                if not existing.price_tiers and cand.price_tiers:
                    existing.price_tiers = cand.price_tiers
                    existing.price_comparability = cand.price_comparability
                    existing.normalized_unit_price_cny = cand.normalized_unit_price_cny
            else:
                merged[key] = cand

    return list(merged.values())


# ---------------------------------------------------------------------------
# Deterministic comparison
# ---------------------------------------------------------------------------


def _evidence_form_in_title(
    fp_product_form: str | None,
    cand_title: str,
) -> MatchEvidence:
    """Check whether fingerprint product_form appears in candidate title.

    Title semantics CANNOT create a structured hard failure.
    """
    dim = EvidenceDimension.PRODUCT_FORM
    if fp_product_form is None:
        return MatchEvidence(
            dimension=dim,
            status=EvidenceStatus.UNKNOWN,
            confidence=0.0,
            summary="product_form: unknown in fingerprint",
            source=EvidenceSource.SORFTIME_1688,
            candidate_value=cand_title[:200],
        )
    fp_norm = fp_product_form.strip().casefold()
    title_norm = cand_title.strip().casefold()
    if fp_norm in title_norm:
        return MatchEvidence(
            dimension=dim,
            status=EvidenceStatus.MATCH,
            confidence=0.65,
            summary=f"product_form '{fp_product_form}' found in candidate title (soft signal)",
            source=EvidenceSource.SORFTIME_1688,
            reference_value=fp_product_form,
            candidate_value=cand_title[:200],
        )
    return MatchEvidence(
        dimension=dim,
        status=EvidenceStatus.UNKNOWN,
        confidence=0.0,
        summary=f"product_form '{fp_product_form}' not in candidate title -- need visual/browser verification",
        source=EvidenceSource.SORFTIME_1688,
        reference_value=fp_product_form,
        candidate_value=cand_title[:200],
    )


def _evidence_material_in_title(
    fp_materials: list[str],
    cand_title: str,
) -> list[MatchEvidence]:
    """Check whether any fingerprint material appears in candidate title.

    Material absent from title is UNKNOWN, not a hard mismatch.
    Title semantics cannot create a structured hard failure.
    """
    dim = EvidenceDimension.MATERIAL
    if not fp_materials:
        return [
            MatchEvidence(
                dimension=dim,
                status=EvidenceStatus.UNKNOWN,
                confidence=0.0,
                summary="material: unknown in fingerprint",
                source=EvidenceSource.SORFTIME_1688,
                candidate_value=cand_title[:200],
            )
        ]

    title_lower = cand_title.casefold()
    found: list[str] = []
    not_found: list[str] = []
    for m in fp_materials:
        if m.casefold() in title_lower:
            found.append(m)
        else:
            not_found.append(m)

    results: list[MatchEvidence] = []
    for m in found:
        results.append(
            MatchEvidence(
                dimension=dim,
                status=EvidenceStatus.MATCH,
                confidence=0.65,
                summary=f"material '{m}' found in candidate title (soft signal)",
                source=EvidenceSource.SORFTIME_1688,
                reference_value=m,
                candidate_value=cand_title[:200],
            )
        )
    for m in not_found:
        results.append(
            MatchEvidence(
                dimension=dim,
                status=EvidenceStatus.UNKNOWN,
                confidence=0.0,
                summary=f"material '{m}' not in candidate title -- need visual/browser verification",
                source=EvidenceSource.SORFTIME_1688,
                reference_value=m,
                candidate_value=cand_title[:200],
            )
        )
    return results


def _evidence_color_in_title(
    fp_colors: list[str],
    cand_title: str,
) -> list[MatchEvidence]:
    """Check whether any fingerprint color appears in candidate title.

    Never a hard constraint -- soft signal only.
    """
    dim = EvidenceDimension.COLOR_PATTERN
    if not fp_colors:
        return []

    title_lower = cand_title.casefold()
    results: list[MatchEvidence] = []
    for c in fp_colors:
        if c.casefold() in title_lower:
            results.append(
                MatchEvidence(
                    dimension=dim,
                    status=EvidenceStatus.MATCH,
                    confidence=0.6,
                    summary=f"color '{c}' found in candidate title (soft signal)",
                    source=EvidenceSource.SORFTIME_1688,
                    reference_value=c,
                    candidate_value=cand_title[:200],
                )
            )
    if not results:
        results.append(
            MatchEvidence(
                dimension=dim,
                status=EvidenceStatus.UNKNOWN,
                confidence=0.0,
                summary="color: not found in candidate title",
                source=EvidenceSource.SORFTIME_1688,
                reference_value=", ".join(fp_colors),
                candidate_value=cand_title[:200],
            )
        )
    return results


def _evidence_quantity(
    fp_qty: float | None,
    fp_unit: str | None,
    cand_qty: float | None,
    cand_unit: str | None,
) -> list[MatchEvidence]:
    """Compare package quantity.

    Hard failure only when BOTH sides have structured quantities AND
    compatible units whose values differ.  If units are incompatible
    (e.g. "pieces" vs "sets") the mismatch is not provable.
    """
    dim = EvidenceDimension.QUANTITY
    if fp_qty is None and cand_qty is None:
        return []
    if fp_qty is None:
        return [
            MatchEvidence(
                dimension=dim,
                status=EvidenceStatus.UNKNOWN,
                confidence=0.0,
                summary="quantity: unknown in fingerprint",
                source=EvidenceSource.SORFTIME_1688,
                candidate_value=f"{cand_qty} {cand_unit or ''}".strip() if cand_qty else None,
            )
        ]
    if cand_qty is None:
        return [
            MatchEvidence(
                dimension=dim,
                status=EvidenceStatus.UNKNOWN,
                confidence=0.0,
                summary="quantity: unknown in candidate",
                source=EvidenceSource.SORFTIME_PRODUCT_DETAIL,
                reference_value=f"{fp_qty} {fp_unit or ''}".strip(),
            )
        ]

    # Both sides have structured quantities
    fp_unit_norm = (fp_unit or "").strip().casefold()
    cand_unit_norm = (cand_unit or "").strip().casefold()

    if fp_unit_norm and cand_unit_norm and fp_unit_norm != cand_unit_norm:
        # Incompatible units -- cannot prove match or mismatch
        return [
            MatchEvidence(
                dimension=dim,
                status=EvidenceStatus.UNKNOWN,
                confidence=0.0,
                summary=f"quantity: units differ (fingerprint={fp_unit}, candidate={cand_unit}) -- not comparable",
                source=EvidenceSource.SORFTIME_1688,
                reference_value=f"{fp_qty} {fp_unit}",
                candidate_value=f"{cand_qty} {cand_unit}",
            )
        ]

    if fp_qty == cand_qty:
        return [
            MatchEvidence(
                dimension=dim,
                status=EvidenceStatus.MATCH,
                confidence=0.9,
                summary=f"quantity matches: {fp_qty} {fp_unit or cand_unit or ''}".strip(),
                source=EvidenceSource.SORFTIME_1688,
                reference_value=f"{fp_qty} {fp_unit or ''}".strip(),
                candidate_value=f"{cand_qty} {cand_unit or ''}".strip(),
            )
        ]

    return [
        MatchEvidence(
            dimension=dim,
            status=EvidenceStatus.MISMATCH,
            confidence=0.95,
            hard_constraint=True,
            summary=f"quantity differs: fingerprint={fp_qty}{fp_unit or ''}, candidate={cand_qty}{cand_unit or ''}",
            source=EvidenceSource.SORFTIME_1688,
            reference_value=f"{fp_qty} {fp_unit or ''}".strip(),
            candidate_value=f"{cand_qty} {cand_unit or ''}".strip(),
        )
    ]


def _evidence_dimensions(fp_dims: Any) -> list[MatchEvidence]:
    """Dimension evidence -- candidates rarely carry structured dimensions."""
    dim = EvidenceDimension.DIMENSIONS
    if fp_dims is None:
        return [
            MatchEvidence(
                dimension=dim,
                status=EvidenceStatus.UNKNOWN,
                confidence=0.0,
                summary="dimensions: unknown in fingerprint",
                source=EvidenceSource.SORFTIME_1688,
            )
        ]
    return [
        MatchEvidence(
            dimension=dim,
            status=EvidenceStatus.UNKNOWN,
            confidence=0.0,
            summary="dimensions: candidate dimensions unavailable -- requires browser verification",
            source=EvidenceSource.SORFTIME_PRODUCT_DETAIL,
        )
    ]


def _evidence_price(candidate: SupplierCandidate) -> MatchEvidence:
    """Generate price-comparability evidence."""
    dim = EvidenceDimension.PRICE_COMPARABILITY
    if candidate.price_comparability == PriceComparability.NORMALIZED:
        return MatchEvidence(
            dimension=dim,
            status=EvidenceStatus.MATCH,
            confidence=0.7,
            summary=f"price normalized from per-pack pricing: {candidate.normalized_unit_price_cny} CNY/unit",
            source=EvidenceSource.SORFTIME_1688,
            candidate_value=str(candidate.normalized_unit_price_cny),
        )
    if candidate.price_comparability == PriceComparability.AMBIGUOUS:
        return MatchEvidence(
            dimension=dim,
            status=EvidenceStatus.UNKNOWN,
            confidence=0.0,
            summary="price comparability is ambiguous -- do not calculate FBA profit from this price",
            source=EvidenceSource.SORFTIME_1688,
        )
    return MatchEvidence(
        dimension=dim,
        status=EvidenceStatus.UNKNOWN,
        confidence=0.0,
        summary="price unavailable",
        source=EvidenceSource.SORFTIME_1688,
    )


def _evidence_title_similarity(fp_title: str, cand_title: str) -> MatchEvidence:
    """Lightweight Jaccard-based title similarity signal. Never hard."""
    dim = EvidenceDimension.TITLE_SEMANTICS
    fp_words = set(fp_title.casefold().split())
    cand_words = set(cand_title.casefold().split())
    if not fp_words or not cand_words:
        return MatchEvidence(
            dimension=dim,
            status=EvidenceStatus.UNKNOWN,
            confidence=0.0,
            summary="title semantics: insufficient text to compare",
            source=EvidenceSource.SORFTIME_1688,
        )
    overlap = fp_words & cand_words
    jaccard = len(overlap) / len(fp_words | cand_words) if (fp_words | cand_words) else 0
    if jaccard >= 0.5:
        return MatchEvidence(
            dimension=dim,
            status=EvidenceStatus.MATCH,
            confidence=min(jaccard, 1.0),
            summary=f"title semantics: {len(overlap)} shared words, Jaccard={jaccard:.2f}",
            source=EvidenceSource.SORFTIME_1688,
            reference_value=fp_title[:200],
            candidate_value=cand_title[:200],
        )
    if jaccard >= 0.2:
        return MatchEvidence(
            dimension=dim,
            status=EvidenceStatus.UNKNOWN,
            confidence=0.0,
            summary=f"title semantics: partial overlap, Jaccard={jaccard:.2f}",
            source=EvidenceSource.SORFTIME_1688,
            reference_value=fp_title[:200],
            candidate_value=cand_title[:200],
        )
    return MatchEvidence(
        dimension=dim,
        status=EvidenceStatus.UNKNOWN,
        confidence=0.0,
        summary=f"title semantics: low overlap, Jaccard={jaccard:.2f}",
        source=EvidenceSource.SORFTIME_1688,
        reference_value=fp_title[:200],
        candidate_value=cand_title[:200],
    )


# ---------------------------------------------------------------------------
# Top-level comparison entry point
# ---------------------------------------------------------------------------


def compare_fingerprint_to_candidates(
    fingerprint: ProductFingerprint,
    candidates: list[SupplierCandidate],
) -> tuple[list[SupplierComparison], VisualReviewBundle]:
    """Apply deterministic gates and produce comparisons plus a visual review bundle.

    Returns at most ``MAX_REVIEW_CANDIDATES`` non-rejected candidates.
    """
    comparisons: list[SupplierComparison] = []

    for idx, cand in enumerate(candidates):
        comparison_id = f"cmp-{fingerprint.asin}-{idx:03d}"
        evidence: list[MatchEvidence] = []
        hard_failures: list[str] = []
        unknown_dims: list[EvidenceDimension] = []

        # --- Product form (title-based, soft only) ---
        fev = _evidence_form_in_title(fingerprint.product_form, cand.title)
        evidence.append(fev)
        if fev.status == EvidenceStatus.UNKNOWN:
            unknown_dims.append(EvidenceDimension.PRODUCT_FORM)

        # --- Material (title-based, soft only) ---
        mevs = _evidence_material_in_title(fingerprint.materials, cand.title)
        for mev in mevs:
            evidence.append(mev)
            if mev.status == EvidenceStatus.UNKNOWN:
                unknown_dims.append(EvidenceDimension.MATERIAL)

        # --- Color (title-based, soft only) ---
        cevs = _evidence_color_in_title(fingerprint.colors, cand.title)
        evidence.extend(cevs)
        for cev in cevs:
            if cev.status == EvidenceStatus.UNKNOWN:
                unknown_dims.append(EvidenceDimension.COLOR_PATTERN)

        # --- Quantity (hard only when both structured + compatible units) ---
        qevs = _evidence_quantity(
            fingerprint.package_quantity, fingerprint.package_unit,
            cand.pack_quantity, cand.pack_unit,
        )
        for qev in qevs:
            evidence.append(qev)
            if qev.status == EvidenceStatus.MISMATCH and qev.hard_constraint:
                hard_failures.append("quantity")
            if qev.status == EvidenceStatus.UNKNOWN:
                unknown_dims.append(EvidenceDimension.QUANTITY)

        # --- Dimensions ---
        devs = _evidence_dimensions(fingerprint.dimensions)
        evidence.extend(devs)
        for dev in devs:
            if dev.status == EvidenceStatus.UNKNOWN:
                unknown_dims.append(EvidenceDimension.DIMENSIONS)

        # --- Title semantics ---
        tev = _evidence_title_similarity(fingerprint.title, cand.title)
        evidence.append(tev)

        # --- Price comparability ---
        pev = _evidence_price(cand)
        evidence.append(pev)
        if pev.status == EvidenceStatus.UNKNOWN:
            unknown_dims.append(EvidenceDimension.PRICE_COMPARABILITY)

        # --- Visual identity ---
        if cand.image_urls:
            evidence.append(
                MatchEvidence(
                    dimension=EvidenceDimension.VISUAL_IDENTITY,
                    status=EvidenceStatus.UNKNOWN,
                    confidence=0.0,
                    summary=f"visual review needed -- {len(cand.image_urls)} candidate image(s) available",
                    source=EvidenceSource.SORFTIME_1688,
                )
            )
            unknown_dims.append(EvidenceDimension.VISUAL_IDENTITY)
        else:
            evidence.append(
                MatchEvidence(
                    dimension=EvidenceDimension.VISUAL_IDENTITY,
                    status=EvidenceStatus.UNKNOWN,
                    confidence=0.0,
                    summary="visual review blocked -- no candidate images available",
                    source=EvidenceSource.SORFTIME_1688,
                )
            )
            unknown_dims.append(EvidenceDimension.VISUAL_IDENTITY)

        # --- Packaging ---
        evidence.append(
            MatchEvidence(
                dimension=EvidenceDimension.PACKAGING,
                status=EvidenceStatus.UNKNOWN,
                confidence=0.0,
                summary="packaging: requires browser verification",
                source=EvidenceSource.SORFTIME_1688,
            )
        )
        unknown_dims.append(EvidenceDimension.PACKAGING)

        # --- Verdict ---
        if hard_failures:
            verdict = MatchVerdict.DIFFERENT
        elif any(e.status == EvidenceStatus.MATCH for e in evidence) and not any(
            e.status == EvidenceStatus.MISMATCH and e.hard_constraint for e in evidence
        ):
            verdict = MatchVerdict.POSSIBLE_SAME
        else:
            verdict = MatchVerdict.INSUFFICIENT

        # --- Overall score ---
        match_count = sum(1 for e in evidence if e.status == EvidenceStatus.MATCH)
        mismatch_count = sum(1 for e in evidence if e.status == EvidenceStatus.MISMATCH)
        total_checks = max(len(evidence), 1)
        overall = round(
            (match_count * 100 / total_checks) - (mismatch_count * 30 / total_checks)
        )
        overall = max(0.0, min(100.0, overall))

        requires_browser = (
            bool(unknown_dims)
            or verdict in (MatchVerdict.POSSIBLE_SAME, MatchVerdict.INSUFFICIENT)
        )

        warnings: list[str] = []
        if cand.price_comparability == PriceComparability.AMBIGUOUS:
            warnings.append(
                "Price comparability is ambiguous -- do not calculate FBA profit from "
                "this price without browser verification."
            )
        if not cand.image_urls:
            warnings.append("No candidate images available for visual comparison.")
        if not cand.price_tiers:
            warnings.append("No price data -- sourcing cost unknown.")

        comparisons.append(
            SupplierComparison(
                comparison_id=comparison_id,
                asin=fingerprint.asin,
                candidate_id=cand.candidate_id,
                verdict=verdict,
                overall_score=overall,
                evidence=evidence,
                hard_failures=hard_failures,
                unknown_dimensions=unknown_dims,
                normalized_unit_price_cny=cand.normalized_unit_price_cny,
                price_comparability=cand.price_comparability,
                requires_browser_review=requires_browser,
                warnings=warnings,
            )
        )

    # --- VisualReviewBundle: at most MAX_REVIEW_CANDIDATES non-rejected ---
    non_rejected = [
        (c, comp)
        for c, comp in zip(candidates, comparisons)
        if comp.verdict != MatchVerdict.DIFFERENT
    ]
    non_rejected.sort(key=lambda pair: pair[1].overall_score, reverse=True)
    top_n = non_rejected[:MAX_REVIEW_CANDIDATES]

    ref_image_urls = [img.url for img in fingerprint.images]

    pairs: list[VisualReviewPair] = []
    for cand, comp in top_n:
        if ref_image_urls and cand.image_urls:
            focus = [d for d in comp.unknown_dimensions if d != EvidenceDimension.VISUAL_IDENTITY]
            pairs.append(
                VisualReviewPair(
                    candidate_id=cand.candidate_id,
                    reference_images=list(ref_image_urls),
                    candidate_images=list(cand.image_urls),
                    focus_dimensions=list(focus),
                )
            )

    bundle = VisualReviewBundle(
        asin=fingerprint.asin,
        pairs=pairs,
    )

    return comparisons, bundle


# ---------------------------------------------------------------------------
# Result builder
# ---------------------------------------------------------------------------


def build_lookup_result(
    status: LookupStatus,
    fingerprint: ProductFingerprint | None = None,
    candidates: list[SupplierCandidate] | None = None,
    comparisons: list[SupplierComparison] | None = None,
    bundle: VisualReviewBundle | None = None,
    api_calls: int = 0,
    warnings: list[str] | None = None,
    error_code: str | None = None,
) -> SupplierLookupResult:
    """Pack all outputs into a SupplierLookupResult."""
    return SupplierLookupResult(
        status=status,
        fingerprint=fingerprint,
        candidates=candidates or [],
        deterministic_comparisons=comparisons or [],
        visual_review=bundle,
        api_calls=api_calls,
        warnings=warnings or [],
        error_code=error_code,
    )
