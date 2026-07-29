"""Synthetic tests for the 1688 backend vertical slice.

Uses only synthetic fixtures -- no live API calls, no real ASINs, no credentials.
Covers: fingerprint extraction (aliases, package_size, attributes JSON,
package_size_cm, weight_g), real-row candidate normalisation, deterministic
comparison (corrected verdict logic), price ambiguity, endpoint naming,
ASIN validation, and contract enforcement.
"""

from __future__ import annotations

import hashlib
import json
import logging

import pytest
from pydantic import ValidationError


def test_authenticated_http_urls_are_not_logged_at_info_level():
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING

from pickflow_mcp_server.supplier_contracts import (
    EvidenceDimension,
    EvidenceSource,
    EvidenceStatus,
    ImageRole,
    LookupStatus,
    MatchVerdict,
    PriceComparability,
    PriceTier,
    ProductFingerprint,
    ProductImage,
    SupplierCandidate,
    SupplierComparison,
    SupplierLookupResult,
    VisualReviewBundle,
    VisualReviewPair,
)
from pickflow_mcp_server.fingerprint import (
    _parse_attributes_json,
    _parse_list_field,
    _parse_measurement,
    _parse_package_size_cm,
    _resolve_source,
    _resolve_value,
    _value_from_attributes,
    build_product_fingerprint,
)
from pickflow_mcp_server.supplier_service import (
    _digest_id,
    _normalize_one,
    _normalize_price_tiers,
    _parse_price_str,
    _parse_purchase_quantity,
    build_lookup_result,
    compare_fingerprint_to_candidates,
    normalize_candidates,
    MAX_REVIEW_CANDIDATES,
)


# ---------------------------------------------------------------------------
# Synthetic fixtures
# ---------------------------------------------------------------------------

def _s_asin() -> str:
    return "B0" + "SYNTHET1"


def _sample_detail() -> dict:
    """Minimal valid product_detail-like dict with varied alias coverage."""
    return {
        "asin": _s_asin(),
        "title": "Silicone Ice Cube Tray with Lid, 2-Pack, BPA-Free",
        "brand": "FrostMate",
        "product_type": "IceCubeTray",
        "material": "Silicone",
        "color": "Blue",
        "package_quantity": "2",
        "package_unit": "pieces",
        "model_number": "FMT-200-BL",
        "item_length": "22.5",
        "item_width": "14.0",
        "item_height": "5.5",
        "item_weight": "280",
        "bullet_points": [
            "BPA-free food-grade silicone",
            "Easy-release flexible bottom",
        ],
        "main_image": "https://images.example.test/ice-tray-main.jpg",
        "extra_images": [
            "https://images.example.test/ice-tray-2.jpg",
        ],
        "search_terms": "ice cube tray, silicone ice tray",
    }


def _sample_detail_alt_aliases() -> dict:
    """Detail using alternate field names including top_category/subcategory."""
    return {
        "asin": _s_asin(),
        "product_title": "Bamboo Cutting Board Set, 3-Piece",
        "manufacturer": "EcoHome",
        "item_type_name": "CuttingBoard",
        "main_material": "Bamboo",
        "colour": "Natural",
        "count": "3",
        "unit_type": "pieces",
        "part_number": "EH-BCB-3",
        "product_length": "45.0",
        "product_width": "30.0",
        "product_height": "1.8",
        "features": ["Organic bamboo, knife-friendly surface"],
        "image_url": "https://images.example.test/board-main.jpg",
        "gallery_images": ["https://images.example.test/board-2.jpg"],
        "top_category": "Kitchen & Dining",
        "subcategory": "Cutting Boards",
    }


def _sample_detail_with_package_size() -> dict:
    """Detail where quantity comes from package_size string."""
    return {
        "asin": _s_asin(),
        "title": "LED Tea Lights, Flameless Candles",
        "package_size": "24 Count (Pack of 1)",
        "package_unit": "packs",
        "color": "Warm White",
        "product_type": "FlamelessCandle",
    }


def _sample_detail_with_attributes_json() -> dict:
    """Detail where metadata comes from the attributes JSON string."""
    return {
        "asin": _s_asin(),
        "title": "Decorative Throw Pillow Covers Set of 2",
        "attributes": json.dumps({
            "Brand": "HomeNest",
            "Material": "Velvet",
            "Color": "Emerald Green",
            "Occasion": "Christmas",
            "Unit Count": "2",
            "Item Type": "ThrowPillowCover",
        }),
    }


def _sample_detail_with_package_size_cm() -> dict:
    """Detail with package_size_cm in L*W*H format and weight_g."""
    return {
        "asin": _s_asin(),
        "title": "LED Night Light, USB Rechargeable",
        "package_size_cm": "8.5*8.5*12.0",
        "weight_g": "150",
        "product_type": "NightLight",
        "material": "ABS Plastic",
    }


# --- Purchase quantity string variants ---

_PURCHASE_QTY_VARIANTS = [
    ("100", 100),
    ("100pcs", 100),
    (">=100", 100),
    ("100-500", 100),
    ("100~500", 100),
    ("100 pieces", 100),
    ("200", 200),
    ("50sets", 50),
    ("", None),
    (None, None),
    ("no digits here", None),
    (">=1000", 1000),
]


# --- Real-shape 1688 keyword search rows ---

