"""
First-run security setup — run ONCE to lock the server down for production use.

    python setup.py

It will:
  1. Detect this machine's MAC (hardware lock).
  2. Generate a strong JWT secret.
  3. Hash an admin password (bcrypt).
  4. Write/refresh these into .env and flip DEV_MODE=false.

Skip this entirely for local development (DEV_MODE=true bypasses MAC + JWT).
"""
import os
import re
import secrets
import sys


def _detect_mac() -> str:
    import uuid
    raw = uuid.getnode()
    return ":".join(f"{(raw >> (5 - i) * 8) & 0xff:02x}" for i in range(6))


def _hash_pw(pw: str) -> str:
    import bcrypt
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def _set_env(text: str, key: str, value: str) -> str:
    line = f"{key}={value}"
    if re.search(rf"(?m)^{re.escape(key)}=.*$", text):
        return re.sub(rf"(?m)^{re.escape(key)}=.*$", line, text)
    return text.rstrip() + "\n" + line + "\n"


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    env_path = os.path.join(here, ".env")
    if not os.path.exists(env_path):
        if os.path.exists(env_path + ".example"):
            with open(env_path + ".example") as f:
                open(env_path, "w").write(f.read())
        else:
            open(env_path, "w").write("")

    with open(env_path, encoding="utf-8") as f:
        text = f.read()

    mac = _detect_mac()
    jwt_secret = secrets.token_hex(32)
    print(f"Detected MAC: {mac}")
    pw = input("Set admin password: ").strip()
    if not pw:
        print("Aborted — empty password.")
        sys.exit(1)
    ips = input("Allowed IPs (CSV, blank = 127.0.0.1, '*' = open): ").strip() or "127.0.0.1"

    text = _set_env(text, "OWNER_MAC", mac)
    text = _set_env(text, "JWT_SECRET", jwt_secret)
    text = _set_env(text, "ADMIN_PASSWORD_HASH", _hash_pw(pw))
    text = _set_env(text, "ALLOWED_IPS", ips)
    text = _set_env(text, "DEV_MODE", "false")

    with open(env_path, "w", encoding="utf-8") as f:
        f.write(text)

    print("\n✓ Security configured. DEV_MODE=false.")
    print("  Start:  uvicorn backend.main:app --host 0.0.0.0 --port 8010")
    print(f"  Admin login at /login (role=admin) from an allowed IP ({ips}).")


if __name__ == "__main__":
    main()
