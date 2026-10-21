"""GenAI Linguistic Dictionary Generator for Vertex AI Search for Commerce.

Generates and normalizes grammatical gender inflections (masculine/feminine,
singular/plural), accent/diacritic variations, and regional/domain synonyms
for Romance-language e-commerce catalogs (pt-PT, es-ES, fr-FR, it-IT).

Supports both:
1. Live Gemini structured generation (`google-genai` SDK reading `GEMINI_MODEL`
   and `GOOGLE_CLOUD_LOCATION` from environment variables).
2. Deterministic Unicode NFD accent normalization and CSV/JSON import/export
   so merchandising & SEO teams can review and edit rules in spreadsheets.
"""

from __future__ import annotations

import csv
import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, List, Sequence

from src.config import ToolkitConfig


def strip_accents(text: str) -> str:
    """Removes diacritics/accents from a UTF-8 string using Unicode NFD."""
    normalized = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")


def slugify_control_id(prefix: str, label: str) -> str:
    """Produces a valid VAIS:C control_id (4-63 chars, /[a-z][0-9]-_/)."""
    clean = strip_accents(label.lower())
    clean = re.sub(r"[^a-z0-9_-]+", "-", clean).strip("-_")
    raw = f"{prefix}-{clean}" if prefix else clean
    if not raw or not raw[0].isalpha():
        raw = f"syn-{raw}"
    return raw[:63].rstrip("-_")


@dataclass
class LinguisticRuleGroup:
    """Represents a single Synonym Control rule group for VAIS:C."""

    control_id: str
    display_name: str
    category: str  # "color_gender", "category_synonym", "accent_variant", "material_gender"
    language_code: str
    rule_type: str = "twoway"  # "twoway" or "oneway"
    canonical_color_family: str = ""  # Optional standard Google ColorInfo.colorFamilies value
    query_terms: List[str] = field(default_factory=list)  # Used when rule_type == "oneway"
    synonyms: List[str] = field(default_factory=list)  # Up to 100 terms per VAIS:C rule
    notes: str = ""

    def expand_accent_variants(self) -> "LinguisticRuleGroup":
        """Ensures every accented term in synonyms also includes its unaccented form."""
        expanded: List[str] = []
        seen = set()
        for term in self.synonyms:
            cleaned = term.strip().lower()
            if not cleaned:
                continue
            if cleaned not in seen:
                seen.add(cleaned)
                expanded.append(cleaned)
            unaccented = strip_accents(cleaned)
            if unaccented and unaccented not in seen:
                seen.add(unaccented)
                expanded.append(unaccented)
        # VAIS:C TwowaySynonymsAction allows a maximum of 100 synonyms per control
        self.synonyms = expanded[:100]
        return self


def extract_catalog_vocabulary(catalog_path: Path, max_terms: int = 300) -> dict[str, list[str]]:
    """Extracts unique colors, categories, and product nouns from a JSON/JSONL catalog."""
    colors: set[str] = set()
    categories: set[str] = set()
    titles: list[str] = []

    content = catalog_path.read_text(encoding="utf-8").strip()
    if content.startswith("["):
        records = json.loads(content)
    else:
        records = [json.loads(line) for line in content.splitlines() if line.strip()]

    for item in records:
        color_info = item.get("colorInfo") or {}
        for c in color_info.get("colors", []):
            if c and isinstance(c, str):
                colors.add(c.strip())
        for cat in item.get("categories", []):
            if cat and isinstance(cat, str):
                for part in cat.split(">"):
                    part = part.strip()
                    if part:
                        categories.add(part)
        title = item.get("title", "")
        if title and isinstance(title, str) and len(titles) < max_terms:
            titles.append(title.strip())

    return {
        "colors": sorted(colors)[:max_terms],
        "categories": sorted(categories)[:max_terms],
        "sample_titles": titles[:100],
    }