def _sample_1688_keyword_real_rows() -> dict:
    """Rows matching verified keyword-search field names."""
    return {
        "data": [
            {
                "title": "Silicone Ice Cube Tray with Lid BPA-Free 2pcs/set",
                "photo": "https://img.1688.test/ice-tray-1.jpg",
                "url": "https://detail.1688.com/offer/001.html",
                "product_id": "off-001",
                "store_name": "Mingda Silicone Factory",
                "service_score": 4.8,
                "service_score_detail": {"quality": 4.9, "speed": 4.7},
                "online_date": "2019-03-15",
                "sales_of_30d": 3200,
                "wholesale_price_range": [
                    {"price": 4.5, "purchase_quantity": 100},
                    {"price": 3.8, "purchase_quantity": 500},
                ],
                "repurchase_rate": 0.42,
                "shipping_origin": "Yiwu, Zhejiang",
                "review_count": 156,
                "score": 4.7,
                "sku_count": 12,
            },
            {
                "title": "Silicone Ice Mold BPA Free Flexible 2 Pack",
                "photo": "https://img.1688.test/ice-tray-4.jpg",
                "url": "https://detail.1688.com/offer/003.html",
                "product_id": "off-003",
                "store_name": "Chengxin Rubber Products",
                "wholesale_price_range": [
                    {"price": 3.2, "purchase_quantity": 200},
                ],
                "sales_of_30d": 1800,
                "repurchase_rate": 0.35,
                "review_count": 89,
                "sku_count": 6,
            },
            {
                # Row with no store_name (sometimes absent)
                "title": "BPA Free Silicone Ice Tray Large Cube",
                "photo": "https://img.1688.test/ice-tray-5.jpg",
                "url": "https://detail.1688.com/offer/005.html",
                "product_id": "off-005",
                "sales_of_30d": 900,
                "wholesale_price_range": [
                    {"price": 5.2, "purchase_quantity": 50},
                ],
            },
        ]
    }


def _sample_1688_image_real_rows() -> dict:
    """Rows matching verified image-search field names."""
    return {
        "data": [
            {
                "title": "Silicone Ice Cube Tray with Lid BPA-Free 2pcs/set",
                "photo": "https://img.1688.test/ice-tray-1.jpg",
                "url": "https://detail.1688.com/offer/001.html",
                "product_id": "off-001",
                "store_name": "Mingda Silicone Factory",
                "seller_identities": ["Verified Supplier", "Gold Supplier"],
                "offer_identities": ["Factory Direct"],
                "min_order_quantity": 100,
                "is_drop_shipping": True,
                "wholesale_price_range": [
                    {"price": 4.5, "purchase_quantity": 100},
                    {"price": 3.8, "purchase_quantity": 500},
                ],
                "sales_of_30d": 3200,
                "repurchase_rate": 0.42,
                "service_score": 4.8,
            },
            {
                "title": "Large Kitchen Cutting Board Bamboo Wooden Natural",
                "photo": "https://img.1688.test/board-1.jpg",
                "url": "https://detail.1688.com/offer/002.html",
                "product_id": "off-002",
                "store_name": "BambooWorks Ltd",
                "min_order_quantity": 50,
                "wholesale_price_range": [
                    {"price": 15.0, "purchase_quantity": 50},
                ],
                "seller_identities": ["Verified Supplier"],
                "is_drop_shipping": False,
            },
        ]
    }


def _sample_1688_empty_result() -> dict:
    return {"data": []}


def _sample_1688_error_result() -> dict:
    return {"_error": {"code": "RATE_LIMITED", "message": "Too many requests"}}


# ---------------------------------------------------------------------------
# 1. Fingerprint extraction tests
# ---------------------------------------------------------------------------

class TestFingerprintExtraction:
    """Alias resolution, missing fields, completeness scoring."""

    def test_extracts_all_identity_fields(self):
        fp = build_product_fingerprint(_s_asin(), _sample_detail())
        assert fp.asin == _s_asin()
        assert fp.title.startswith("Silicone Ice Cube Tray")
        assert fp.brand == "FrostMate"
        assert fp.product_form == "IceCubeTray"

    def test_extracts_material_color_package(self):
        fp = build_product_fingerprint(_s_asin(), _sample_detail())
        assert fp.materials == ["Silicone"]
        assert fp.colors == ["Blue"]
        assert fp.package_quantity == 2
        assert fp.package_unit == "pieces"

    def test_extracts_dimensions(self):
        fp = build_product_fingerprint(_s_asin(), _sample_detail())
        assert fp.dimensions is not None
        assert fp.dimensions.length is not None
        assert fp.dimensions.length.value == 22.5

    def test_extracts_images_and_search_terms(self):
        fp = build_product_fingerprint(_s_asin(), _sample_detail())
        assert len(fp.images) >= 2
        assert len(fp.search_terms_en) >= 1

    def test_alias_resolution_with_alt_names(self):
        fp = build_product_fingerprint(_s_asin(), _sample_detail_alt_aliases())
        assert fp.title == "Bamboo Cutting Board Set, 3-Piece"
        assert fp.brand == "EcoHome"
        assert fp.product_form == "CuttingBoard"
        assert fp.materials == ["Bamboo"]
        assert fp.colors == ["Natural"]
        assert fp.package_quantity == 3
        assert fp.model_number == "EH-BCB-3"
        assert fp.dimensions.length.value == 45.0

    def test_top_category_and_subcategory_aliases(self):
        """top_category/subcategory map through the category alias list."""
        fp = build_product_fingerprint(_s_asin(), _sample_detail_alt_aliases())
        # The first alias that matched (top_category) becomes the category value
        assert fp.category == "Kitchen & Dining"

    def test_package_size_parsed_defensively(self):
        fp = build_product_fingerprint(_s_asin(), _sample_detail_with_package_size())
        assert fp.package_quantity == 24  # parsed from "24 Count (Pack of 1)"

    def test_minimal_detail_produces_critical_unknowns(self):
        fp = build_product_fingerprint(_s_asin(), {"title": "Some Unknown Product"})
        assert "material" in fp.critical_unknowns
        assert "product_form" in fp.critical_unknowns
        assert "package_quantity" in fp.critical_unknowns
        assert fp.completeness_pct < 50

    def test_full_detail_has_high_completeness(self):
        fp = build_product_fingerprint(_s_asin(), _sample_detail())
        assert fp.completeness_pct > 50
        assert len(fp.critical_unknowns) == 0  # all present

    def test_source_fields_track_provenance(self):
        fp = build_product_fingerprint(_s_asin(), _sample_detail())
        assert "title" in fp.source_fields

    def test_must_match_includes_product_form_and_quantity(self):
        fp = build_product_fingerprint(_s_asin(), _sample_detail())
        assert any("IceCubeTray" in m for m in fp.must_match)
        assert any("qty:2" in m for m in fp.must_match)

    def test_accepts_only_dict_detail_data(self):
        with pytest.raises(ValueError):
            build_product_fingerprint(_s_asin(), None)
        with pytest.raises(ValueError):
            build_product_fingerprint(_s_asin(), [])
        with pytest.raises(ValueError):
            build_product_fingerprint(_s_asin(), "not a dict")

    def test_empty_dict_returns_minimal_fingerprint(self):
        fp = build_product_fingerprint(_s_asin(), {})
        assert fp.asin == _s_asin()
        assert fp.title == ""
        assert len(fp.critical_unknowns) >= 3


