"""Deterministic ProductFingerprint extraction from Sorftime product_detail responses.

Maps known field aliases into a normalized ProductFingerprint. Tolerates missing
and renamed fields without inventing values. Reports critical unknowns and a
defensible completeness score.

This module is internal -- only the contract models cross the stable boundary.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .supplier_contracts import (
    EvidenceSource,
    FingerprintAttribute,
    ImageRole,
    Measurement,
    ProductDimensions,
    ProductFingerprint,
    ProductImage,
    SourceReference,
)

# ---------------------------------------------------------------------------
# Field alias map: canonical name -> tuple of recognised source-field names
# ---------------------------------------------------------------------------
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "title": (
        "title",
        "product_title",
        "item_name",
        "name",
    ),
    "brand": (
        "brand",
        "brand_name",
        "manufacturer",
        "byline",
    ),
    "category": (
        "category",
        "product_category",
        "cate_name",
        "product_group",
        "category_path",
        "top_category",
        "subcategory",
    ),
    "product_form": (
        "product_type",
        "product_form",
        "item_type_name",
        "type",
        "sub_type",
    ),
    "material": (
        "material",
        "main_material",
        "fabric_type",
        "material_type",
    ),
    "color": (
        "color",
        "colour",
        "color_name",
        "colour_name",
    ),
    "components": (
        "components",
        "package_contents",
        "included_components",
        "includes",
        "whats_in_the_box",
    ),
    "model_number": (
        "model_number",
        "model",
        "item_model",
        "part_number",
        "mpn",
        "manufacturer_part_number",
    ),
    "package_quantity": (
        "package_quantity",
        "unit_count",
        "count",
        "number_of_items",
        "item_count",
        "package_size",
    ),
    "package_unit": (
        "package_unit",
        "unit",
        "unit_type",
        "selling_unit",
    ),
    "bullet_points": (
        "bullet_points",
        "features",
        "key_features",
        "feature_bullets",
    ),
    "description": (
        "description",
        "product_description",
        "item_description",
        "detail_description",
    ),
    "variation_text": (
        "variation_text",
        "variation",
        "variant",
        "size_name",
        "style",
        "flavor",
        "scent",
    ),
    "seller": (
        "seller",
        "seller_name",
        "merchant",
        "brand_store_name",
    ),
    # Dimension / measurement fields -- each is a single scalar + optional unit
    "length": (
        "length",
        "item_length",
        "product_length",
    ),
    "width": (
        "width",
        "item_width",
        "product_width",
    ),
    "height": (
        "height",
        "item_height",
        "product_height",
    ),
    "item_weight": (
        "item_weight",
        "weight",
        "product_weight",
    ),
    "package_size_cm": (
        "package_size_cm",
    ),
    "package_weight": (
        "weight_g",
    ),
    # Image fields
    "main_image_url": (
        "main_image",
        "main_image_url",
        "image_url",
        "primary_image",
        "picture_url",
    ),
    "extra_images": (
        "extra_images",
        "images",
        "image_list",
        "gallery_images",
        "image_urls",
        "all_images",
    ),
    "search_terms": (
        "search_terms",
        "keywords",
        "search_keywords",
        "backend_search_terms",
    ),
}

# Fields whose absence triggers a critical-unknown listing
CRITICAL_FIELDS = frozenset(
    {
        "title",
        "product_form",
        "material",
        "package_quantity",
        "dimensions",
    }
)

# Fields counted toward completeness (each present = 1 point, max = total)
COMPLETENESS_FIELDS = (
    "title",
    "brand",
    "category",
    "product_form",
    "material",
    "color",
    "components",
    "model_number",
    "package_quantity",
    "package_unit",
    "bullet_points",
    "description",
    "variation_text",
    "dimensions",
    "item_weight",
    "package_dimensions",
    "package_weight",
    "main_image",
    "extra_images",
    "search_terms",
)


def _resolve_value(data: dict[str, Any], canonical: str) -> Any | None:
    """Return the first non-None value across all aliases for *canonical*."""
    for alias in FIELD_ALIASES.get(canonical, (canonical,)):
        raw = data.get(alias)
        if raw is not None and raw != "":
            return raw
    return None


def _resolve_source(data: dict[str, Any], canonical: str) -> str | None:
    """Return the first alias name that supplied a non-None value."""
    for alias in FIELD_ALIASES.get(canonical, (canonical,)):
        raw = data.get(alias)
        if raw is not None and raw != "":
            return alias
    return None


def _parse_measurement(
    value: Any, unit: str | None = None
) -> Measurement | None:
    """Coerce a numeric value plus optional unit string into a Measurement."""
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric < 0:
        return None
    return Measurement(value=numeric, unit=unit or "cm", raw_text=str(value))


def _parse_list_field(value: Any) -> list[str]:
    """Normalise a list-or-string field into a list of non-empty strings."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if v is not None and str(v).strip()]
    if isinstance(value, str):
        parts = [p.strip() for p in value.replace(";", ",").split(",")]
        return [p for p in parts if p]
    return [str(value).strip()] if str(value).strip() else []


