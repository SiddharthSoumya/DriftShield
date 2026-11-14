import os

import uvicorn


def main():
    host = os.getenv("DRIFTSHIELD_HOST", "127.0.0.1")
    if host not in ("127.0.0.1", "::1") and len(os.getenv("DRIFTSHIELD_PASSWORD", "")) < 16:
        raise SystemExit("Set DRIFTSHIELD_PASSWORD to at least 16 characters for network access.")
    uvicorn.run("driftshield.app:from_environment", factory=True, host=host, port=int(os.getenv("PORT", "8000")))


if __name__ == "__main__":
    main()