class TestFingerprintHelpers:
    """Unit tests for internal fingerprint helpers."""

    def test_resolve_value_skips_empty(self):
        data = {"title": "", "product_title": "The Real Title"}
        assert _resolve_value(data, "title") == "The Real Title"

    def test_resolve_source_returns_alias_name(self):
        data = {"product_title": "A Title"}
        assert _resolve_source(data, "title") == "product_title"

    def test_parse_list_field_variants(self):
        assert _parse_list_field("red;blue;green") == ["red", "blue", "green"]
        assert _parse_list_field("red, blue, green") == ["red", "blue", "green"]
        assert _parse_list_field(["a", "b"]) == ["a", "b"]
        assert _parse_list_field(None) == []

    def test_parse_measurement(self):
        m = _parse_measurement(42.5, "cm")
        assert m is not None and m.value == 42.5 and m.unit == "cm"
        assert _parse_measurement(None) is None
        assert _parse_measurement("not a number") is None

    def test_parse_measurement_zero(self):
        m = _parse_measurement(0)
        assert m is not None
        assert m.value == 0


# ---------------------------------------------------------------------------
# 2. Endpoint naming tests (no live calls)
# ---------------------------------------------------------------------------

class TestEndpointNames:
    """Verified endpoint arguments names -- regression guards."""

    def test_ali1688_similar_product_signature(self):
        from pickflow_mcp_server.api import ali1688_similar_product
        import inspect
        sig = inspect.signature(ali1688_similar_product)
        params = list(sig.parameters.keys())
        assert "search_name" in params
        assert "page" in params

    def test_ali1688_product_search_from_image_signature(self):
        from pickflow_mcp_server.api import ali1688_product_search_from_image
        import inspect
        sig = inspect.signature(ali1688_product_search_from_image)
        params = list(sig.parameters.keys())
        assert "image_url" in params
        assert "page" in params


# ---------------------------------------------------------------------------
# 3. Candidate normalisation tests (real row shapes)
# ---------------------------------------------------------------------------

