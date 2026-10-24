"""Create private local settings once. Existing files are never overwritten."""
import os
import secrets

from backend.config import ROOT


def main():
    path = ROOT / ".env"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        print("Existing .env preserved.")
        return
    password = secrets.token_hex(24)
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        file.write("GEMINI_API_KEY=\nGEMINI_MODEL=gemini-3.1-flash-lite\nGEMINI_TIMEOUT_SECONDS=90\n"
                   f"DATABASE_URL=postgresql+psycopg://esse:{password}@127.0.0.1:5432/esse\n"
                   f"POSTGRES_PASSWORD={password}\n")
    print("Created ignored .env with random database credentials. Set GEMINI_API_KEY securely.")


if __name__ == "__main__":
    main()
