"""Centralized configuration loaded from environment variables.

Never hardcode model IDs or cloud locations in application code; configure
`GEMINI_MODEL`, `GOOGLE_CLOUD_LOCATION`, and `GOOGLE_CLOUD_PROJECT` in `.env`
or environment variables.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


@dataclass(frozen=True)
class ToolkitConfig:
    """Runtime configuration for Gemini and Vertex AI Search for Commerce."""

    project_id: str
    project_number: str
    gemini_model: str
    gemini_location: str
    vais_location: str
    vais_catalog: str
    vais_branch: str
    vais_serving_config: str
    default_language_code: str

    @classmethod
    def from_env(cls, require_gemini: bool = False, require_gcp: bool = False) -> "ToolkitConfig":
        """Loads configuration from environment variables."""
        project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
        project_number = os.environ.get("GOOGLE_CLOUD_PROJECT_NUMBER", project_id).strip()
        gemini_model = os.environ.get("GEMINI_MODEL", "").strip()
        gemini_location = os.environ.get("GOOGLE_CLOUD_LOCATION", "global").strip()
        vais_location = os.environ.get("VAIS_LOCATION", "global").strip()
        vais_catalog = os.environ.get("VAIS_CATALOG", "default_catalog").strip()
        vais_branch = os.environ.get("VAIS_BRANCH", "0").strip()
        vais_serving_config = os.environ.get("VAIS_SERVING_CONFIG", "default_search").strip()
        default_language_code = os.environ.get("VAIS_DEFAULT_LANGUAGE_CODE", "pt-PT").strip()

        if require_gemini and not gemini_model:
            raise ValueError(
                "Environment variable GEMINI_MODEL is required for GenAI generation. "
                "Please set GEMINI_MODEL in your .env file (see .env.example)."
            )
        if require_gcp and not project_number:
            raise ValueError(
                "Environment variable GOOGLE_CLOUD_PROJECT or GOOGLE_CLOUD_PROJECT_NUMBER "
                "is required for Retail API calls. Please set it in your .env file."
            )

        return cls(
            project_id=project_id,
            project_number=project_number,
            gemini_model=gemini_model,
            gemini_location=gemini_location,
            vais_location=vais_location,
            vais_catalog=vais_catalog,
            vais_branch=vais_branch,
            vais_serving_config=vais_serving_config,
            default_language_code=default_language_code,
        )

    @property
    def catalog_parent(self) -> str:
        """Full resource name of the VAIS:C catalog."""
        return (
            f"projects/{self.project_number}/locations/{self.vais_location}"
            f"/catalogs/{self.vais_catalog}"
        )

    @property
    def branch_parent(self) -> str:
        """Full resource name of the VAIS:C catalog branch."""
        return f"{self.catalog_parent}/branches/{self.vais_branch}"

    @property
    def serving_config_path(self) -> str:
        """Full resource name of the VAIS:C serving config."""
        return f"{self.catalog_parent}/servingConfigs/{self.vais_serving_config}"