class TestCandidateNormalization:
    """Normalising raw 1688 rows into SupplierCandidate -- real field names."""

    def test_normalizes_real_keyword_row(self):
        rows = _sample_1688_keyword_real_rows()["data"]
        cand = _normalize_one(rows[0], "keyword_search")
        assert cand is not None
        assert "silicone" in cand.title.casefold()
        assert cand.offer_id == "off-001"
        assert cand.detail_url == "https://detail.1688.com/offer/001.html"
        assert cand.supplier_name == "Mingda Silicone Factory"
        assert len(cand.image_urls) == 1
        assert "photo" in cand.image_urls[0] or "1688" in cand.image_urls[0]
        assert len(cand.price_tiers) == 2
        assert cand.price_tiers[0].unit_price_cny == 4.5
        assert cand.price_tiers[0].min_quantity == 100
        assert cand.price_tiers[1].unit_price_cny == 3.8
        assert cand.price_comparability == PriceComparability.AMBIGUOUS  # real row
        assert cand.normalized_unit_price_cny is None
        assert "keyword_search" in cand.search_modes

    def test_normalizes_real_image_row(self):
        rows = _sample_1688_image_real_rows()["data"]
        cand = _normalize_one(rows[0], "image_search")
        assert cand is not None
        assert cand.offer_id == "off-001"
        assert cand.moq == 100
        assert cand.supplier_name == "Mingda Silicone Factory"
        assert cand.supplier_signals.get("seller_identities") == ["Verified Supplier", "Gold Supplier"]
        assert cand.supplier_signals.get("is_drop_shipping") is True
        assert "image_search" in cand.search_modes

    def test_real_row_price_is_always_ambiguous(self):
        """Real 16-field rows must stay AMBIGUOUS -- no EXACT ever."""
        for rows in [
            _sample_1688_keyword_real_rows()["data"],
            _sample_1688_image_real_rows()["data"],
        ]:
            for row in rows:
                cand = _normalize_one(row, "test")
                if cand is None:
                    continue
                # Even if pack_quantity were present, real rows are AMBIGUOUS
                assert cand.price_comparability in (
                    PriceComparability.AMBIGUOUS,
                    PriceComparability.UNAVAILABLE,
                )
                # EXACT price is never emitted for real rows
                assert cand.price_comparability != PriceComparability.EXACT

    def test_per_pack_pricing_synthetic_flag(self):
        """Synthetic _per_pack_pricing flag triggers NORMALIZED."""
        row = {
            "title": "Silicone Ice Cube Tray 2-Pack",
            "url": "https://detail.1688.com/offer/synth.html",
            "product_id": "synth-01",
            "store_name": "Synthetic Factory",
            "photo": "https://img.test/synth.jpg",
            "wholesale_price_range": [
                {"price": 8.0, "purchase_quantity": 100},
            ],
            "min_order_quantity": 100,
            "pack_quantity": 2,
            "pack_unit": "pieces",
            "_per_pack_pricing": True,
        }
        cand = _normalize_one(row, "test")
        assert cand is not None
        assert cand.price_comparability == PriceComparability.NORMALIZED
        assert cand.normalized_unit_price_cny == 4.0  # 8.0 / 2

    def test_missing_store_name_still_normalizes(self):
        """store_name is sometimes absent in real keyword rows."""
        row = {
            "title": "Test Product",
            "url": "https://detail.1688.com/offer/nostore.html",
            "product_id": "ns-01",
            "photo": "https://img.test/ns.jpg",
            "wholesale_price_range": [{"price": 10.0, "purchase_quantity": 100}],
        }
        cand = _normalize_one(row, "keyword_search")
        assert cand is not None
        assert cand.supplier_name is None
        assert "supplier_name" in cand.missing_fields

    def test_empty_title_returns_none(self):
        row = {"product_id": "off-004", "url": "https://test/4.html"}
        assert _normalize_one(row, "image_search") is None

    def test_candidate_id_is_deterministic_digest(self):
        row = {
            "title": "Consistent Product",
            "url": "https://detail.1688.com/offer/deterministic.html",
            "product_id": "det-01",
            "store_name": "ConsistentCo",
            "photo": "https://img.test/det.jpg",
        }
        cand1 = _normalize_one(row, "test")
        cand2 = _normalize_one(row, "test")
        # Same inputs -> same id (no random hash)
        assert cand1.candidate_id == cand2.candidate_id
        # ID should look like a hex digest
        expected = hashlib.md5(
            "https://detail.1688.com/offer/deterministic.html::ConsistentCo::Consistent Product".encode()
        ).hexdigest()[:12]
        assert cand1.candidate_id == expected
        assert len(cand1.candidate_id) == 12

    def test_supplier_signals_populated(self):
        rows = _sample_1688_keyword_real_rows()["data"]
        cand = _normalize_one(rows[0], "keyword_search")
        assert cand.supplier_signals.get("service_score") == 4.8
        assert cand.supplier_signals.get("sales_of_30d") == 3200
        assert cand.supplier_signals.get("repurchase_rate") == 0.42
        assert cand.supplier_signals.get("review_count") == 156
        assert cand.supplier_signals.get("sku_count") == 12

    def test_price_tiers_from_wholesale_price_range(self):
        tiers = _normalize_price_tiers([
            {"price": 4.5, "purchase_quantity": 100},
            {"price": 3.8, "purchase_quantity": 500},
        ])
        assert len(tiers) == 2
        assert tiers[0].unit_price_cny == 4.5
        assert tiers[0].min_quantity == 100
        assert tiers[1].unit_price_cny == 3.8

    def test_price_tiers_reject_invalid(self):
        tiers = _normalize_price_tiers([
            {"purchase_quantity": 100},  # no price
            {"price": 5.0},             # no quantity
            {"price": 3.0, "purchase_quantity": 200},  # valid
        ])
        assert len(tiers) == 1
        assert tiers[0].unit_price_cny == 3.0


# ---------------------------------------------------------------------------
# 4. Deduplication tests
# ---------------------------------------------------------------------------

class TestDeduplication:
    """Merging image-search and keyword-search candidates."""

    def test_merges_overlapping_candidates(self):
        candidates = normalize_candidates(
            _sample_1688_image_real_rows(),
            _sample_1688_keyword_real_rows(),
        )
        # off-001 appears in both -> merged; off-003 keyword only; off-002 image only; off-005 keyword only
        # Total unique: 4
        assert len(candidates) == 4

    def test_merged_candidate_has_both_search_modes(self):
        candidates = normalize_candidates(
            _sample_1688_image_real_rows(),
            _sample_1688_keyword_real_rows(),
        )
        merged = [c for c in candidates if c.offer_id == "off-001"]
        assert len(merged) == 1
        assert set(merged[0].search_modes) == {"image_search", "keyword_search"}

    def test_dedup_preserves_best_data(self):
        candidates = normalize_candidates(
            _sample_1688_image_real_rows(),
            _sample_1688_keyword_real_rows(),
        )
        merged = [c for c in candidates if c.offer_id == "off-001"]
        assert len(merged) == 1
        # Image result had 2 price tiers; keyword only 1 -> merged keeps 2
        assert len(merged[0].price_tiers) == 2

    def test_empty_results_return_empty(self):
        assert normalize_candidates(None, None) == []
        assert normalize_candidates({}, {}) == []
        assert normalize_candidates({"data": []}, {"data": []}) == []

    def test_error_results_ignored(self):
        candidates = normalize_candidates(
            _sample_1688_error_result(),
            _sample_1688_image_real_rows(),
        )
        assert len(candidates) == 2  # only image rows survive

    def test_partial_search_still_normalizes(self):
        candidates = normalize_candidates(
            _sample_1688_image_real_rows(),
            None,
        )
        assert len(candidates) == 2


# ---------------------------------------------------------------------------
# 5. Deterministic comparison tests (corrected verdict logic)
# ---------------------------------------------------------------------------

