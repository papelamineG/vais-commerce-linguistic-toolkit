"""Command-Line Interface for the VAIS:C Linguistic Toolkit."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.catalog_enricher import enrich_catalog_file
from src.config import ToolkitConfig
from src.controls_sync import sync_controls_to_vais
from src.diagnostics import compare_query_pair
from src.generator import (
    export_dictionary_csv,
    extract_catalog_vocabulary,
    generate_dictionary_with_gemini,
    load_dictionary,
    save_dictionary_json,
)


def cmd_generate(args: argparse.Namespace) -> int:
    cfg = ToolkitConfig.from_env(require_gemini=True)
    vocab = extract_catalog_vocabulary(Path(args.catalog))
    print(
        f"[1/3] Extracted {len(vocab['colors'])} unique colors and "
        f"{len(vocab['categories'])} categories from {args.catalog}"
    )
    print(
        f"[2/3] Generating linguistic rule groups for locale '{args.language}' "
        f"using model='{cfg.gemini_model}' (location='{cfg.gemini_location}')..."
    )
    groups = generate_dictionary_with_gemini(
        vocabulary=vocab,
        language_code=args.language,
        config=cfg,
    )
    out_json = Path(args.output_json)
    save_dictionary_json(groups, out_json)
    print(f"[3/3] Saved {len(groups)} rule groups to {out_json}")
    if args.output_csv:
        out_csv = Path(args.output_csv)
        export_dictionary_csv(groups, out_csv)
        print(f"      Exported spreadsheet-ready CSV to {out_csv}")
    return 0


def cmd_convert(args: argparse.Namespace) -> int:
    in_path = Path(args.input)
    out_path = Path(args.output)
    groups = load_dictionary(in_path)
    if out_path.suffix.lower() == ".csv":
        export_dictionary_csv(groups, out_path)
    else:
        save_dictionary_json(groups, out_path)
    print(
        f"Converted {len(groups)} linguistic rule groups (with automatic accent normalization) "
        f"from {in_path} -> {out_path}"
    )
    return 0


def cmd_sync_controls(args: argparse.Namespace) -> int:
    dry_run = not args.apply
    cfg = ToolkitConfig.from_env(require_gcp=not dry_run)
    if not cfg.project_number:
        # Provide a placeholder in dry-run mode if .env is not set yet
        cfg = ToolkitConfig(
            project_id="YOUR_PROJECT_ID",
            project_number="YOUR_PROJECT_NUMBER",
            gemini_model=cfg.gemini_model,
            gemini_location=cfg.gemini_location,
            vais_location=cfg.vais_location,
            vais_catalog=cfg.vais_catalog,
            vais_branch=cfg.vais_branch,
            vais_serving_config=cfg.vais_serving_config,
            default_language_code=cfg.default_language_code,
        )

    groups = load_dictionary(Path(args.dictionary))
    result = sync_controls_to_vais(
        groups=groups,
        config=cfg,
        dry_run=dry_run,
        attach_to_serving_config=not args.no_attach,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not result.get("errors") else 1


def cmd_enrich_catalog(args: argparse.Namespace) -> int:
    groups = load_dictionary(Path(args.dictionary))
    stats = enrich_catalog_file(
        input_path=Path(args.input),
        output_path=Path(args.output),
        groups=groups,
        default_language_code=args.language,
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


def cmd_diagnose(args: argparse.Namespace) -> int:
    cfg = ToolkitConfig.from_env(require_gcp=True)
    report = compare_query_pair(
        query_a=args.query_a,
        query_b=args.query_b,
        config=cfg,
        page_size=args.page_size,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vais-linguistic-toolkit",
        description=(
            "Vertex AI Search for Commerce (VAIS:C) Linguistic Toolkit — "
            "Solves grammatical gender (preto/preta), accent/diacritic, and synonym "
            "discrepancies in Romance-language catalogs."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # 1. generate
    p_gen = sub.add_parser(
        "generate",
        help="Use Gemini to generate gender, accent, and synonym rule groups from a catalog feed.",
    )
    p_gen.add_argument("--catalog", required=True, help="Path to input catalog (.jsonl or .json).")
    p_gen.add_argument("--language", default="pt-PT", help="BCP-47 language code (default: pt-PT).")
    p_gen.add_argument("--output-json", required=True, help="Output JSON dictionary path.")
    p_gen.add_argument("--output-csv", default="", help="Optional output CSV path for spreadsheet editing.")
    p_gen.set_defaults(func=cmd_generate)

    # 2. convert (CSV <-> JSON with automatic accent expansion)
    p_conv = sub.add_parser(
        "convert",
        help="Convert between CSV and JSON dictionaries while expanding unaccented variants.",
    )
    p_conv.add_argument("--input", required=True, help="Input dictionary (.csv or .json).")
    p_conv.add_argument("--output", required=True, help="Output dictionary (.csv or .json).")
    p_conv.set_defaults(func=cmd_convert)

    # 3. sync-controls
    p_sync = sub.add_parser(
        "sync-controls",
        help="Bulk-create/update Two-Way Synonym Controls in VAIS:C from CSV or JSON.",
    )
    p_sync.add_argument("--dictionary", required=True, help="Path to dictionary (.csv or .json).")
    p_sync.add_argument(
        "--apply",
        action="store_true",
        help="Execute live REST calls against retail.googleapis.com (default is dry-run).",
    )
    p_sync.add_argument(
        "--no-attach",
        action="store_true",
        help="Create/update controls without attaching them to the ServingConfig.",
    )
    p_sync.set_defaults(func=cmd_sync_controls)

    # 4. enrich-catalog
    p_enr = sub.add_parser(
        "enrich-catalog",
        help="Enrich product catalog feed with gender/accent ColorInfo & linguistic search attributes.",
    )
    p_enr.add_argument("--input", required=True, help="Input catalog (.jsonl or .json).")
    p_enr.add_argument("--output", required=True, help="Output enriched catalog (.jsonl).")
    p_enr.add_argument("--dictionary", required=True, help="Path to dictionary (.csv or .json).")
    p_enr.add_argument("--language", default="pt-PT", help="Default BCP-47 languageCode (default: pt-PT).")
    p_enr.set_defaults(func=cmd_enrich_catalog)

    # 5. diagnose
    p_diag = sub.add_parser(
        "diagnose",
        help="Run side-by-side A/B query comparison and capture attributionTokens.",
    )
    p_diag.add_argument("--query-a", required=True, help="First query, e.g. 'cadeira preto'.")
    p_diag.add_argument("--query-b", required=True, help="Second query, e.g. 'cadeira preta'.")
    p_diag.add_argument("--page-size", type=int, default=20, help="Number of results to compare.")
    p_diag.set_defaults(func=cmd_diagnose)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
