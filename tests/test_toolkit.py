"""Unit tests for the VAIS:C Linguistic Toolkit."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.catalog_enricher import CatalogLinguisticEnricher, enrich_catalog_file
from src.config import ToolkitConfig
from src.controls_sync import build_control_rest_payload, sync_controls_to_vais
from src.diagnostics import compute_overlap_metrics, resolve_display_image
from src.generator import (
    export_dictionary_csv,
    extract_catalog_vocabulary,
    load_dictionary,
    save_dictionary_json,
    slugify_control_id,
    strip_accents,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestVaisLinguisticToolkit(unittest.TestCase):
    def test_strip_accents_and_slugify(self) -> None:
        self.assertEqual(strip_accents("cadeirão de pé sofá"), "cadeirao de pe sofa")
        self.assertEqual(
            slugify_control_id("pt", "Cadeirão / Poltrona!"),
            "pt-cadeirao-poltrona",
        )

    def test_pt_dictionary_csv_load_and_accent_expansion(self) -> None:
        csv_path = REPO_ROOT / "data" / "pt_pt_furniture_dictionary.csv"
        groups = load_dictionary(csv_path)
        self.assertGreaterEqual(len(groups), 25)

        by_id = {g.control_id: g for g in groups}
        self.assertIn("pt-color-preto", by_id)
        preto = by_id["pt-color-preto"]
        self.assertEqual(preto.canonical_color_family, "Black")
        self.assertIn("preto", preto.synonyms)
        self.assertIn("preta", preto.synonyms)
        self.assertIn("pretos", preto.synonyms)
        self.assertIn("pretas", preto.synonyms)

        cadeirao = by_id["pt-cat-cadeirao"]
        self.assertIn("cadeirão", cadeirao.synonyms)
        self.assertIn("cadeirao", cadeirao.synonyms)
        self.assertIn("poltrona", cadeirao.synonyms)

    def test_build_control_rest_payload(self) -> None:
        csv_path = REPO_ROOT / "data" / "pt_pt_furniture_dictionary.csv"
        groups = load_dictionary(csv_path)
        payload = build_control_rest_payload(
            groups[0],
            catalog_parent="projects/12345/locations/global/catalogs/default_catalog",
        )
        self.assertEqual(payload["solutionTypes"], ["SOLUTION_TYPE_SEARCH"])
        self.assertIn("twowaySynonymsAction", payload["rule"])
        self.assertIn("preta", payload["rule"]["twowaySynonymsAction"]["synonyms"])

    def test_catalog_enricher_expands_compound_gender_colors_and_accents(self) -> None:
        csv_path = REPO_ROOT / "data" / "pt_pt_furniture_dictionary.csv"
        groups = load_dictionary(csv_path)
        enricher = CatalogLinguisticEnricher(groups, default_language_code="pt-PT")

        sample_product = {
            "id": "173688",
            "title": "Cadeira de jantar em madeira de olmo Uish Edition Preto Claro",
            "categories": ["Móveis > Cadeiras > Cadeiras de jantar"],
            "colorInfo": {"colors": ["Preto Claro"]},
        }
        enriched = enricher.enrich_product(sample_product)
        self.assertEqual(enriched["languageCode"], "pt-PT")
        self.assertEqual(enriched["colorInfo"]["colorFamilies"], ["Black"])
        colors_lower = [c.lower() for c in enriched["colorInfo"]["colors"]]
        self.assertIn("preto claro", colors_lower)
        self.assertIn("preta clara", colors_lower)
        self.assertIn("preto", colors_lower)
        self.assertIn("preta", colors_lower)

        # Check accent-stripped title/category search terms
        attr_terms = enriched["attributes"]["linguistic_search_terms"]["text"]
        self.assertTrue(any("moveis" in t for t in attr_terms))

    def test_variant_image_rollup_resolver(self) -> None:
        search_item = {
            "id": "173688",
            "product": {
                "id": "173688",
                "images": [{"uri": "https://cdn.example.com/primary-random.jpg"}],
                "variants": [
                    {
                        "id": "173688-preto",
                        "images": [{"uri": "https://cdn.example.com/variant-black.jpg"}],
                    }
                ],
            },
        }
        self.assertEqual(
            resolve_display_image(search_item),
            "https://cdn.example.com/variant-black.jpg",
        )

    def test_dry_run_sync_and_catalog_file_enrichment(self) -> None:
        csv_path = REPO_ROOT / "data" / "pt_pt_furniture_dictionary.csv"
        sample_catalog = REPO_ROOT / "examples" / "sample_catalog_pt.jsonl"
        groups = load_dictionary(csv_path)

        cfg = ToolkitConfig(
            project_id="demo-project",
            project_number="123456789",
            gemini_model="",
            gemini_location="global",
            vais_location="global",
            vais_catalog="default_catalog",
            vais_branch="0",
            vais_serving_config="default_search",
            default_language_code="pt-PT",
        )
        sync_res = sync_controls_to_vais(groups, config=cfg, dry_run=True)
        self.assertTrue(sync_res["dry_run"])
        self.assertEqual(sync_res["total_groups"], len(groups))
        self.assertIsNone(sync_res["quota_warning"])

        with tempfile.TemporaryDirectory() as tmpdir:
            out_catalog = Path(tmpdir) / "enriched.jsonl"
            stats = enrich_catalog_file(sample_catalog, out_catalog, groups)
            self.assertEqual(stats["total_products"], 5)
            self.assertEqual(stats["products_with_expanded_colors"], 5)
            vocab = extract_catalog_vocabulary(out_catalog)
            self.assertIn("Preta Clara", vocab["colors"])

        metrics = compute_overlap_metrics(["a", "b", "c"], ["a", "b", "d"])
        self.assertEqual(metrics["shared_products"], 2)


if __name__ == "__main__":
    unittest.main()