class TestDeterministicComparison:
    """Hard gates only on structured quantity mismatch. Title-based signals are soft."""

    def _make_fp(self, **overrides) -> ProductFingerprint:
        defaults = {
            "asin": _s_asin(),
            "title": "Silicone Ice Cube Tray with Lid, 2-Pack, BPA-Free",
            "product_form": "IceCubeTray",
            "materials": ["Silicone"],
            "colors": ["Blue"],
            "package_quantity": 2,
            "package_unit": "pieces",
            "images": [
                ProductImage(
                    url="https://images.test/ref.jpg",
                    role=ImageRole.MAIN,
                    source=EvidenceSource.SORFTIME_PRODUCT_DETAIL,
                )
            ],
            "completeness_pct": 80,
        }
        defaults.update(overrides)
        return ProductFingerprint(**defaults)

    def _make_cand(self, cand_id: str, **overrides) -> SupplierCandidate:
        defaults = {
            "candidate_id": cand_id,
            "title": "Silicone Ice Cube Tray with Lid, BPA-Free, 2pcs/set",
            "detail_url": f"https://detail.1688.com/{cand_id}.html",
            "supplier_name": "Test Factory",
            "image_urls": ["https://img.test/cand.jpg"],
            "search_modes": ["image_search"],
            "price_tiers": [
                PriceTier(min_quantity=100, unit_price_cny=4.5, unit="piece")
            ],
            "moq": 100,
            "pack_quantity": 2,
            "pack_unit": "pieces",
            "price_comparability": PriceComparability.AMBIGUOUS,
        }
        defaults.update(overrides)
        return SupplierCandidate(**defaults)

    def test_matching_candidate_gets_possible_same(self):
        fp = self._make_fp()
        cand = self._make_cand("cand-match")
        comps, bundle = compare_fingerprint_to_candidates(fp, [cand])
        assert comps[0].verdict in (MatchVerdict.POSSIBLE_SAME, MatchVerdict.INSUFFICIENT)
        assert len(comps[0].hard_failures) == 0

    def test_form_not_in_title_is_unknown_not_hard_failure(self):
        """Product form absent from candidate title -> UNKNOWN, not DIFFERENT."""
        fp = self._make_fp(product_form="IceCubeTray")
        cand = self._make_cand(
            "cand-form",
            title="Stainless Steel Water Bottle 500ml Insulated",
        )
        comps, bundle = compare_fingerprint_to_candidates(fp, [cand])
        # Should NOT be DIFFERENT from title-based form check
        assert comps[0].verdict != MatchVerdict.DIFFERENT or all(
            "product_form" not in f for f in comps[0].hard_failures
        )
        # Form check should produce UNKNOWN evidence
        form_ev = [e for e in comps[0].evidence if e.dimension == EvidenceDimension.PRODUCT_FORM]
        assert len(form_ev) >= 1
        assert form_ev[0].status == EvidenceStatus.UNKNOWN

    def test_material_not_in_title_is_unknown_not_hard_failure(self):
        """Material absent from candidate title -> UNKNOWN, never hard failure."""
        fp = self._make_fp(materials=["Silicone"])
        cand = self._make_cand(
            "cand-mat",
            title="Wooden Ice Cube Tray Natural Bamboo",
        )
        comps, bundle = compare_fingerprint_to_candidates(fp, [cand])
        assert "material" not in comps[0].hard_failures
        # Evidence should be UNKNOWN, not MISMATCH
        mat_ev = [e for e in comps[0].evidence if e.dimension == EvidenceDimension.MATERIAL]
        assert all(e.status != EvidenceStatus.MISMATCH for e in mat_ev)

    def test_material_in_title_is_soft_match(self):
        """Material found in title -> soft MATCH with lowered confidence."""
        fp = self._make_fp(materials=["Silicone"])
        cand = self._make_cand(
            "cand-mat-match",
            title="Food Grade Silicone Ice Cube Tray 2-Pack",
        )
        comps, bundle = compare_fingerprint_to_candidates(fp, [cand])
        mat_ev = [e for e in comps[0].evidence if e.dimension == EvidenceDimension.MATERIAL]
        assert any(e.status == EvidenceStatus.MATCH for e in mat_ev)
        # Soft match means confidence < 1.0
        for e in mat_ev:
            if e.status == EvidenceStatus.MATCH:
                assert e.confidence < 1.0

    def test_structured_quantity_mismatch_is_hard_veto(self):
        """When BOTH sides have structured quantities + compatible units AND differ."""
        fp = self._make_fp(package_quantity=2, package_unit="pieces")
        cand = self._make_cand(
            "cand-qty",
            pack_quantity=12,
            pack_unit="pieces",
        )
        comps, bundle = compare_fingerprint_to_candidates(fp, [cand])
        assert comps[0].verdict == MatchVerdict.DIFFERENT
        assert "quantity" in comps[0].hard_failures

    def test_incompatible_units_are_unknown_not_hard(self):
        """pieces vs sets -> incompatible units -> UNKNOWN, not hard failure."""
        fp = self._make_fp(package_quantity=2, package_unit="pieces")
        cand = self._make_cand(
            "cand-units",
            pack_quantity=2,
            pack_unit="sets",
        )
        comps, bundle = compare_fingerprint_to_candidates(fp, [cand])
        assert "quantity" not in comps[0].hard_failures

    def test_unknown_fingerprint_produces_unknowns(self):
        fp = self._make_fp(materials=[], product_form=None, package_quantity=None)
        cand = self._make_cand("cand-unk", pack_quantity=None, pack_unit=None)
        comps, bundle = compare_fingerprint_to_candidates(fp, [cand])
        unknown = [e for e in comps[0].evidence if e.status == EvidenceStatus.UNKNOWN]
        assert len(unknown) > 0
        assert comps[0].hard_failures == []

    def test_visual_review_bundle_max_five(self):
        fp = self._make_fp()
        many = [self._make_cand(f"cand-{i}", title=f"Ice Cube Tray Variant {i}") for i in range(10)]
        comps, bundle = compare_fingerprint_to_candidates(fp, many)
        assert len(bundle.pairs) <= MAX_REVIEW_CANDIDATES

    def test_bundle_excludes_different_verdicts(self):
        fp = self._make_fp(package_quantity=2, package_unit="pieces")
        cands = [
            self._make_cand("good", pack_quantity=2, pack_unit="pieces", title="Silicone Ice Tray"),
            self._make_cand("bad", pack_quantity=12, pack_unit="pieces", title="Silicone Ice Tray 12"),  # hard qty fail
        ]
        comps, bundle = compare_fingerprint_to_candidates(fp, cands)
        pair_ids = {p.candidate_id for p in bundle.pairs}
        assert "good" in pair_ids
        assert "bad" not in pair_ids

    def test_ambigous_price_gets_warning(self):
        fp = self._make_fp()
        cand = self._make_cand("cand-amb", pack_quantity=None, pack_unit=None)
        comps, bundle = compare_fingerprint_to_candidates(fp, [cand])
        assert len(comps[0].warnings) > 0
        assert any("ambiguous" in w.casefold() for w in comps[0].warnings)

    def test_hard_failure_contract_validation(self):
        with pytest.raises((ValidationError, ValueError)):
            SupplierComparison(
                comparison_id="bad-1",
                asin=_s_asin(),
                candidate_id="c-1",
                verdict=MatchVerdict.POSSIBLE_SAME,
                overall_score=50,
                hard_failures=["quantity"],
            )