def _parse_images(
    main_url: Any,
    extra_list: Any,
) -> list[ProductImage]:
    """Combine main image and extra images into ProductImage list."""
    images: list[ProductImage] = []
    seen: set[str] = set()

    def _add(url: str, role: ImageRole) -> None:
        key = url.strip()
        if not key or key in seen:
            return
        seen.add(key)
        images.append(ProductImage(url=key, role=role, source=EvidenceSource.SORFTIME_PRODUCT_DETAIL))

    if main_url and isinstance(main_url, str) and main_url.strip():
        _add(main_url.strip(), ImageRole.MAIN)

    if isinstance(extra_list, list):
        for i, u in enumerate(extra_list):
            if isinstance(u, str) and u.strip():
                role = ImageRole.GALLERY if i > 0 else ImageRole.MAIN
                _add(u.strip(), role)
    elif isinstance(extra_list, str):
        for u in extra_list.split(","):
            _add(u.strip(), ImageRole.GALLERY)

    return images


def _extract_item_dimensions(data: dict[str, Any]) -> ProductDimensions | None:
    """Extract item/product dimensions from individual fields or structured dict.

    Does NOT consume package_size_cm -- that belongs to package_dimensions.
    """
    raw_dim = data.get("dimensions") or data.get("product_dimensions") or data.get("item_dimensions")
    if isinstance(raw_dim, dict):
        length = _parse_measurement(
            raw_dim.get("length") or raw_dim.get("Length"),
            (raw_dim.get("length_unit") or raw_dim.get("unit") or "cm"),
        )
        width = _parse_measurement(
            raw_dim.get("width") or raw_dim.get("Width"),
            (raw_dim.get("width_unit") or raw_dim.get("unit") or "cm"),
        )
        height = _parse_measurement(
            raw_dim.get("height") or raw_dim.get("Height"),
            (raw_dim.get("height_unit") or raw_dim.get("unit") or "cm"),
        )
        if length or width or height:
            return ProductDimensions(length=length, width=width, height=height)
        return None

    length = _parse_measurement(_resolve_value(data, "length"))
    width = _parse_measurement(_resolve_value(data, "width"))
    height = _parse_measurement(_resolve_value(data, "height"))
    if length or width or height:
        return ProductDimensions(length=length, width=width, height=height)
    return None


def _extract_package_dimensions(data: dict[str, Any]) -> ProductDimensions | None:
    """Extract package dimensions from package_size_cm (L*W*H string) only."""
    return _parse_package_size_cm(data)


# Recognised attribute keys that can fill primary fields when absent
_ATTR_ALIAS_BRAND = frozenset({"Brand"})
_ATTR_ALIAS_MATERIAL = frozenset({"Material"})
_ATTR_ALIAS_COLOR = frozenset({"Color", "Colour"})
_ATTR_ALIAS_PRODUCT_FORM = frozenset({"Item Type", "Product Type", "Type"})
_ATTR_ALIAS_UNIT_COUNT = frozenset({"Unit Count", "Number of Items", "Package Quantity"})
_ATTR_ALIAS_OCCASION = frozenset({"Occasion", "Theme"})


def _parse_attributes_json(raw: Any) -> tuple[list[FingerprintAttribute], dict[str, str]]:
    """Parse attributes field (JSON string) into FingerprintAttribute list and a flat key-value dict.

    Returns (attributes_list, flat_dict). Flat dict keys are original attribute names.
    """
    if raw is None or not isinstance(raw, str) or not raw.strip():
        return [], {}

    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return [], {}

    if not isinstance(parsed, dict):
        return [], {}

    attrs: list[FingerprintAttribute] = []
    flat: dict[str, str] = {}

    for key, value in parsed.items():
        val_str = str(value).strip() if value is not None else ""
        if not val_str:
            continue
        flat[key] = val_str
        attrs.append(
            FingerprintAttribute(
                name=key,
                value=val_str,
                normalized_value=val_str.casefold(),
                evidence=[
                    SourceReference(
                        source=EvidenceSource.SORFTIME_PRODUCT_DETAIL,
                        field=f"attributes.{key}",
                    )
                ],
            )
        )

    return attrs, flat


