#!/usr/bin/env python3
"""生成管理员密码与签名密钥，写入本地 .env（不上传仓库）。

用法：
  python ingest/make_admin_password.py                 # 首次生成随机密码
  python ingest/make_admin_password.py --password 自定义 # 设置指定密码
  python ingest/make_admin_password.py --rotate         # 换密码并让旧会话全部失效

脚本只输出写入位置，不在终端回显密码。
"""
import argparse
import re
import secrets
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from backend.admin_auth import hash_password  # noqa: E402

ENV_FILE = ROOT / ".env"


def read_env() -> str:
    return ENV_FILE.read_text(encoding="utf-8") if ENV_FILE.exists() else ""


def get_value(text: str, key: str) -> str:
    m = re.search(rf"^{key}=(.*)$", text, flags=re.M)
    if not m:
        return ""
    value = m.group(1).strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1]
    return value


def upsert(text: str, key: str, value: str) -> str:
    # 必须加引号：PBKDF2 哈希里的 $ 会被 shell source 当成变量展开
    line = f"{key}={shlex.quote(value)}"
    if re.search(rf"^{key}=.*$", text, flags=re.M):
        return re.sub(rf"^{key}=.*$", lambda _: line, text, count=1, flags=re.M)
    if text and not text.endswith("\n"):
        text += "\n"
    return text + line + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--password", help="指定管理密码；省略则保留已有密码或随机生成")
    parser.add_argument("--rotate", action="store_true", help="更换密码并重置会话密钥")
    args = parser.parse_args()

    if args.password and ("\n" in args.password or "\r" in args.password):
        raise SystemExit("密码不能包含换行")

    env = read_env()
    password = args.password or get_value(env, "ADMIN_PASSWORD")
    if args.rotate or not password:
        password = args.password or secrets.token_urlsafe(15)

    secret = get_value(env, "ADMIN_SESSION_SECRET")
    if args.rotate or len(secret) < 32:
        secret = secrets.token_urlsafe(48)

    env = upsert(env, "ADMIN_PASSWORD", password)
    env = upsert(env, "ADMIN_PASSWORD_HASH", hash_password(password))
    env = upsert(env, "ADMIN_SESSION_SECRET", secret)
    env = upsert(env, "ADMIN_SESSION_TTL", "7200")
    ENV_FILE.write_text(env, encoding="utf-8")
    print(f"管理员凭据已写入 {ENV_FILE}")
    print("密码在 ADMIN_PASSWORD 一行；线上只使用 ADMIN_PASSWORD_HASH 与 ADMIN_SESSION_SECRET。")


if __name__ == "__main__":
    main()