# ---------------------------------------------------------------------------
# 6. LookupResult & serialization tests
# ---------------------------------------------------------------------------

class TestLookupResult:
    def test_success_result(self):
        fp = build_product_fingerprint(_s_asin(), _sample_detail())
        result = build_lookup_result(status=LookupStatus.SUCCESS, fingerprint=fp, api_calls=3)
        assert result.status == LookupStatus.SUCCESS
        assert result.fingerprint is not None

    def test_error_result(self):
        result = build_lookup_result(status=LookupStatus.ERROR, error_code="X", warnings=["fail"])
        assert result.status == LookupStatus.ERROR
        assert result.error_code == "X"

    def test_json_roundtrip(self):
        fp = build_product_fingerprint(_s_asin(), _sample_detail())
        result = build_lookup_result(status=LookupStatus.SUCCESS, fingerprint=fp, api_calls=1)
        data = result.model_dump(mode="json")
        assert data["contract_version"] == "1.0"
        assert data["api_calls"] == 1
        json_str = json.dumps(data)
        re_read = json.loads(json_str)
        assert re_read["status"] == "success"


class TestContractSerialization:
    def test_fingerprint_roundtrip(self):
        fp = build_product_fingerprint(_s_asin(), _sample_detail())
        recreated = ProductFingerprint(**fp.model_dump(mode="json"))
        assert recreated.asin == fp.asin

    def test_comparison_roundtrip(self):
        comp = SupplierComparison(
            comparison_id="cmp-1", asin=_s_asin(), candidate_id="x",
            verdict=MatchVerdict.INSUFFICIENT, overall_score=55,
        )
        recreated = SupplierComparison(**comp.model_dump(mode="json"))
        assert recreated.verdict == MatchVerdict.INSUFFICIENT

    def test_bundle_roundtrip(self):
        bundle = VisualReviewBundle(
            asin=_s_asin(),
            pairs=[VisualReviewPair(
                candidate_id="c-1",
                reference_images=["https://t/ref.jpg"],
                candidate_images=["https://t/cand.jpg"],
            )],
        )
        recreated = VisualReviewBundle(**bundle.model_dump(mode="json"))
        assert recreated.instructions_version == "codex-vision-v1"


# ---------------------------------------------------------------------------
# 7. Contract boundary enforcement
# ---------------------------------------------------------------------------

class TestContractBoundary:
    def test_fp_rejects_extra_fields(self):
        with pytest.raises(ValidationError):
            ProductFingerprint(asin=_s_asin(), title="T", extra="no")

    def test_visual_bundle_rejects_extra_fields(self):
        with pytest.raises(ValidationError):
            VisualReviewBundle(asin=_s_asin(), bonus="nope")

    def test_ambiguous_price_cannot_publish_normalized(self):
        with pytest.raises((ValidationError, ValueError)):
            SupplierCandidate(
                candidate_id="c-1", title="T",
                normalized_unit_price_cny=5.0,
                price_comparability=PriceComparability.AMBIGUOUS,
            )


# ---------------------------------------------------------------------------
# 8. ASIN validation tests (contract regex)
# ---------------------------------------------------------------------------

class TestAsinValidation:
    """ASIN must exactly match ^[A-Z0-9]{10}$ as the contract specifies."""

    def test_valid_asin_accepted(self):
        fp = ProductFingerprint(asin=_s_asin(), title="Test")
        assert fp.asin == _s_asin()

    def test_lowercase_rejected_by_contract(self):
        with pytest.raises(ValidationError):
            ProductFingerprint(asin="b0syntht1", title="Test")

    def test_short_asin_rejected(self):
        with pytest.raises(ValidationError):
            ProductFingerprint(asin="B0SHORT", title="Test")

    def test_long_asin_rejected(self):
        with pytest.raises(ValidationError):
            ProductFingerprint(asin=_s_asin() + "23", title="Test")

    def test_special_chars_rejected(self):
        with pytest.raises(ValidationError):
            ProductFingerprint(asin="B0SYNTH-T1", title="Test")


# ---------------------------------------------------------------------------
# 9. Real-row regression tests
# ---------------------------------------------------------------------------

