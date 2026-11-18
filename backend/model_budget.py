"""Conservative pre-call token reservations, shared across FC workers when enabled.

No refunds: retries, failed streams and disconnects retain their reservation.
This is a spend safety estimate, not a provider billing/usage report.
"""
import json
import os
import threading
import time

import importlib

ENABLED = (os.getenv("V2_MEMORY_ENABLED", "0") == "1"
           or os.getenv("COACH_ENABLED", "0") == "1")
SHARED = os.getenv("V2_SHARED_QUOTA", "0") == "1"
try:
    DAILY_LIMIT = int(os.getenv("COACH_DAILY_TOKEN_BUDGET", "200000"))
except ValueError:
    DAILY_LIMIT = 0  # Invalid deployment configuration must fail closed.
DEFAULT_OUTPUT = 4096
_lock = threading.Lock()
_day = {"date": "", "used": 0}


class BudgetUnavailable(RuntimeError):
    """Fixed public-safe error; callers must not retry another provider."""


def _rpc(name, payload):
    # Lazy import: v1 must not initialize storage; memory may itself use LLMs.
    try:
        memory = importlib.import_module(".memory", __package__) if __package__ else importlib.import_module("memory")
        return memory._json(memory._request("POST", "/rpc/" + name, json=payload))
    except Exception:
        raise BudgetUnavailable("模型预算暂不可用") from None


def bounded_output(model, output_tokens=None):
    # Only documented non-thinking text model contracts are admitted. Unknown
    # aliases (including reasoning models) need an explicit reviewed contract.
    if model not in {"qwen-plus", "qwen-turbo", "qwen-max", "deepseek-chat"}:
        raise BudgetUnavailable("预算模式暂不支持此模型")
    value = DEFAULT_OUTPUT if output_tokens is None else output_tokens
    if type(value) is not int or value < 1:
        raise BudgetUnavailable("模型预算暂不可用")
    return value


def reserve(messages, output_tokens=0):
    if not ENABLED:
        return
    if (type(output_tokens) is not int or output_tokens < 0
            or type(DAILY_LIMIT) is not int or DAILY_LIMIT < 1):
        raise BudgetUnavailable("模型预算暂不可用")
    # UTF-8 byte count is deliberately conservative for supported text models.
    # Include JSON serialization + framing margin; never infer token cost from
    # the final answer alone. Output is bounded by the matching API max_tokens.
    try:
        amount = len(json.dumps(messages, ensure_ascii=False, allow_nan=False).encode("utf-8")) + 1024 + output_tokens
    except (ValueError, TypeError, UnicodeError):
        raise BudgetUnavailable("模型预算暂不可用") from None
    if SHARED:
        out = _rpc("v2_charge_tokens", {"p_tokens": amount, "p_budget": DAILY_LIMIT})
        if (not isinstance(out, dict) or type(out.get("allowed")) is not bool
                or type(out.get("used")) is not int or not 0 <= out["used"] <= DAILY_LIMIT
                or type(out.get("budget")) is not int or out["budget"] != DAILY_LIMIT
                or (out["allowed"] and out["used"] < amount)):
            raise BudgetUnavailable("模型预算暂不可用")
        if not out["allowed"]:
            raise BudgetUnavailable("今日模型额度不足")
        return
    today = time.strftime("%Y-%m-%d")
    with _lock:
        if _day["date"] != today:
            _day.update(date=today, used=0)
        if _day["used"] + amount > DAILY_LIMIT:
            raise BudgetUnavailable("今日模型额度不足")
        _day["used"] += amount


def available():
    if not ENABLED:
        return True
    if type(DAILY_LIMIT) is not int or DAILY_LIMIT < 1:
        return False
    if SHARED:
        try:
            used = _rpc("v2_token_usage_today", {})
            return type(used) is int and 0 <= used < DAILY_LIMIT
        except BudgetUnavailable:
            return False
    with _lock:
        return _day["date"] != time.strftime("%Y-%m-%d") or _day["used"] < DAILY_LIMIT