def _value_from_attributes(attr_flat: dict[str, str], aliases: frozenset[str]) -> str | None:
    """Return the first value matching any of the recognised attribute key aliases."""
    for key in aliases:
        v = attr_flat.get(key)
        if v:
            return v
    return None


def _parse_package_size_cm(detail_data: dict[str, Any]) -> ProductDimensions | None:
    """Parse package_size_cm in length*width*height form."""
    raw = detail_data.get("package_size_cm")
    if not raw or not isinstance(raw, str):
        return None
    m = re.match(r"(\d+(?:\.\d+)?)\s*\*\s*(\d+(?:\.\d+)?)\s*\*\s*(\d+(?:\.\d+)?)", raw.strip())
    if not m:
        return None
    try:
        length = float(m.group(1))
        width = float(m.group(2))
        height = float(m.group(3))
    except (TypeError, ValueError):
        return None
    return ProductDimensions(
        length=Measurement(value=length, unit="cm", raw_text=raw),
        width=Measurement(value=width, unit="cm", raw_text=raw),
        height=Measurement(value=height, unit="cm", raw_text=raw),
    )


def _extract_quantity(data: dict[str, Any]) -> tuple[float | None, str | None]:
    """Extract package quantity and unit, defensibly parsing package_size."""
    qty = _resolve_value(data, "package_quantity")
    unit = _resolve_value(data, "package_unit")
    parsed_qty: float | None = None
    if qty is not None:
        if isinstance(qty, str):
            # Defensibly parse "2 Count (Pack of 1)", "12 Piece Set", etc.
            m = re.match(r"(\d+(?:\.\d+)?)", qty.strip())
            if m:
                try:
                    parsed_qty = float(m.group(1))
                except (TypeError, ValueError):
                    parsed_qty = None
        else:
            try:
                parsed_qty = float(qty)
            except (TypeError, ValueError):
                parsed_qty = None
        if parsed_qty is not None and parsed_qty <= 0:
            parsed_qty = None
    parsed_unit = str(unit).strip() if unit and str(unit).strip() else None
    return parsed_qty, parsed_unit


