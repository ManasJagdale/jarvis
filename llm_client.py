"""
llm_client.py

This defines the *shape* every LLM provider must have. The agent loop in
main.py only ever talks to this interface — it never imports Gemini or
Claude directly. That's what makes swapping providers a one-file change:
write a new file that implements LLMClient, then change one import line
in main.py.

A LLMResponse is a plain, provider-agnostic result:
  - text: the model's natural-language reply (may be empty if it only
    wants to call a tool)
  - tool_calls: a list of {"name": str, "args": dict} the model wants
    executed. Empty list means the model is done and just answered.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    # Provider-specific object (e.g. Gemini's Content, carrying its internal
    # thought_signature) that must be replayed as-is on the next turn.
    # Opaque to main.py -- it just stores and passes it back unchanged.
    raw: Any = None


class LLMClient:
    """Abstract base class. Every provider client must implement generate()."""

    def generate(
        self,
        messages: list[dict[str, Any]],
        tool_schemas: list[dict[str, Any]],
        system_prompt: str = "",
    ) -> LLMResponse:
        """
        messages: the full conversation so far, in a simple provider-agnostic
                  format: [{"role": "user"|"assistant"|"tool", "content": str, ...}]
        tool_schemas: descriptions of the tools available (name, description,
                      parameters) so the model knows what it can call.
        system_prompt: standing instructions for the model's behaviour,
                        sent separately from the conversation history.

        Returns an LLMResponse.
        """
        raise NotImplementedError