class TestRealRowRegression:
    """End-to-end flows using verified row shapes."""

    def test_keyword_rows_all_normalize(self):
        rows = _sample_1688_keyword_real_rows()["data"]
        for row in rows:
            cand = _normalize_one(row, "keyword_search")
            if row.get("title"):
                assert cand is not None
                assert len(cand.candidate_id) == 12
                # All real rows -> AMBIGUOUS or UNAVAILABLE price
                assert cand.price_comparability != PriceComparability.EXACT

    def test_image_rows_all_normalize(self):
        rows = _sample_1688_image_real_rows()["data"]
        for row in rows:
            cand = _normalize_one(row, "image_search")
            assert cand is not None
            assert cand.price_comparability != PriceComparability.EXACT
            # Image rows carry min_order_quantity
            if "min_order_quantity" in row:
                assert cand.moq is not None
                assert cand.moq > 0

    def test_store_name_absent_handled(self):
        """Keyword row at index 2 has no store_name."""
        rows = _sample_1688_keyword_real_rows()["data"]
        no_store_row = rows[2]
        assert "store_name" not in no_store_row
        cand = _normalize_one(no_store_row, "keyword_search")
        assert cand is not None
        assert cand.supplier_name is None
        assert "supplier_name" in cand.missing_fields

    def test_wholesale_price_range_all_entries_parsed(self):
        """Every entry with both price and purchase_quantity becomes a PriceTier."""
        rows = _sample_1688_keyword_real_rows()["data"]
        cand = _normalize_one(rows[0], "keyword_search")
        assert cand is not None
        assert len(cand.price_tiers) == 2
        assert cand.price_tiers[0].min_quantity == 100
        assert cand.price_tiers[0].unit_price_cny == 4.5
        assert cand.price_tiers[1].min_quantity == 500
        assert cand.price_tiers[1].unit_price_cny == 3.8

    def test_digest_id_is_stable(self):
        row = _sample_1688_keyword_real_rows()["data"][0]
        cand1 = _normalize_one(row, "k")
        cand2 = _normalize_one(row, "k")
        assert cand1.candidate_id == cand2.candidate_id

    def test_digest_id_helper(self):
        a = _digest_id("url-1", "Supplier A", "Title A")
        b = _digest_id("url-1", "Supplier A", "Title A")
        c = _digest_id("url-2", "Supplier A", "Title A")
        assert a == b
        assert a != c
        assert len(a) == 12


# ---------------------------------------------------------------------------
# 10. Attributes JSON parsing tests
# ---------------------------------------------------------------------------

class TestAttributesJsonParsing:
    """Parse attributes (JSON string) into FingerprintAttribute list."""

    def test_parses_valid_json_attributes(self):
        raw = json.dumps({"Brand": "AcmeCorp", "Material": "Steel", "Occasion": "Camping"})
        attrs, flat = _parse_attributes_json(raw)
        assert len(attrs) == 3
        assert flat["Brand"] == "AcmeCorp"
        assert flat["Material"] == "Steel"
        for a in attrs:
            assert len(a.evidence) >= 1
            assert a.evidence[0].source == EvidenceSource.SORFTIME_PRODUCT_DETAIL

    def test_parses_empty_string(self):
        attrs, flat = _parse_attributes_json("")
        assert attrs == []
        assert flat == {}

    def test_parses_none(self):
        attrs, flat = _parse_attributes_json(None)
        assert attrs == []
        assert flat == {}

    def test_parses_invalid_json(self):
        attrs, flat = _parse_attributes_json("{not valid json}")
        assert attrs == []
        assert flat == {}

    def test_parses_non_dict_json(self):
        attrs, flat = _parse_attributes_json(json.dumps([1, 2, 3]))
        assert attrs == []
        assert flat == {}

    def test_skip_empty_values(self):
        raw = json.dumps({"Brand": "Acme", "Material": "", "Color": None})
        attrs, flat = _parse_attributes_json(raw)
        assert len(attrs) == 1
        assert "Brand" in flat
        assert "Material" not in flat

    def test_attributes_fill_brand_when_absent(self):
        fp = build_product_fingerprint(_s_asin(), _sample_detail_with_attributes_json())
        assert fp.brand == "HomeNest"

    def test_attributes_fill_material_when_absent(self):
        fp = build_product_fingerprint(_s_asin(), _sample_detail_with_attributes_json())
        assert fp.materials == ["Velvet"]

    def test_attributes_fill_product_form(self):
        fp = build_product_fingerprint(_s_asin(), _sample_detail_with_attributes_json())
        assert fp.product_form == "ThrowPillowCover"

    def test_attributes_fill_unit_count(self):
        fp = build_product_fingerprint(_s_asin(), _sample_detail_with_attributes_json())
        assert fp.package_quantity == 2

    def test_attributes_do_not_override_existing(self):
        detail = _sample_detail_with_attributes_json()
        detail["brand"] = "ExplicitBrand"
        fp = build_product_fingerprint(_s_asin(), detail)
        assert fp.brand == "ExplicitBrand"

    def test_value_from_attributes_resolves_aliases(self):
        from pickflow_mcp_server.fingerprint import (
            _ATTR_ALIAS_BRAND,
            _ATTR_ALIAS_MATERIAL,
            _ATTR_ALIAS_UNIT_COUNT,
        )
        flat = {"Brand": "Acme", "Material": "Steel", "Unit Count": "5"}
        assert _value_from_attributes(flat, _ATTR_ALIAS_BRAND) == "Acme"
        assert _value_from_attributes(flat, _ATTR_ALIAS_MATERIAL) == "Steel"
        assert _value_from_attributes(flat, _ATTR_ALIAS_UNIT_COUNT) == "5"
        assert _value_from_attributes(flat, frozenset({"Nonexistent"})) is None


# ---------------------------------------------------------------------------
# 11. Package size / weight tests
# ---------------------------------------------------------------------------

