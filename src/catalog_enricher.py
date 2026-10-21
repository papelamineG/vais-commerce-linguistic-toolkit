"""Catalog Feed Enricher for Vertex AI Search for Commerce (VAIS:C).

Enriches product catalog JSON/JSONL records prior to ingestion into VAIS:C so
that exact-match retrieval and ranking succeed regardless of whether the shopper
queries in the masculine or feminine gender, singular or plural, with or without
accents, or using a regional synonym.

Key transformations:
1. `languageCode`: Guarantees consistent BCP-47 locale tagging (e.g. `pt-PT`).
2. `colorInfo.colorFamilies`: Maps localized color names to Google's 13 standard
   canonical color families (`Black`, `White`, `Gray`, `Brown`, `Red`, `Pink`,
   `Orange`, `Yellow`, `Green`, `Cyan`, `Blue`, `Purple`, `Mixed`).
3. `colorInfo.colors`: Expands localized color names (including compound shades
   like `"Preto Claro"` or `"Castanho Escuro"`) to include masculine, feminine,
   plural, and accent-stripped surface forms (up to the 75-value schema limit).
4. `attributes.linguistic_search_terms`: Adds searchable linguistic variants
   (unaccented tokens, gender inflections, and category synonyms) directly onto
   the product record.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any, Iterable, List, Sequence

from src.generator import LinguisticRuleGroup, strip_accents

# Compound shade modifier inflections (masculine <-> feminine) in Romance languages
SHADE_MODIFIER_INFLECTIONS: dict[str, str] = {
    "claro": "clara",
    "clara": "claro",
    "escuro": "escura",
    "escura": "escuro",
    "oscuro": "oscura",
    "oscura": "oscuro",
    "metalizado": "metalizada",
    "metalizada": "metalizado",
    "acetinado": "acetinada",
    "acetinada": "acetinado",
    "dourado": "dourada",
    "dourada": "dourado",
    "prateado": "prateada",
    "prateada": "prateado",
    "acobreado": "acobreada",
    "acobreada": "acobreado",
}


class CatalogLinguisticEnricher:
    """Enriches VAIS:C Product dicts using a `LinguisticRuleGroup` dictionary."""

    def __init__(
        self,
        groups: Sequence[LinguisticRuleGroup],
        default_language_code: str = "pt-PT",
    ) -> None:
        self.groups = list(groups)
        self.default_language_code = default_language_code

        # Map lowercase term -> LinguisticRuleGroup for color_gender and other categories
        self.color_term_to_group: dict[str, LinguisticRuleGroup] = {}
        self.term_to_synonyms: dict[str, list[str]] = {}

        for group in self.groups:
            for syn in group.synonyms:
                key = syn.lower().strip()
                if not key:
                    continue
                if group.category == "color_gender":
                    self.color_term_to_group[key] = group
                existing = self.term_to_synonyms.setdefault(key, [])
                for candidate in group.synonyms:
                    c_clean = candidate.lower().strip()
                    if c_clean and c_clean not in existing:
                        existing.append(c_clean)

    def expand_color_surface_forms(
        self, raw_colors: Iterable[str]
    ) -> tuple[list[str], list[str]]:
        """Expands localized color strings into all gender/number/accent forms and canonical families.

        Returns:
            `(expanded_colors, canonical_color_families)` respecting VAIS:C schema limits
            (max 75 `colors`, max 5 `colorFamilies`).
        """
        expanded_colors: list[str] = []
        seen_colors: set[str] = set()
        families: list[str] = []
        seen_families: set[str] = set()

        def _add_color(val: str) -> None:
            v = val.strip()
            if v and v.lower() not in seen_colors and len(expanded_colors) < 75:
                seen_colors.add(v.lower())
                expanded_colors.append(v)

        def _add_family(fam: str) -> None:
            f = fam.strip()
            if f and f not in seen_families and len(families) < 5:
                seen_families.add(f)
                families.append(f)

        for raw in raw_colors:
            if not raw or not isinstance(raw, str):
                continue
            _add_color(raw)
            unaccented_raw = strip_accents(raw)
            if unaccented_raw != raw:
                _add_color(unaccented_raw)

            lowered = raw.lower().strip()
            # 1. Direct match in color_gender dictionary
            matched_group = self.color_term_to_group.get(lowered) or self.color_term_to_group.get(
                strip_accents(lowered)
            )
            if matched_group:
                if matched_group.canonical_color_family:
                    _add_family(matched_group.canonical_color_family)
                for syn in matched_group.synonyms:
                    _add_color(syn.capitalize())

            # 2. Compound color handling (e.g. "Preto Claro" -> "Preta Clara" + base "Preto"/"Preta")
            tokens = re.findall(r"\w+", lowered)
            if len(tokens) >= 1:
                head = tokens[0]
                head_group = self.color_term_to_group.get(head) or self.color_term_to_group.get(
                    strip_accents(head)
                )
                if head_group:
                    if head_group.canonical_color_family:
                        _add_family(head_group.canonical_color_family)
                    for syn in head_group.synonyms:
                        _add_color(syn.capitalize())
                    # If compound (e.g. "Preto Claro"), also synthesize feminine compound ("Preta Clara")
                    if len(tokens) == 2 and tokens[1] in SHADE_MODIFIER_INFLECTIONS:
                        alt_mod = SHADE_MODIFIER_INFLECTIONS[tokens[1]]
                        for syn in head_group.synonyms[:2]:
                            _add_color(f"{syn.capitalize()} {alt_mod.capitalize()}")

        return expanded_colors, families

    def enrich_product(self, product: dict[str, Any]) -> dict[str, Any]:
        """Returns an enriched copy of a VAIS:C Product dictionary."""
        enriched = copy.deepcopy(product)

        # 1. Ensure languageCode is set
        if not enriched.get("languageCode"):
            enriched["languageCode"] = self.default_language_code

        # 2. Enrich colorInfo (both primary and variants if present)
        color_info = enriched.get("colorInfo") or {}
        raw_colors = color_info.get("colors") or []
        if raw_colors:
            expanded_colors, canonical_families = self.expand_color_surface_forms(raw_colors)
            color_info["colors"] = expanded_colors
            existing_families = color_info.get("colorFamilies") or []
            merged_families = list(dict.fromkeys([*existing_families, *canonical_families]))[:5]
            if merged_families:
                color_info["colorFamilies"] = merged_families
            enriched["colorInfo"] = color_info

        # 3. Collect searchable linguistic variants from title and categories
        text_blobs: list[str] = []
        if isinstance(enriched.get("title"), str):
            text_blobs.append(enriched["title"])
        for cat in enriched.get("categories") or []:
            if isinstance(cat, str):
                text_blobs.append(cat)

        extra_terms: list[str] = []
        seen_extra: set[str] = set()

        def _add_extra(term: str) -> None:
            t = term.lower().strip()
            if t and t not in seen_extra:
                seen_extra.add(t)
                extra_terms.append(t)

        combined_text = " ".join(text_blobs).lower()
        unaccented_title = strip_accents(enriched.get("title", "")).strip()
        if unaccented_title and unaccented_title.lower() != enriched.get("title", "").lower():
            _add_extra(unaccented_title)

        words = set(re.findall(r"\w+", combined_text))
        for word in words:
            unacc = strip_accents(word)
            if unacc != word:
                _add_extra(unacc)
            for cand in self.term_to_synonyms.get(word, []):
                if cand != word:
                    _add_extra(cand)

        if extra_terms:
            attributes = enriched.setdefault("attributes", {})
            existing_attr = attributes.get("linguistic_search_terms", {}).get("text", [])
            merged_text = list(dict.fromkeys([*existing_attr, *extra_terms]))[:50]
            attributes["linguistic_search_terms"] = {
                "text": merged_text,
            }

        # 4. Recursively enrich inline variants if present
        if isinstance(enriched.get("variants"), list):
            enriched["variants"] = [
                self.enrich_product(v) if isinstance(v, dict) else v
                for v in enriched["variants"]
            ]

        return enriched


def enrich_catalog_file(
    input_path: Path,
    output_path: Path,
    groups: Sequence[LinguisticRuleGroup],
    default_language_code: str = "pt-PT",
) -> dict[str, int]:
    """Reads a JSON or JSONL catalog file, enriches every product, and writes JSONL output."""
    enricher = CatalogLinguisticEnricher(groups, default_language_code=default_language_code)
    content = input_path.read_text(encoding="utf-8").strip()
    if content.startswith("["):
        records = json.loads(content)
    else:
        records = [json.loads(line) for line in content.splitlines() if line.strip()]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    colors_expanded_count = 0
    attributes_added_count = 0

    with output_path.open("w", encoding="utf-8") as f:
        for item in records:
            before_colors = len((item.get("colorInfo") or {}).get("colors") or [])
            enriched = enricher.enrich_product(item)
            after_colors = len((enriched.get("colorInfo") or {}).get("colors") or [])
            if after_colors > before_colors:
                colors_expanded_count += 1
            if "linguistic_search_terms" in (enriched.get("attributes") or {}):
                attributes_added_count += 1
            f.write(json.dumps(enriched, ensure_ascii=False) + "\n")

    return {
        "total_products": len(records),
        "products_with_expanded_colors": colors_expanded_count,
        "products_with_linguistic_attributes": attributes_added_count,
    }
