from __future__ import annotations

import time

import httpx

from deep_researcher.models import ToolResult
from deep_researcher.tools.base import Tool

UNPAYWALL_BASE = "https://api.unpaywall.org/v2"

_RETRIABLE_STATUSES = {429, 500, 502, 503}


class OpenAccessTool(Tool):
    name = "find_open_access"
    category = "utility"
    description = (
        "Check if a paper has a free open access version available. Uses the Unpaywall "
        "database which tracks legal open access copies of papers. Provide a DOI to check. "
        "Returns the best available open access URL if one exists."
    )
    parameters = {
        "type": "object",
        "properties": {
            "doi": {
                "type": "string",
                "description": "The DOI of the paper to check for open access availability.",
            },
        },
        "required": ["doi"],
    }

    def __init__(self, email: str = "") -> None:
        self._email = email

    def execute(self, doi: str) -> ToolResult:
        email = self._email or "user@example.com"
        try:
            resp = None
            for attempt in range(3):
                resp = httpx.get(
                    f"{UNPAYWALL_BASE}/{doi}",
                    params={"email": email},
                    timeout=15,
                )
                if resp.status_code in _RETRIABLE_STATUSES:
                    time.sleep(2 ** (attempt + 1))
                    continue
                break
            if resp.status_code == 404:
                return ToolResult(text=f"No Unpaywall record found for DOI: {doi}")
            resp.raise_for_status()
        except httpx.HTTPError as e:
            return ToolResult(text=f"Error checking open access status: {e}")

        data = resp.json()
        is_oa = data.get("is_oa", False)
        best = data.get("best_oa_location")

        # Fallback to first oa_location if best_oa_location is None
        all_locations = data.get("oa_locations", [])
        if not best and all_locations:
            best = all_locations[0]

        if not is_oa or not best:
            return ToolResult(text=f"No open access version found for: {data.get('title', doi)}")

        parts = [f"Open access version found for: {data.get('title', doi)}"]
        url = best.get("url_for_pdf") or best.get("url")
        if url:
            parts.append(f"URL: {url}")
        host = best.get("host_type")
        if host:
            parts.append(f"Host: {host}")
        version = best.get("version")
        if version:
            parts.append(f"Version: {version}")
        license_val = best.get("license")
        if license_val:
            parts.append(f"License: {license_val}")

        if len(all_locations) > 1:
            parts.append(f"\n{len(all_locations)} open access copies found in total.")

        return ToolResult(text="\n".join(parts))
