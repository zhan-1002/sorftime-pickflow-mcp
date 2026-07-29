"""Contract tests for the future Amazon-to-1688 backend."""

import pytest
from pydantic import ValidationError

from pickflow_mcp_server.supplier_contracts import (
    EvidenceDimension,
    EvidenceSource,
    MatchVerdict,
    PriceComparability,
    ProductFingerprint,
    ProductImage,
    SupplierCandidate,
    SupplierComparison,
    VisualReviewBundle,
    VisualReviewPair,
)


def _synthetic_asin() -> str:
    return "B0" + "SYNTHET1"


def test_fingerprint_and_visual_bundle_are_json_serializable():
    fingerprint = ProductFingerprint(
        asin=_synthetic_asin(),
        title="Synthetic bulk product",
        product_form="flat ornament",
        package_quantity=24,
        package_unit="pieces",
        images=[
            ProductImage(
                url="https://example.test/reference.jpg",
                role="main",
                source=EvidenceSource.SORFTIME_PRODUCT_DETAIL,
            )
        ],
        source_fields=["title", "main_image", "package_quantity"],
        completeness_pct=75,
    )
    bundle = VisualReviewBundle(
        asin=fingerprint.asin,
        pairs=[
            VisualReviewPair(
                candidate_id="candidate-1",
                reference_images=[fingerprint.images[0].url],
                candidate_images=["https://example.test/candidate.jpg"],
                focus_dimensions=[EvidenceDimension.PRODUCT_FORM],
            )
        ],
    )

    assert fingerprint.model_dump(mode="json")["package_quantity"] == 24
    assert bundle.model_dump(mode="json")["instructions_version"] == "codex-vision-v1"


def test_contracts_reject_raw_payloads_and_unknown_fields():
    with pytest.raises(ValidationError):
        ProductFingerprint(
            asin=_synthetic_asin(),
            title="Synthetic product",
            raw_payload={"private": "must not cross the contract boundary"},
        )


def test_ambiguous_price_cannot_be_published_as_normalized_unit_cost():
    with pytest.raises(ValidationError):
        SupplierCandidate(
            candidate_id="candidate-1",
            title="Synthetic supplier listing",
            normalized_unit_price_cny=1.25,
            price_comparability=PriceComparability.AMBIGUOUS,
        )


def test_hard_mismatch_requires_different_product_verdict():
    with pytest.raises(ValidationError):
        SupplierComparison(
            comparison_id="comparison-1",
            asin=_synthetic_asin(),
            candidate_id="candidate-1",
            verdict=MatchVerdict.POSSIBLE_SAME,
            overall_score=60,
            hard_failures=["package quantity differs"],
        )

    comparison = SupplierComparison(
        comparison_id="comparison-2",
        asin=_synthetic_asin(),
        candidate_id="candidate-1",
        verdict=MatchVerdict.DIFFERENT,
        overall_score=15,
        hard_failures=["package quantity differs"],
    )
    assert comparison.verdict is MatchVerdict.DIFFERENT
