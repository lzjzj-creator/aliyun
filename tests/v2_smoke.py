#!/usr/bin/env python3
"""v2 上线冒烟与延迟测量（打真实部署；--turns>0 会产生模型费用）。

用法：
  python tests/v2_smoke.py --base https://<fc-host>                       # 零模型开销的契约冒烟
  python tests/v2_smoke.py --base https://<fc-host> --turns 2             # 2 轮真实访谈（计费）
  python tests/v2_smoke.py --base https://<fc-host> --sample 30 --turns 1 # p95 口径（≥30 样本）

默认只跑安全项：路由是否挂载、归属校验、一次性令牌、幂等重发、错误脱敏，
以及响应/SSE 事件里是否泄漏 `session_token_hash` 或上游密钥。
第 2 步用「新建会话 + 空消息」触发问候轮，不调模型、不计费；真正花钱的只有 --turns。
本脚本不打印用户消息或模型原文，只打印状态码、事件名、耗时与计数。
"""
import argparse
import json
import statistics
import sys
import threading
import time
import uuid

import httpx

FORBIDDEN_KEYS = {"session_token_hash", "service_key", "apikey", "service_role_key", "anon_key"}
SENSITIVE_MARKERS = ("session_token_hash", "service_role", "eyJhbGciOi")
PROBE = "我在学校做过一个二手书交易小程序，我负责后端接口和数据库。"


def scan(payload, where, findings):
    """递归检查禁止外泄的键名，以及字符串值里意外出现的上游标识。"""
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key.lower() in FORBIDDEN_KEYS:
                findings.append(f"{where}: 出现禁止字段 {key}")
            scan(value, f"{where}.{key}", findings)
    elif isinstance(payload, list):
        for index, value in enumerate(payload[:30]):
            scan(value, f"{where}[{index}]", findings)
    elif isinstance(payload, str):
        for marker in SENSITIVE_MARKERS:
            if marker in payload:
                findings.append(f"{where}: 值里含 {marker}")


