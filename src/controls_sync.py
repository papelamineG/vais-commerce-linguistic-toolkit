"""Bulk Serving Controls Synchronizer for Vertex AI Search for Commerce.

Pushes Two-Way and One-Way Synonym Rule Groups from a curated JSON or CSV
dictionary into Vertex AI Search for Commerce (`retail.googleapis.com/v2`)
and attaches them to the target ServingConfig (`default_search`).

Solves the Merchandising Console UI limitation where synonym rules cannot be
bulk-imported from CSV files.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Sequence

from src.config import ToolkitConfig
from src.generator import LinguisticRuleGroup

RETAIL_API_BASE = "https://retail.googleapis.com/v2"
MAX_CONTROLS_PER_SERVING_CONFIG = 100
MAX_SYNONYMS_PER_CONTROL = 100


def build_control_rest_payload(
    group: LinguisticRuleGroup,
    catalog_parent: str | None = None,
) -> dict[str, Any]:
    """Builds a valid `google.cloud.retail.v2.Control` JSON payload.

    Follows the proto3 JSON mapping of `google/cloud/retail/v2/control.proto`
    and `google/cloud/retail/v2/common.proto` (`twowaySynonymsAction` /
    `onewaySynonymsAction`).
    """
    synonyms = [s.strip() for s in group.synonyms if s.strip()][:MAX_SYNONYMS_PER_CONTROL]
    if group.rule_type == "oneway":
        query_terms = [q.strip() for q in group.query_terms if q.strip()][:MAX_SYNONYMS_PER_CONTROL]
        rule_action: dict[str, Any] = {
            "onewaySynonymsAction": {
                "queryTerms": query_terms,
                "synonyms": [s for s in synonyms if s not in query_terms],
            }
        }
    else:
        if len(synonyms) < 2:
            raise ValueError(
                f"Control '{group.control_id}' requires at least 2 synonyms for TwowaySynonymsAction."
            )
        rule_action = {
            "twowaySynonymsAction": {
                "synonyms": synonyms,
            }
        }

    payload: dict[str, Any] = {
        "displayName": group.display_name[:128],
        "solutionTypes": ["SOLUTION_TYPE_SEARCH"],
        "searchSolutionUseCase": ["SEARCH_SOLUTION_USE_CASE_SEARCH"],
        "rule": {
            "condition": {},
            **rule_action,
        },
    }
    if catalog_parent:
        payload["name"] = f"{catalog_parent}/controls/{group.control_id}"
    return payload


def _get_access_token() -> str:
    """Retrieves an OAuth2 access token using Application Default Credentials."""
    import google.auth
    import google.auth.transport.requests

    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    auth_req = google.auth.transport.requests.Request()
    credentials.refresh(auth_req)
    return credentials.token


def _rest_request(
    method: str,
    url: str,
    token: str,
    project_id: str,
    body: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    """Executes an authenticated REST call against retail.googleapis.com/v2."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    if project_id:
        headers["x-goog-user-project"] = project_id

    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        try:
            err_json = json.loads(err_body)
        except Exception:
            err_json = {"raw_error": err_body}
        return exc.code, err_json


def sync_controls_to_vais(
    groups: Sequence[LinguisticRuleGroup],
    config: ToolkitConfig,
    dry_run: bool = True,
    attach_to_serving_config: bool = True,
) -> dict[str, Any]:
    """Creates or updates Synonym Controls in VAIS:C and links them to ServingConfig.

    Args:
        groups: Sequence of `LinguisticRuleGroup` objects to synchronize.
        config: `ToolkitConfig` with project & catalog identifiers.
        dry_run: If True, validates and outputs payloads without mutating GCP.
        attach_to_serving_config: If True, attaches each control to `config.serving_config_path`.

    Returns:
        Summary dictionary with created, updated, attached, and previewed controls.
    """
    summary: dict[str, Any] = {
        "dry_run": dry_run,
        "catalog_parent": config.catalog_parent,
        "serving_config": config.serving_config_path,
        "total_groups": len(groups),
        "quota_warning": (
            f"Exceeds default {MAX_CONTROLS_PER_SERVING_CONFIG} controls per serving config quota!"
            if len(groups) > MAX_CONTROLS_PER_SERVING_CONFIG
            else None
        ),
        "created": [],
        "updated": [],
        "attached": [],
        "errors": [],
        "sample_payloads": [],
    }

    for idx, group in enumerate(groups):
        payload = build_control_rest_payload(group)
        if idx < 3:
            summary["sample_payloads"].append(
                {
                    "control_id": group.control_id,
                    "endpoint": (
                        f"POST {RETAIL_API_BASE}/{config.catalog_parent}/controls"
                        f"?controlId={group.control_id}"
                    ),
                    "body": payload,
                }
            )

    if dry_run:
        return summary

    token = _get_access_token()
    for group in groups:
        create_url = (
            f"{RETAIL_API_BASE}/{config.catalog_parent}/controls?"
            + urllib.parse.urlencode({"controlId": group.control_id})
        )
        create_body = build_control_rest_payload(group)
        status, resp = _rest_request(
            "POST", create_url, token, config.project_id, create_body
        )

        if status in (200, 201):
            summary["created"].append(group.control_id)
        elif status == 409:
            # Control already exists -> PATCH (UpdateControl)
            patch_url = (
                f"{RETAIL_API_BASE}/{config.catalog_parent}/controls/{group.control_id}"
                "?updateMask=displayName,rule"
            )
            patch_body = build_control_rest_payload(
                group, catalog_parent=config.catalog_parent
            )
            p_status, p_resp = _rest_request(
                "PATCH", patch_url, token, config.project_id, patch_body
            )
            if p_status == 200:
                summary["updated"].append(group.control_id)
            else:
                summary["errors"].append(
                    {"control_id": group.control_id, "step": "patch", "status": p_status, "error": p_resp}
                )
                continue
        else:
            summary["errors"].append(
                {"control_id": group.control_id, "step": "create", "status": status, "error": resp}
            )
            continue

        if attach_to_serving_config:
            add_url = f"{RETAIL_API_BASE}/{config.serving_config_path}:addControl"
            add_body = {"controlId": group.control_id}
            a_status, a_resp = _rest_request(
                "POST", add_url, token, config.project_id, add_body
            )
            if a_status in (200, 409):
                # 409 ALREADY_EXISTS means control is already attached to servingConfig
                summary["attached"].append(group.control_id)
            else:
                summary["errors"].append(
                    {
                        "control_id": group.control_id,
                        "step": "addControl",
                        "status": a_status,
                        "error": a_resp,
                    }
                )

    return summary
