from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class Config:
    model: str = field(default_factory=lambda: os.getenv("DEEP_RESEARCH_MODEL", "llama3.1"))
    base_url: str = field(default_factory=lambda: os.getenv("OPENAI_BASE_URL", "http://localhost:11434/v1"))
    api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", "ollama"))
    max_iterations: int = field(default_factory=lambda: int(os.getenv("DEEP_RESEARCH_MAX_ITER", "20")))
    output_dir: str = field(default_factory=lambda: os.getenv("DEEP_RESEARCH_OUTPUT", "./output"))
    email: str = field(default_factory=lambda: os.getenv("DEEP_RESEARCH_EMAIL", ""))
    core_api_key: str = field(default_factory=lambda: os.getenv("CORE_API_KEY", ""))