def generate_dictionary_with_gemini(
    vocabulary: dict[str, Any],
    language_code: str = "pt-PT",
    config: ToolkitConfig | None = None,
) -> List[LinguisticRuleGroup]:
    """Calls Gemini with structured JSON output to build linguistic rule groups.

    Reads `GEMINI_MODEL` and `GOOGLE_CLOUD_LOCATION` from `ToolkitConfig`.
    """
    cfg = config or ToolkitConfig.from_env(require_gemini=True)
    if not cfg.gemini_model:
        raise ValueError(
            "GEMINI_MODEL is not set. Configure GEMINI_MODEL in your environment or .env file."
        )

    from google import genai
    from google.genai import types
    from pydantic import BaseModel, Field

    class GeneratedGroup(BaseModel):
        control_id: str = Field(
            description="Short ASCII identifier (4-50 chars, lowercase letters, digits, hyphens), e.g. 'pt-color-preto'."
        )
        display_name: str = Field(
            description="Human-readable label for the Merchandising Console, e.g. 'PT Color Gender: Preto / Preta'."
        )
        category: str = Field(
            description="One of: 'color_gender', 'category_synonym', 'accent_variant', 'material_gender'."
        )
        canonical_color_family: str = Field(
            default="",
            description=(
                "If category is 'color_gender', one of Google's standard ColorInfo.colorFamilies: "
                "Black, White, Gray, Brown, Red, Pink, Orange, Yellow, Green, Cyan, Blue, Purple, Mixed. "
                "Empty string otherwise."
            ),
        )
        synonyms: list[str] = Field(
            description=(
                "All equivalent surface forms in lowercase: masculine singular, feminine singular, "
                "masculine plural, feminine plural, unaccented variations, and regional/domain synonyms."
            )
        )
        notes: str = Field(
            default="",
            description="Brief explanation of why shoppers use these variants.",
        )

    class GeneratedDictionary(BaseModel):
        groups: list[GeneratedGroup]

    client = genai.Client(
        vertexai=True,
        project=cfg.project_id or None,
        location=cfg.gemini_location,
    )

    prompt = f"""You are a Romance-language Search Relevance Linguist for Vertex AI Search for Commerce (VAIS:C).
Target storefront locale: {language_code}

In e-commerce catalogs for furniture, lighting, and home decor, product titles and color attributes are
frequently stored only in one grammatical gender (e.g., masculine "Preto", "Branco", "Castanho"), while
shoppers search using the grammatical gender of the furniture noun (e.g., "cadeira preta", "mesa branca",
"poltrona castanha"), omit diacritics/accents ("sofa preto", "cadeirao", "armario", "candeeiro de pe"),
or use regional/domain synonyms ("cadeirão" vs "poltrona", "candeeiro" vs "luminária" vs "abajur").

Given the following catalog vocabulary:
{json.dumps(vocabulary, ensure_ascii=False, indent=2)}

Generate a comprehensive list of Two-Way Synonym Rule Groups (`groups`) covering:
1. `color_gender`: Every color adjective with its masculine singular, feminine singular, masculine plural,
   feminine plural, common shade modifiers, and accent-free forms, mapped to Google's standard `canonical_color_family`.
2. `category_synonym`: Furniture & home decor product nouns with their regional synonyms, singular/plural forms,
   and accent-free spellings.
3. `material_gender`: Material and finish adjectives/nouns (wood, metal, velvet, linen, rattan, matte, glossy)
   with masculine/feminine/plural and accent-free forms.
4. `accent_variant`: High-frequency accented search terms paired with their unaccented counterparts.

Rules:
- Do NOT include cross-language English translations (like 'red' or 'black') inside Romance synonym groups,
  as English brand/collection words in product titles can pollute search rankings.
- Each group must contain between 2 and 50 lowercase terms.
"""

    response = client.models.generate_content(
        model=cfg.gemini_model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=GeneratedDictionary,
            temperature=0.1,
        ),
    )

    parsed = json.loads(response.text)
    results: List[LinguisticRuleGroup] = []
    for item in parsed.get("groups", []):
        group = LinguisticRuleGroup(
            control_id=slugify_control_id("", item["control_id"]),
            display_name=item["display_name"][:128],
            category=item["category"],
            language_code=language_code,
            rule_type="twoway",
            canonical_color_family=item.get("canonical_color_family", ""),
            synonyms=item.get("synonyms", []),
            notes=item.get("notes", ""),
        ).expand_accent_variants()
        if len(group.synonyms) >= 2:
            results.append(group)
    return results