def build_product_fingerprint(
    asin: str,
    detail_data: dict[str, Any],
    *,
    marketplace: str = "US",
) -> ProductFingerprint:
    """Build a deterministic ProductFingerprint from a product_detail response.

    Parameters
    ----------
    asin:
        The Amazon ASIN (must be 10 uppercase alphanumeric characters).
    detail_data:
        The ``data`` dict from a successful Sorftime ``product_detail`` call.
    marketplace:
        Amazon marketplace code (default ``"US"``).

    Returns
    -------
    ProductFingerprint
        Normalised fingerprint with provenance, critical unknowns, and
        completeness score.
    """
    if not isinstance(detail_data, dict):
        raise ValueError("detail_data must be a dict")

    # --- Identity fields ---
    title = str(_resolve_value(detail_data, "title") or "")
    brand = _resolve_value(detail_data, "brand")
    brand = str(brand).strip() if brand else None
    category = _resolve_value(detail_data, "category")
    category = str(category).strip() if category else None
    product_form = _resolve_value(detail_data, "product_form")
    product_form = str(product_form).strip() if product_form else None
    model_number = _resolve_value(detail_data, "model_number")
    model_number = str(model_number).strip() if model_number else None

    # --- Attributes JSON (defensive parse) ---
    fp_attributes, attr_flat = _parse_attributes_json(detail_data.get("attributes"))

    # --- Materials, colours, components ---
    materials = _parse_list_field(_resolve_value(detail_data, "material"))
    colors = _parse_list_field(_resolve_value(detail_data, "color"))
    components = _parse_list_field(_resolve_value(detail_data, "components"))

    # Fill gaps from attributes when primary fields are absent
    if brand is None:
        ab = _value_from_attributes(attr_flat, _ATTR_ALIAS_BRAND)
        if ab:
            brand = ab
            fp_attributes.append(
                FingerprintAttribute(
                    name="brand_from_attrs",
                    value=ab,
                    normalized_value=ab.casefold(),
                    confidence=0.8,
                    evidence=[
                        SourceReference(
                            source=EvidenceSource.SORFTIME_PRODUCT_DETAIL,
                            field="attributes",
                        )
                    ],
                )
            )
    if not materials:
        am = _value_from_attributes(attr_flat, _ATTR_ALIAS_MATERIAL)
        if am:
            materials = _parse_list_field(am)
    if not colors:
        ac = _value_from_attributes(attr_flat, _ATTR_ALIAS_COLOR)
        if ac:
            colors = _parse_list_field(ac)
    if product_form is None:
        af = _value_from_attributes(attr_flat, _ATTR_ALIAS_PRODUCT_FORM)
        if af:
            product_form = str(af).strip()

    # --- Item dimensions & weight ---
    dimensions = _extract_item_dimensions(detail_data)
    weight_val = _resolve_value(detail_data, "item_weight")
    item_weight = _parse_measurement(weight_val, "g")

    # --- Package dimensions & weight ---
    package_dimensions = _extract_package_dimensions(detail_data)
    pkg_weight_val = _resolve_value(detail_data, "package_weight")
    package_weight = _parse_measurement(pkg_weight_val, "g")

    # --- Package quantity ---
    package_quantity, package_unit = _extract_quantity(detail_data)
    if package_quantity is None:
        acount = _value_from_attributes(attr_flat, _ATTR_ALIAS_UNIT_COUNT)
        if acount:
            try:
                parsed = float(re.match(r"(\d+(?:\.\d+)?)", str(acount).strip()).group(1))
                if parsed > 0:
                    package_quantity = parsed
            except (AttributeError, TypeError, ValueError):
                pass

    # --- Variation / bullets ---
    variation = _resolve_value(detail_data, "variation_text")
    bullets = _parse_list_field(_resolve_value(detail_data, "bullet_points"))
    description = _resolve_value(detail_data, "description")
    if description:
        description = str(description).strip()
    if not bullets and description:
        bullets = [description[:200]]

    # --- Images ---
    main_url = _resolve_value(detail_data, "main_image_url")
    extra_list = _resolve_value(detail_data, "extra_images")
    images = _parse_images(main_url, extra_list)

    # --- Search terms ---
    search_en = _parse_list_field(_resolve_value(detail_data, "search_terms"))

    # --- Source-field provenance ---
    source_fields: list[str] = []
    for canonical in FIELD_ALIASES:
        src = _resolve_source(detail_data, canonical)
        if src:
            source_fields.append(src)
    # Also track raw attributes when present
    if isinstance(detail_data.get("attributes"), str) and detail_data["attributes"].strip():
        source_fields.append("attributes")
    if isinstance(detail_data.get("package_size_cm"), str) and detail_data["package_size_cm"].strip():
        source_fields.append("package_size_cm")

    # --- Critical unknowns ---
    critical_unknowns: list[str] = []
    if not title:
        critical_unknowns.append("title")
    if product_form is None:
        critical_unknowns.append("product_form")
    if not materials:
        critical_unknowns.append("material")
    if package_quantity is None:
        critical_unknowns.append("package_quantity")
    if dimensions is None or not (
        dimensions.length or dimensions.width or dimensions.height
    ):
        critical_unknowns.append("dimensions")

    # --- Completeness ---
    resolved: dict[str, Any] = {
        "title": title,
        "brand": brand,
        "category": category,
        "product_form": product_form,
        "material": materials,
        "color": colors,
        "components": components,
        "model_number": model_number,
        "package_quantity": package_quantity,
        "package_unit": package_unit,
        "bullet_points": bullets,
        "description": description,
        "variation_text": str(variation).strip() if variation else None,
        "dimensions": dimensions,
        "item_weight": item_weight,
        "package_dimensions": package_dimensions,
        "package_weight": package_weight,
        "main_image": bool(images),
        "extra_images": len(images) > 1,
        "search_terms": search_en,
    }
    present = sum(1 for v in resolved.values() if v)
    completeness = round(present / len(COMPLETENESS_FIELDS) * 100, 1)

    # --- must_match / must_not_match rules ---
    must_match: list[str] = []
    must_not_match: list[str] = []
    if product_form:
        must_match.append(product_form)
    if package_quantity is not None:
        must_match.append(f"qty:{int(package_quantity)}")
    if package_unit:
        must_match.append(f"unit:{package_unit}")

    return ProductFingerprint(
        asin=asin,
        marketplace=marketplace,
        title=title,
        brand=brand,
        category=category,
        product_form=product_form,
        materials=materials,
        colors=colors,
        components=components,
        dimensions=dimensions,
        item_weight=item_weight,
        package_dimensions=package_dimensions,
        package_weight=package_weight,
        package_quantity=package_quantity,
        package_unit=package_unit,
        model_number=model_number,
        attributes=fp_attributes,
        search_terms_en=search_en,
        must_match=must_match,
        must_not_match=must_not_match,
        images=images,
        source_fields=source_fields,
        critical_unknowns=critical_unknowns,
        completeness_pct=completeness,
    )