def read_stream(response, findings):
    """按 §4 协议解析 `event:` + `data:` 帧；返回事件摘要与计时。"""
    started = time.perf_counter()
    names, session, done, error = [], None, False, False
    first_step = first_token = None
    pending = None
    for line in response.iter_lines():
        if not line:
            continue
        if line.startswith("event:"):
            pending = line[6:].strip()
            continue
        if not line.startswith("data:"):
            continue
        try:
            data = json.loads(line[5:].strip())
        except ValueError:
            findings.append("stream: data 帧不是合法 JSON")
            continue
        name = pending or "?"
        names.append(name)
        now = time.perf_counter() - started
        if name == "session":
            session = data
        if name in ("step_start", "step_end") and first_step is None:
            first_step = now
        if name in ("reasoning_summary", "token") and first_token is None:
            first_token = now
        done = done or name == "done"
        error = error or name == "error"
        scan(data, f"sse.{name}", findings)
    return {"names": names, "session": session or {}, "done": done, "error": error,
            "first_step": first_step, "first_token": first_token,
            "total": time.perf_counter() - started}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True, help="FC 基址，例如 https://zhimai-api-xxx.cn-beijing.fcapp.run")
    parser.add_argument("--turns", type=int, default=0, help="真实用户消息轮数（计费），默认 0")
    parser.add_argument("--sample", type=int, default=1, help="重复次数；p95 需要 ≥30")
    parser.add_argument("--message", default=PROBE, help="--turns 时提交的用户消息")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--json-out", default="", help="把结果写成 JSON 文件")
    args = parser.parse_args()

    anon = str(uuid.uuid4())
    findings, checks = [], []
    timings = {"首 step": [], "首 token": [], "整轮": []}
    sid = token = ""

    def record(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": str(detail)[:160]})
        print(f"  {'✅' if ok else '❌'} {name}{('：' + str(detail)[:160]) if detail else ''}")

    def stream(client, payload, headers=None):
        with client.stream("POST", "/api/v2/chat/stream",
                           headers=headers or {"X-Anon-Id": anon}, json=payload) as response:
            if response.status_code != 200:
                return {"status": response.status_code}
            result = read_stream(response, findings)
            result["status"] = 200
            return result

    with httpx.Client(base_url=args.base.rstrip("/"), timeout=args.timeout) as client:
        health = client.get("/health")
        record("GET /health", health.status_code == 200, f"HTTP {health.status_code}")

        # 1)+2) 新建会话 + 问候轮（不调模型、不计费）；顺带确认 v2 路由是否挂载
        greeting = stream(client, {"session_id": None, "client_msg_id": str(uuid.uuid4()), "message": ""})
        if greeting.get("status") == 404:
            record("v2 路由已挂载", False, "404：V2_MEMORY_ENABLED=0 或未部署 v2 分支")
            return finish(checks, timings, findings, args)
        record("v2 路由已挂载", True, "HTTP 200/503")
        if greeting.get("status") == 503:
            record("访谈引擎已开启", False, "503：COACH_ENABLED=0，跳过访谈相关检查")
        else:
            record("访谈引擎已开启", greeting.get("status") == 200, f"HTTP {greeting.get('status')}")
            sid = greeting["session"].get("session_id", "")
            token = greeting["session"].get("session_token", "")
            record("session 事件一次性下发令牌", bool(sid) and bool(token), f"session_id={sid[:8]}…")
            record("问候轮以 done 终结", greeting["done"] and not greeting["error"],
                   greeting["names"][-4:])
        owned = {"X-Anon-Id": anon, "X-Session-Token": token}

        # 3) 归属与读回
        listed = client.get("/api/v2/sessions", headers={"X-Anon-Id": anon})
        record("本人可列出会话", listed.status_code == 200, f"HTTP {listed.status_code}")
        if listed.status_code == 200:
            scan(listed.json(), "list_sessions", findings)
            rows = listed.json()
            record("列表项不含令牌哈希", isinstance(rows, list) and all(
                "session_token_hash" not in row for row in rows if isinstance(row, dict)))
            record("列表含刚建的会话", any(isinstance(r, dict) and r.get("id") == sid for r in rows))

        bundle = client.get(f"/api/v2/sessions/{sid}", headers={"X-Anon-Id": anon}) if sid else None
        record("缺令牌读回被拒", bundle is not None and bundle.status_code == 404,
               f"HTTP {bundle.status_code}" if bundle is not None else "跳过")
        if sid:
            for label, headers in (("伪造令牌被拒", {"X-Anon-Id": anon, "X-Session-Token": "forged" + "0" * 24}),
                                   ("他人身份被拒", {"X-Anon-Id": str(uuid.uuid4()), "X-Session-Token": token or "x" * 32})):
                probe = client.get(f"/api/v2/sessions/{sid}", headers=headers)
                record(label, probe.status_code == 404, f"HTTP {probe.status_code}")
            bogus = client.get("/api/v2/sessions/not-a-uuid", headers=owned)
            record("非法会话 ID 不返工数据", bogus.status_code == 404, f"HTTP {bogus.status_code}")
            bad_anon = client.get("/api/v2/sessions", headers={"X-Anon-Id": "not-a-uuid"})
            record("非法匿名 ID 被拒", bad_anon.status_code == 404, f"HTTP {bad_anon.status_code}")

        # 4) 幂等与错误体脱敏（v1 过渡落库对）
        if sid and token:
            key = str(uuid.uuid4())
            pair = {"session_id": sid, "client_msg_id": key, "user": "冒烟：第一次", "assistant": "冒烟回复"}
            first = client.post("/api/v2/messages", headers=owned, json=pair)
            again = client.post("/api/v2/messages", headers=owned, json=pair)
            same = (first.status_code == again.status_code == 200
                    and first.json().get("message_ids") == again.json().get("message_ids"))
            record("同幂等键重发返回原消息", same,
                   f"{first.status_code}/{again.status_code}")
            drift = dict(pair, user="冒烟：改内容重发")
            conflict = client.post("/api/v2/messages", headers=owned, json=drift)
            body = conflict.text if conflict.status_code == 409 else ""
            record("幂等键改内容 → 409", conflict.status_code == 409, f"HTTP {conflict.status_code}")
            record("409 响应不含上游原文", "session_token_hash" not in body and "supabase" not in body.lower())

            # 5) 单会话流互斥：两路并发只能有一路进入
            stop = threading.Barrier(2)

            def racer():
                stop.wait(timeout=30)
                return stream(client, {"session_id": sid, "client_msg_id": str(uuid.uuid4()),
                                       "message": args.message})

            outcomes = []
            threads = [threading.Thread(target=lambda: outcomes.append(racer())) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=args.timeout + 10)
            statuses = sorted(o.get("status") for o in outcomes)
            record("并发双流被互斥收敛", statuses in ([200, 429], [429, 429]) or statuses.count(200) <= 1, statuses)

            # 6) 真实访谈轮 + 计时
            for sample in range(max(1, args.sample) if args.turns else 0):
                for _ in range(args.turns):
                    result = stream(client, {"session_id": sid, "client_msg_id": str(uuid.uuid4()),
                                             "message": args.message}, headers=owned)
                    if result.get("status") != 200:
                        record(f"访谈轮 HTTP {result.get('status')}", False)
                        break
                    for name, value in (("首 step", result["first_step"]),
                                        ("首 token", result["first_token"]), ("整轮", result["total"])):
                        if value is not None:
                            timings[name].append(value)
                    record("访谈轮以 done 终结", result["done"] and not result["error"], result["names"][-4:])
                    time.sleep(1)   # 别把 IP 分钟桶当被测目标打爆

    record("响应与事件流无令牌哈希/上游密钥", not findings, f"{len(findings)} 处异常")
    for item in findings[:10]:
        print(f"     · {item}")
    return finish(checks, timings, findings, args)


def finish(checks, timings, findings, args):
    print("\n== 延迟（开发计划 §13：p95 需 ≥30 样本）==")
    summary = {}
    for name, values in timings.items():
        if not values:
            continue
        ordered = sorted(values)
        entry = {"n": len(values), "median": round(statistics.median(values), 2),
                 "max": round(max(values), 2), "p95": None}
        if len(values) >= 30:
            entry["p95"] = round(ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))], 2)
        else:
            entry["note"] = f"样本 {len(values)} < 30，p95 不成立，不能对外报数"
        summary[name] = entry
        print(f"  ·  {name}: {entry}")
    if not any(timings.values()):
        print("  ·  未跑 --turns：无延迟样本，§13 延迟行仍属未测量")

    failed = [c for c in checks if not c["ok"]]
    print(f"\n结论：{len(checks) - len(failed)}/{len(checks)} 通过")
    for item in failed:
        print(f"     ✗ {item['name']}（{item['detail']}）")
    if args.json_out:
        payload = {"base": args.base, "checks": checks, "timings": summary,
                   "leaks": findings, "ran_at": int(time.time())}
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        print(f"明细写入 {args.json_out}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
