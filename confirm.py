"""
confirm.py

The confirmation gate. Any tool call flagged as destructive (see
DESTRUCTIVE_TOOLS in main.py) must pass through confirm_action() before
it's allowed to run.

Requires the user to type the exact word "yes" -- pressing Enter, or
typing "y", does not count. This is deliberate: a throwaway keystroke
should never be enough to move or overwrite a real file.
"""


def confirm_action(description: str) -> bool:
    print(f"\n⚠️  Jarvis wants to do the following:\n  {description}")
    response = input("Type 'yes' to confirm, anything else to cancel: ").strip()
    return response.lower() == "yes"