def save_dictionary_json(groups: Sequence[LinguisticRuleGroup], output_path: Path) -> None:
    """Writes rule groups to a structured JSON file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "1.0",
        "total_controls": len(groups),
        "groups": [asdict(g) for g in groups],
    }
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_dictionary_json(input_path: Path) -> List[LinguisticRuleGroup]:
    """Loads rule groups from a structured JSON file."""
    raw = json.loads(input_path.read_text(encoding="utf-8"))
    items = raw.get("groups", raw) if isinstance(raw, dict) else raw
    groups: List[LinguisticRuleGroup] = []
    for item in items:
        group = LinguisticRuleGroup(
            control_id=slugify_control_id("", item["control_id"]),
            display_name=item["display_name"],
            category=item.get("category", "custom"),
            language_code=item.get("language_code", "pt-PT"),
            rule_type=item.get("rule_type", "twoway"),
            canonical_color_family=item.get("canonical_color_family", ""),
            query_terms=item.get("query_terms", []),
            synonyms=item.get("synonyms", []),
            notes=item.get("notes", ""),
        ).expand_accent_variants()
        groups.append(group)
    return groups


def export_dictionary_csv(groups: Sequence[LinguisticRuleGroup], csv_path: Path) -> None:
    """Exports rule groups to a spreadsheet-friendly CSV for SEO/Merchandising teams."""
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "control_id",
        "display_name",
        "category",
        "language_code",
        "rule_type",
        "canonical_color_family",
        "query_terms",
        "synonyms",
        "notes",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for g in groups:
            writer.writerow(
                {
                    "control_id": g.control_id,
                    "display_name": g.display_name,
                    "category": g.category,
                    "language_code": g.language_code,
                    "rule_type": g.rule_type,
                    "canonical_color_family": g.canonical_color_family,
                    "query_terms": " | ".join(g.query_terms),
                    "synonyms": " | ".join(g.synonyms),
                    "notes": g.notes,
                }
            )


def load_dictionary_csv(csv_path: Path) -> List[LinguisticRuleGroup]:
    """Loads rule groups from a CSV file edited by SEO/Merchandising teams."""
    groups: List[LinguisticRuleGroup] = []
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw_syns = row.get("synonyms", "")
            sep = "|" if "|" in raw_syns else ","
            synonyms = [s.strip() for s in raw_syns.split(sep) if s.strip()]
            raw_qterms = row.get("query_terms", "")
            qsep = "|" if "|" in raw_qterms else ","
            query_terms = [q.strip() for q in raw_qterms.split(qsep) if q.strip()]
            group = LinguisticRuleGroup(
                control_id=slugify_control_id("", row["control_id"]),
                display_name=row["display_name"].strip(),
                category=row.get("category", "custom").strip(),
                language_code=row.get("language_code", "pt-PT").strip(),
                rule_type=row.get("rule_type", "twoway").strip() or "twoway",
                canonical_color_family=row.get("canonical_color_family", "").strip(),
                query_terms=query_terms,
                synonyms=synonyms,
                notes=row.get("notes", "").strip(),
            ).expand_accent_variants()
            if len(group.synonyms) >= 2 or (group.rule_type == "oneway" and group.synonyms):
                groups.append(group)
    return groups


def load_dictionary(path: Path) -> List[LinguisticRuleGroup]:
    """Loads a linguistic dictionary from either `.json` or `.csv`."""
    if path.suffix.lower() == ".csv":
        return load_dictionary_csv(path)
    return load_dictionary_json(path)
