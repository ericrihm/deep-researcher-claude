from __future__ import annotations

import json
from typing import Any


class Tool:
    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = {}

    def execute(self, **kwargs: Any) -> str:
        raise NotImplementedError

    def to_openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def schemas(self) -> list[dict[str, Any]]:
        return [t.to_openai_schema() for t in self._tools.values()]

    def execute(self, name: str, arguments: str) -> str:
        tool = self._tools.get(name)
        if not tool:
            return f"Error: Unknown tool '{name}'"
        try:
            kwargs = json.loads(arguments) if arguments else {}
            return tool.execute(**kwargs)
        except json.JSONDecodeError:
            return f"Error: Invalid JSON arguments for tool '{name}'"
        except Exception as e:
            return f"Error executing {name}: {e}"
