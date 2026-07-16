"""
hash_password.py

Run this ONCE to turn your chosen password into a bcrypt hash, then set
that hash as an environment variable. The plaintext password itself is
never stored anywhere -- only this hash is, and a hash can't be reversed
back into the password.

Usage (PowerShell, from D:\\Projects\\Jarvis):
    python hash_password.py
    (it will prompt you to type a password -- won't echo it to the screen)
    setx JARVIS_PASSWORD_HASH "<paste the printed hash here, in quotes>"

Then close and reopen your terminal (and restart the server) for it to
take effect. If you ever want to change your password, just run this
again and setx the new hash -- it fully replaces the old one.
"""

import getpass

import bcrypt


def main():
    password = getpass.getpass("Choose a password for remote Jarvis access: ")
    confirm = getpass.getpass("Type it again to confirm: ")

    if password != confirm:
        print("Passwords didn't match -- nothing was generated. Try again.")
        return

    if not password:
        print("Password can't be empty -- nothing was generated. Try again.")
        return

    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
    print("\nYour hash -- copy this ENTIRE line, exactly as printed:\n")
    # Single quotes are deliberate here, not a typo: bcrypt hashes contain
    # $ characters (e.g. $2b$12$...), and PowerShell expands $ inside
    # double-quoted strings as a variable reference, silently corrupting
    # the hash. Single-quoted strings in PowerShell are literal -- no
    # expansion -- so they're the only safe way to pass this value.
    print(f"setx JARVIS_PASSWORD_HASH '{hashed.decode('utf-8')}'")
    print(
        "\nRun that command in PowerShell EXACTLY as printed, with the "
        "single quotes (not double quotes -- PowerShell corrupts the hash "
        "if you swap them, since bcrypt hashes contain $ characters). "
        "Then close and reopen your terminal for it to take effect."
    )


if __name__ == "__main__":
    main()
