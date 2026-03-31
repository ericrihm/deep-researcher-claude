from __future__ import annotations

import json
import os
from dataclasses import dataclass


CONFIG_LOCATIONS = [
    os.path.expanduser("~/.deep-researcher/config.json"),
    "./deep-researcher.json",
]


def _load_config_file() -> dict:
    for path in CONFIG_LOCATIONS:
        if os.path.isfile(path):
            with open(path) as f:
                return json.load(f)
    return {}


def _get(file_cfg: dict, key: str, env_var: str, default: str) -> str:
    return os.getenv(env_var) or file_cfg.get(key) or default


@dataclass
class Config:
    model: str = ""
    base_url: str = ""
    api_key: str = ""
    max_iterations: int = 20
    output_dir: str = "./output"
    email: str = ""
    core_api_key: str = ""
    breadth: int = 3
    depth: int = 2
    timeout: int = 60

    def __post_init__(self) -> None:
        file_cfg = _load_config_file()

        if not self.model:
            self.model = _get(file_cfg, "model", "DEEP_RESEARCH_MODEL", "llama3.1")
        if not self.base_url:
            self.base_url = _get(file_cfg, "base_url", "OPENAI_BASE_URL", "http://localhost:11434/v1")
        if not self.api_key:
            self.api_key = _get(file_cfg, "api_key", "OPENAI_API_KEY", "ollama")
        if not self.email:
            self.email = _get(file_cfg, "email", "DEEP_RESEARCH_EMAIL", "")
        if not self.core_api_key:
            self.core_api_key = _get(file_cfg, "core_api_key", "CORE_API_KEY", "")

        iter_str = os.getenv("DEEP_RESEARCH_MAX_ITER") or str(file_cfg.get("max_iterations", ""))
        if iter_str:
            try:
                self.max_iterations = int(iter_str)
            except ValueError:
                pass

        output = os.getenv("DEEP_RESEARCH_OUTPUT") or file_cfg.get("output_dir") or ""
        if output:
            self.output_dir = output

        self.breadth = max(1, min(self.breadth, 5))
        self.depth = max(0, min(self.depth, 5))
        self.max_iterations = max(1, min(self.max_iterations, 50))
