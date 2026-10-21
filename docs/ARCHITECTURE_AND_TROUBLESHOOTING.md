# Deep-Dive Architecture & Troubleshooting Guide

## 1. Understanding How VAIS:C Processes Gender, Accents, and Synonyms

When a shopper submits a query $q$ (for example, `q = "cadeira preta"`) to `projects/*/locations/global/catalogs/default_catalog/servingConfigs/default_search:search`, Vertex AI Search for Commerce processes the query through two distinct stages:

1. **Stage A — Internal Query Rewriting (`qrewrite`)**:
   * Runs Google's internal multilingual morphological and synonym expansion to produce an internal structured query (`Squery`).
   * If a query token (e.g. feminine `"preta"` or Spanish `"roja"`) is not directly matched against high-confidence catalog attribute tokens, `qrewrite` may expand it to cross-language equivalents (e.g., English `"black"` or `"red"`).
   * If your catalog has popular products with English words in their brand, line, or collection title, those items can receive an unintended lexical boost.
2. **Stage B — Customer Serving Controls (`ControlService`)**:
   * Applies customer-defined `TwowaySynonymsAction`, `OnewaySynonymsAction`, `ReplacementAction`, `DoNotAssociateAction`, and `BoostAction` rules attached to the active `ServingConfig`.

### Why Combine Pillar 1 (Serving Controls) + Pillar 2 (`ColorInfo` Catalog Enrichment)?

| Mechanism | What It Solves | Limitation When Used Alone |
| :--- | :--- | :--- |
| **Pillar 1: Two-Way Synonym Controls (`TwowaySynonymsAction`)** | Immediately equates `preto ↔ preta`, `sofá ↔ sofa`, and `cadeirão ↔ poltrona` at query time without waiting for a catalog re-import. | Runs alongside internal `qrewrite`; if `colorInfo.colors` only contains `"Preto"`, exact-match attribute scoring can still slightly favor `"preto"` or allow `qrewrite` cross-language expansions. |
| **Pillar 2: `colorInfo.colors` + `colorFamilies` Enrichment** | Populates `["Preto", "Preta", "Pretos", "Pretas"]` and `colorFamilies: ["Black"]` directly on the product record, giving both `"cadeira preto"` and `"cadeira preta"` an identical native exact match. | Requires a catalog import/patch cycle to update product records. |
| **Pillar 1 + Pillar 2 Together** | **Immediate query-time synonym & accent coverage + 100% symmetric exact-match attribute scoring.** | None (Gold Standard). |

---

## 2. Quota-Safe Control Design (100 Controls × 100 Synonyms)

Vertex AI Search for Commerce enforces the following default limits:
* **Maximum Controls per ServingConfig**: `100` (for non-redirect controls).
* **Maximum Synonyms per `TwowaySynonymsAction`**: `100` terms (`repeated string synonyms = 1`).

Instead of creating separate 2-word controls (`preto ↔ preta`, `preto ↔ pretos`, `preto ↔ negro`), always group all morphological inflections (masculine singular, feminine singular, masculine plural, feminine plural, unaccented spelling, and close shade synonyms) into a **single `TwowaySynonymsAction` control per concept**:

```json
{
  "displayName": "PT Color Gender: Preto / Preta (Black)",
  "solutionTypes": ["SOLUTION_TYPE_SEARCH"],
  "searchSolutionUseCase": ["SEARCH_SOLUTION_USE_CASE_SEARCH"],
  "rule": {
    "condition": {},
    "twowaySynonymsAction": {
      "synonyms": [
        "preto",
        "preta",
        "pretos",
        "pretas",
        "negro",
        "negra",
        "negros",
        "negras"
      ]
    }
  }
}
```

With this design, an entire language's color palette (14 groups), core furniture taxonomy (12–15 groups), and material vocabulary (5–8 groups) fits comfortably inside **30–35 controls**, leaving over 60% of the ServingConfig control quota free for merchandising boost/bury or pinning rules.

---

## 3. Storefront Variant Image Resolution (`results[].product.variants[0].images[0].uri`)

In catalogs with `PRIMARY` and `VARIANT` products (such as a dining chair offered in Oak, White, and Black), `SearchService.Search` returns `PRIMARY` items as the top-level search hits.

* The top-level `results[].product.images` array on the `PRIMARY` product is rolled up from an arbitrary variant.
* When the shopper's query specifies a variant attribute (e.g., `"cadeira preta"`), VAIS:C populates `results[].product.variants` sorted by relevance to the query.
* Therefore, your storefront frontend should select the product card thumbnail using:

```javascript
function getProductCardImage(searchResult) {
  const product = searchResult.product || {};
  const matchedVariantImage = product.variants?.[0]?.images?.[0]?.uri;
  if (matchedVariantImage) {
    return matchedVariantImage;
  }
  return product.images?.[0]?.uri || "/assets/placeholder.png";
}
```

---

## 4. Escalating Persistent Upstream `qrewrite` Collisions

In rare cases, even after applying Two-Way Synonym Controls and `ColorInfo` enrichment, a specific Romance-language adjective may trigger an unwanted cross-language expansion inside Google's internal `qrewrite` stage.

To verify and resolve this:
1. Run `python3 -m src.cli diagnose --query-a "cadeira preto" --query-b "cadeira preta"`.
2. Copy the two `attribution_token` strings from the JSON output, along with the `project_number` and `serving_config` ID.
3. Share them with your Google Cloud Customer Engineer to inspect the internal `Squery` and, if needed, add the unwanted expansion pair to the project's internal `synonym_blocklist`.