class TestPackageSizeAndWeight:
    """Parse package_size_cm L*W*H and weight_g."""

    def test_parse_package_size_cm_valid(self):
        dims = _parse_package_size_cm({"package_size_cm": "8.5*8.5*12.0"})
        assert dims is not None
        assert dims.length.value == 8.5
        assert dims.length.unit == "cm"
        assert dims.width.value == 8.5
        assert dims.height.value == 12.0

    def test_parse_package_size_cm_spaces(self):
        dims = _parse_package_size_cm({"package_size_cm": " 22.5 * 14.0 * 5.5 "})
        assert dims is not None
        assert dims.length.value == 22.5
        assert dims.width.value == 14.0

    def test_parse_package_size_cm_invalid(self):
        assert _parse_package_size_cm({"x": "y"}) is None
        assert _parse_package_size_cm({"package_size_cm": "not a size"}) is None
        assert _parse_package_size_cm({"package_size_cm": ""}) is None

    def test_weight_g_maps_to_package_weight(self):
        fp = build_product_fingerprint(_s_asin(), _sample_detail_with_package_size_cm())
        assert fp.package_weight is not None
        assert fp.package_weight.value == 150
        assert fp.package_weight.unit == "g"
        assert fp.item_weight is None  # weight_g is NOT item-scoped

    def test_package_size_cm_produces_package_dimensions(self):
        fp = build_product_fingerprint(_s_asin(), _sample_detail_with_package_size_cm())
        assert fp.package_dimensions is not None
        assert fp.package_dimensions.length.value == 8.5
        assert fp.package_dimensions.length.unit == "cm"
        assert fp.dimensions is None  # package_size_cm does NOT populate item dimensions

    def test_package_size_cm_in_source_fields(self):
        fp = build_product_fingerprint(_s_asin(), _sample_detail_with_package_size_cm())
        assert "package_size_cm" in fp.source_fields
        assert "weight_g" in fp.source_fields

    def test_dimensions_critical_unknown_when_only_package_size_cm(self):
        """Item dimensions remain a critical unknown when only package dims exist."""
        fp = build_product_fingerprint(_s_asin(), _sample_detail_with_package_size_cm())
        assert "dimensions" in fp.critical_unknowns
        assert fp.package_dimensions is not None  # package dims are present


# ---------------------------------------------------------------------------
# 12. Purchase quantity / price string parsing tests
# ---------------------------------------------------------------------------

class TestPurchaseQuantityParsing:
    """Parse purchase_quantity string variants: prefixes, suffixes, ranges."""

    def test_all_variants_extract_first_numeric(self):
        for raw_str, expected in _PURCHASE_QTY_VARIANTS:
            qty, label = _parse_purchase_quantity(raw_str)
            assert qty == expected, f"Failed for {raw_str!r}: got qty={qty}, expected={expected}"
            if raw_str is not None:
                assert label is not None or expected is None

    def test_raw_text_preserved_in_price_tier(self):
        row = {
            "title": "Test Product",
            "url": "https://detail.1688.com/test.html",
            "product_id": "t-1",
            "photo": "https://img.test/t.jpg",
            "wholesale_price_range": [
                {"price": "15.5", "purchase_quantity": "200pcs"},
            ],
        }
        cand = _normalize_one(row, "test")
        assert cand is not None
        assert len(cand.price_tiers) == 1
        assert cand.price_tiers[0].min_quantity == 200
        assert cand.price_tiers[0].raw_text == "200pcs"

    def test_range_purchase_quantity(self):
        row = {
            "title": "Ranged Product",
            "url": "https://detail.1688.com/ranged.html",
            "product_id": "r-1",
            "photo": "https://img.test/r.jpg",
            "wholesale_price_range": [
                {"price": "8.0", "purchase_quantity": "100-500"},
            ],
        }
        cand = _normalize_one(row, "test")
        assert cand is not None
        assert len(cand.price_tiers) == 1
        assert cand.price_tiers[0].min_quantity == 100
        assert cand.price_tiers[0].raw_text == "100-500"

    def test_price_as_string(self):
        row = {
            "title": "String Price Product",
            "url": "https://detail.1688.com/strprice.html",
            "product_id": "sp-1",
            "photo": "https://img.test/sp.jpg",
            "wholesale_price_range": [
                {"price": "12.99", "purchase_quantity": "50"},
            ],
        }
        cand = _normalize_one(row, "test")
        assert cand is not None
        assert len(cand.price_tiers) == 1
        assert cand.price_tiers[0].unit_price_cny == 12.99

    def test_price_as_number(self):
        row = {
            "title": "Numeric Price Product",
            "url": "https://detail.1688.com/numprice.html",
            "product_id": "np-1",
            "photo": "https://img.test/np.jpg",
            "wholesale_price_range": [
                {"price": 10.5, "purchase_quantity": 30},
            ],
        }
        cand = _normalize_one(row, "test")
        assert cand is not None
        assert len(cand.price_tiers) == 1
        assert cand.price_tiers[0].unit_price_cny == 10.5

    def test_price_comparability_never_inferred(self):
        """wholesale_price_range alone never sets price comparability beyond AMBIGUOUS."""
        # Even with pack_quantity present (which real rows never have),
        # without _per_pack_pricing the result is AMBIGUOUS.
        row = {
            "title": "Test Product",
            "url": "https://detail.1688.com/amb.html",
            "product_id": "amb-1",
            "photo": "https://img.test/amb.jpg",
            "store_name": "TestCo",
            "wholesale_price_range": [
                {"price": "5.0", "purchase_quantity": "100"},
            ],
            "pack_quantity": 2,
            "pack_unit": "pieces",
        }
        cand = _normalize_one(row, "test")
        assert cand is not None
        # Even with pack_quantity, no _per_pack_pricing -> AMBIGUOUS
        assert cand.price_comparability == PriceComparability.AMBIGUOUS
        assert cand.normalized_unit_price_cny is None

    def test_parse_price_str_helper(self):
        assert _parse_price_str("12.99") == 12.99
        assert _parse_price_str(10.5) == 10.5
        assert _parse_price_str(0) is None
        assert _parse_price_str(None) is None
        assert _parse_price_str("") is None
        assert _parse_price_str("abc") is None

    def test_parse_purchase_quantity_helper(self):
        qty, label = _parse_purchase_quantity("200pcs")
        assert qty == 200
        assert label == "200pcs"
        qty, label = _parse_purchase_quantity(None)
        assert qty is None
        assert label is None
