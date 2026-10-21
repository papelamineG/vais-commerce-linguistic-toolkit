"""Search A/B Diagnostics, Attribution Token Inspector & Variant Image Resolver.

Provides two production utilities for Vertex AI Search for Commerce (VAIS:C):
1. `compare_query_pair`: Runs side-by-side search requests (e.g. `"cadeira preto"`
   vs. `"cadeira preta"` or `"sofá"` vs. `"sofa"`), calculates Top-K result
   overlap (Jaccard similarity & rank correlation), and records both
   `attributionToken` strings for query-expansion (`qrewrite`) inspection.
2. `resolve_display_image`: Implements the storefront best practice for rendering
   the color-matched variant thumbnail (`results[].product.variants[0].images[0].uri`)
   instead of the randomly rolled-up primary product thumbnail.
"""

from __future__ import annotations

from typing import Any

from src.config import ToolkitConfig
from src.controls_sync import RETAIL_API_BASE, _get_access_token, _rest_request


def resolve_display_image(search_result_item: dict[str, Any]) -> str | None:
    """Returns the matched variant thumbnail URI, falling back to the primary image.

    Why this matters:
    When a shopper searches for a color-specific query like `"cadeira preta"`,
    VAIS:C returns the `PRIMARY` product plus up to 5 matching `VARIANT` items
    inside `results[].product.variants` (or `results[].matchingVariantFields`).
    The top-level `results[].product.images[0].uri` on the primary item is
    rolled up from an arbitrary variant and may display a white or beige chair
    in the Merchandising Console preview. Always prefer `variants[0].images[0].uri`
    on the storefront search listing page (PLP).
    """
    product = search_result_item.get("product") or search_result_item
    variants = product.get("variants") or []
    for variant in variants:
        v_images = variant.get("images") or []
        if v_images and isinstance(v_images[0], dict) and v_images[0].get("uri"):
            return v_images[0]["uri"]

    p_images = product.get("images") or []
    if p_images and isinstance(p_images[0], dict) and p_images[0].get("uri"):
        return p_images[0]["uri"]
    return None


def compute_overlap_metrics(ids_a: list[str], ids_b: list[str]) -> dict[str, Any]:
    """Computes Jaccard overlap and exact rank match rate between two result ID lists."""
    set_a, set_b = set(ids_a), set(ids_b)
    union = set_a | set_b
    intersection = set_a & set_b
    jaccard = len(intersection) / len(union) if union else 1.0

    min_len = min(len(ids_a), len(ids_b))
    exact_position_matches = sum(1 for i in range(min_len) if ids_a[i] == ids_b[i])
    rank_agreement = exact_position_matches / min_len if min_len > 0 else 1.0

    return {
        "count_query_a": len(ids_a),
        "count_query_b": len(ids_b),
        "shared_products": len(intersection),
        "jaccard_similarity": round(jaccard, 4),
        "exact_rank_agreement": round(rank_agreement, 4),
        "only_in_query_a": [pid for pid in ids_a if pid not in set_b][:10],
        "only_in_query_b": [pid for pid in ids_b if pid not in set_a][:10],
    }


def run_search_query(
    query: str,
    config: ToolkitConfig,
    visitor_id: str = "diagnostic-visitor-001",
    page_size: int = 20,
) -> dict[str, Any]:
    """Executes a Search request against VAIS:C and returns results + attributionToken."""
    token = _get_access_token()
    url = f"{RETAIL_API_BASE}/{config.serving_config_path}:search"
    body = {
        "query": query,
        "visitorId": visitor_id,
        "pageSize": page_size,
        "branch": config.branch_parent,
    }
    status, resp = _rest_request("POST", url, token, config.project_id, body)
    if status != 200:
        raise RuntimeError(f"Search API returned HTTP {status}: {resp}")

    results = resp.get("results") or []
    product_ids = [r.get("id") or (r.get("product") or {}).get("id", "") for r in results]
    return {
        "query": query,
        "attribution_token": resp.get("attributionToken", ""),
        "total_size": resp.get("totalSize", 0),
        "product_ids": product_ids,
        "sample_results": [
            {
                "id": r.get("id"),
                "title": (r.get("product") or {}).get("title"),
                "display_image_uri": resolve_display_image(r),
            }
            for r in results[:5]
        ],
    }


def compare_query_pair(
    query_a: str,
    query_b: str,
    config: ToolkitConfig,
    page_size: int = 20,
) -> dict[str, Any]:
    """Compares two queries (e.g. masculine vs feminine or accented vs unaccented)."""
    res_a = run_search_query(query_a, config=config, page_size=page_size)
    res_b = run_search_query(query_b, config=config, page_size=page_size)
    metrics = compute_overlap_metrics(res_a["product_ids"], res_b["product_ids"])

    diagnosis = "HEALTHY: High overlap between both linguistic surface forms."
    if metrics["jaccard_similarity"] < 0.8:
        diagnosis = (
            "DIVERGENCE DETECTED: Top results differ significantly between the two surface forms. "
            "1) Apply Two-Way Synonym Controls (`sync-controls`) and enrich `colorInfo.colors` (`enrich-catalog`). "
            "2) Wait 5 minutes for control propagation and re-test. "
            "3) If divergence persists after Two-Way Synonym Controls are active, share both "
            "`attribution_token` values below with your Google Cloud Customer Engineer to check "
            "whether internal `qrewrite` is injecting a cross-language synonym that requires an "
            "internal `synonym_blocklist` entry."
        )

    return {
        "query_a": res_a,
        "query_b": res_b,
        "metrics": metrics,
        "diagnosis": diagnosis,
    }
