"""
main.py

Terminal entry point for Jarvis. All agent-loop logic lives in
jarvis_core.py so it's shared with gui.py -- this file is just the
terminal-shaped wrapper around it (read a line, run a turn, print the
answer, repeat).

Which LLM provider gets used is decided by config.ACTIVE_PROVIDER, not
here -- see providers.py.

Run it with:
    python main.py

For the desktop window instead, run gui.py (see gui.py / README.md).
"""

from confirm import confirm_action
from jarvis_core import run_turn
from providers import get_llm_client


def main():
    print("Jarvis is running. Type 'exit' to quit.\n")

    llm = get_llm_client()
    messages: list[dict] = []

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            break

        if user_input.lower() in {"exit", "quit"}:
            print("Exiting.")
            break
        if not user_input:
            continue

        messages.append({"role": "user", "content": user_input})

        try:
            answer = run_turn(llm, messages, confirm_action)
        except Exception as exc:  # noqa: BLE001
            # Never let a raw stack trace hit the terminal.
            print(f"Jarvis: Something went wrong on my end: {exc}\n")
            continue

        print(f"Jarvis: {answer}\n")


if __name__ == "__main__":
    main()
