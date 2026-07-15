"""
providers.py

Picks which LLM provider Jarvis talks to, based on config.ACTIVE_PROVIDER.
Both main.py and gui.py call get_llm_client() instead of importing a
provider class directly -- that's the actual swap point now: edit
config.ACTIVE_PROVIDER, not an import line in two different files.

Each provider module is only imported once it's actually selected, so
having, say, only a GEMINI_API_KEY set doesn't require the `openai`
package to even be installed if you're not using NVIDIA.
"""

from config import ACTIVE_PROVIDER


def get_llm_client():
    if ACTIVE_PROVIDER == "auto":
        from llm_fallback import FallbackLLMClient

        return FallbackLLMClient()
    if ACTIVE_PROVIDER == "gemini":
        from gemini_client import GeminiClient

        return GeminiClient()

    if ACTIVE_PROVIDER == "nvidia":
        from nvidia_client import NvidiaClient

        return NvidiaClient()

    raise RuntimeError(
        f"Unknown ACTIVE_PROVIDER '{ACTIVE_PROVIDER}' in config.py. "
        "Expected 'gemini', 'nvidia', or 'auto'."
    )
