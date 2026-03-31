from __future__ import annotations

from openai import OpenAI
from openai.types.chat import ChatCompletionMessage

from deep_researcher.config import Config


class LLMClient:
    def __init__(self, config: Config) -> None:
        self.client = OpenAI(base_url=config.base_url, api_key=config.api_key)
        self.model = config.model

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> ChatCompletionMessage:
        kwargs: dict = {
            "model": self.model,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        response = self.client.chat.completions.create(**kwargs)
        return response.choices[0].message
