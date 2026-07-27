"""Stable contracts for the Amazon-to-1688 sourcing workflow.

These models define the boundary between the deterministic MCP backend and the
Codex-native vision/browser workflow. Raw API payloads and model reasoning do
not belong in these contracts.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

SUPPLIER_CONTRACT_VERSION = "1.0"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EvidenceSource(str, Enum):
    SORFTIME_PRODUCT_DETAIL = "sorftime_product_detail"
    SORFTIME_1688 = "sorftime_1688"
    AMAZON_BROWSER = "amazon_browser"
    ALIBABA_BROWSER = "1688_browser"
    CODEX_VISION = "codex_vision"
    USER = "user"


class EvidenceStatus(str, Enum):
    MATCH = "match"
    MISMATCH = "mismatch"
    UNKNOWN = "unknown"


class MatchVerdict(str, Enum):
    LIKELY_SAME = "likely_same"
    POSSIBLE_SAME = "possible_same"
    INSUFFICIENT = "insufficient_evidence"
    DIFFERENT = "different"


class LookupStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    NEEDS_LOGIN = "needs_login"
    ERROR = "error"


class PriceComparability(str, Enum):
    EXACT = "exact_pack_and_tier"
    NORMALIZED = "normalized_from_pack"
    AMBIGUOUS = "ambiguous_sku_or_pack"
    UNAVAILABLE = "unavailable"


class ImageRole(str, Enum):
    MAIN = "main"
    GALLERY = "gallery"
    VARIANT = "variant"
    DETAIL = "detail"
    PACKAGING = "packaging"


class EvidenceDimension(str, Enum):
    PRODUCT_FORM = "product_form"
    MATERIAL = "material"
    DIMENSIONS = "dimensions"
    QUANTITY = "quantity"
    COMPONENTS = "components"
    COLOR_PATTERN = "color_pattern"
    PACKAGING = "packaging"
    VISUAL_IDENTITY = "visual_identity"
    TITLE_SEMANTICS = "title_semantics"
    PRICE_COMPARABILITY = "price_comparability"


class SourceReference(StrictModel):
    source: EvidenceSource
    field: str | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)


class Measurement(StrictModel):
    value: float = Field(ge=0)
    unit: str
    raw_text: str | None = None


class ProductDimensions(StrictModel):
    length: Measurement | None = None
    width: Measurement | None = None
    height: Measurement | None = None


class ProductImage(StrictModel):
    url: str
    role: ImageRole = ImageRole.GALLERY
    source: EvidenceSource
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)


class FingerprintAttribute(StrictModel):
    name: str
    value: str
    normalized_value: str | None = None
    unit: str | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)
    evidence: list[SourceReference] = Field(default_factory=list)


class ProductFingerprint(StrictModel):
    contract_version: str = SUPPLIER_CONTRACT_VERSION
    asin: str = Field(pattern=r"^[A-Z0-9]{10}$")
    marketplace: str = "US"
    title: str
    brand: str | None = None
    category: str | None = None
    product_form: str | None = None
    materials: list[str] = Field(default_factory=list)
    colors: list[str] = Field(default_factory=list)
    components: list[str] = Field(default_factory=list)
    dimensions: ProductDimensions | None = None
    item_weight: Measurement | None = None
    package_dimensions: ProductDimensions | None = None
    package_weight: Measurement | None = None
    package_quantity: float | None = Field(default=None, gt=0)
    package_unit: str | None = None
    model_number: str | None = None
    attributes: list[FingerprintAttribute] = Field(default_factory=list)
    search_terms_en: list[str] = Field(default_factory=list)
    search_terms_zh: list[str] = Field(default_factory=list)
    must_match: list[str] = Field(default_factory=list)
    must_not_match: list[str] = Field(default_factory=list)
    images: list[ProductImage] = Field(default_factory=list)
    source_fields: list[str] = Field(default_factory=list)
    critical_unknowns: list[str] = Field(default_factory=list)
    completeness_pct: float = Field(default=0, ge=0, le=100)


class PriceTier(StrictModel):
    min_quantity: float = Field(gt=0)
    unit_price_cny: float = Field(ge=0)
    unit: str | None = None
    raw_text: str | None = None


class SupplierCandidate(StrictModel):
    contract_version: str = SUPPLIER_CONTRACT_VERSION
    candidate_id: str
    offer_id: str | None = None
    title: str
    detail_url: str | None = None
    supplier_name: str | None = None
    image_urls: list[str] = Field(default_factory=list)
    search_modes: list[str] = Field(default_factory=list)
    price_tiers: list[PriceTier] = Field(default_factory=list)
    moq: float | None = Field(default=None, gt=0)
    pack_quantity: float | None = Field(default=None, gt=0)
    pack_unit: str | None = None
    normalized_unit_price_cny: float | None = Field(default=None, ge=0)
    price_comparability: PriceComparability = PriceComparability.UNAVAILABLE
    supplier_signals: dict[str, Any] = Field(default_factory=dict)
    source_fields: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    data_completeness_pct: float = Field(default=0, ge=0, le=100)

    @model_validator(mode="after")
    def validate_normalized_price(self) -> "SupplierCandidate":
        comparable = {
            PriceComparability.EXACT,
            PriceComparability.NORMALIZED,
        }
        if self.normalized_unit_price_cny is not None and self.price_comparability not in comparable:
            raise ValueError("normalized unit price requires exact or normalized price comparability")
        return self


class MatchEvidence(StrictModel):
    dimension: EvidenceDimension
    status: EvidenceStatus
    confidence: float = Field(ge=0, le=1)
    summary: str
    source: EvidenceSource
    hard_constraint: bool = False
    reference_value: str | None = None
    candidate_value: str | None = None


class SupplierComparison(StrictModel):
    contract_version: str = SUPPLIER_CONTRACT_VERSION
    comparison_id: str
    asin: str = Field(pattern=r"^[A-Z0-9]{10}$")
    candidate_id: str
    verdict: MatchVerdict
    overall_score: float = Field(ge=0, le=100)
    evidence: list[MatchEvidence] = Field(default_factory=list)
    hard_failures: list[str] = Field(default_factory=list)
    unknown_dimensions: list[EvidenceDimension] = Field(default_factory=list)
    normalized_unit_price_cny: float | None = Field(default=None, ge=0)
    price_comparability: PriceComparability = PriceComparability.UNAVAILABLE
    requires_browser_review: bool = True
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_verdict_and_price(self) -> "SupplierComparison":
        if self.hard_failures and self.verdict is not MatchVerdict.DIFFERENT:
            raise ValueError("hard failures require a different-product verdict")
        comparable = {
            PriceComparability.EXACT,
            PriceComparability.NORMALIZED,
        }
        if self.normalized_unit_price_cny is not None and self.price_comparability not in comparable:
            raise ValueError("normalized unit price requires exact or normalized price comparability")
        return self


class VisualReviewPair(StrictModel):
    candidate_id: str
    reference_images: list[str] = Field(min_length=1)
    candidate_images: list[str] = Field(min_length=1)
    focus_dimensions: list[EvidenceDimension] = Field(default_factory=list)


class VisualReviewBundle(StrictModel):
    contract_version: str = SUPPLIER_CONTRACT_VERSION
    asin: str = Field(pattern=r"^[A-Z0-9]{10}$")
    pairs: list[VisualReviewPair] = Field(default_factory=list)
    instructions_version: str = "codex-vision-v1"
    require_original_resolution: bool = True


class SupplierLookupResult(StrictModel):
    contract_version: str = SUPPLIER_CONTRACT_VERSION
    status: LookupStatus
    fingerprint: ProductFingerprint | None = None
    candidates: list[SupplierCandidate] = Field(default_factory=list)
    deterministic_comparisons: list[SupplierComparison] = Field(default_factory=list)
    visual_review: VisualReviewBundle | None = None
    api_calls: int = Field(default=0, ge=0)
    warnings: list[str] = Field(default_factory=list)
    error_code: str | None = None
