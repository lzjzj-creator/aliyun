#!/usr/bin/env python3
"""Decision Layer 路由评测：50 条标注用例 → 混淆矩阵 + 分类指标。

用法：
  python tests/route_cases.py
  python tests/route_cases.py --base http://127.0.0.1:8788
  python tests/route_cases.py --json-out docs/route-eval.json --min-accuracy 0.7
"""
import argparse
import json
import sys
from datetime import datetime, timezone

import httpx

DEFAULT_BASE = "https://zhimai-api-hiwijfvopw.cn-beijing.fcapp.run"
INTENTS = ["kb_qa", "resume_edit", "resume_qa", "chitchat", "out_of_scope"]
INTENT_CN = {
    "kb_qa": "知识问答",
    "resume_edit": "改简历",
    "resume_qa": "简历咨询",
    "chitchat": "闲聊",
    "out_of_scope": "超范围",
}

CASES = [
    ("kb_qa", "大一应该重点补什么信息差？"),
    ("kb_qa", "大三找实习该怎么规划时间线？"),
    ("kb_qa", "大四秋招什么时候开始准备？"),
    ("kb_qa", "保研需要准备哪些材料？"),
    ("kb_qa", "出国留学怎么规划申请？"),
    ("kb_qa", "考公应该从什么时候开始准备？"),
    ("kb_qa", "怎么提高大学绩点？"),
    ("kb_qa", "转专业需要做哪些准备？"),
    ("kb_qa", "大学四年应该怎么规划？"),
    ("kb_qa", "没有实习经历怎么办？"),
    ("kb_qa", "怎么找第一份实习？"),
    ("kb_qa", "计算机专业以后能做什么工作？"),
    ("kb_qa", "参加哪些竞赛对求职有帮助？"),
    ("kb_qa", "如何弥补大学里的信息差？"),
    ("kb_qa", "大二应该确定什么方向？"),
    ("kb_qa", "秋招和春招有什么区别？"),
    ("kb_qa", "考研和就业应该怎么选？"),
    ("kb_qa", "大学期间要不要参加学生会？"),
    ("resume_edit", "帮我改一下简历，突出我的项目成果"),
    ("resume_edit", "我的简历太长了，帮我压缩到一页"),
    ("resume_edit", "把这段实习经历改写成STAR结构"),
    ("resume_edit", "这是我的简历文件，帮我优化", True),
    ("resume_qa", "简历上要不要放照片？"),
    ("resume_qa", "简历里的自我评价应该写多长？"),
    ("resume_qa", "简历用一页还是两页比较好？"),
    ("chitchat", "你好"),
    ("chitchat", "谢谢！"),
    ("chitchat", "在吗？"),
    ("out_of_scope", "今天天气怎么样？"),
    ("out_of_scope", "推荐一部好看的电影"),
    # 卡 #16 扩充：跨域知识问答
    ("kb_qa", "实习面试一般会问哪些问题？"),
    ("kb_qa", "大厂秋招笔试都考什么内容？"),
    ("kb_qa", "大学生要不要考教师资格证？"),
    ("kb_qa", "第一份实习没有工资值得去吗？"),
    ("kb_qa", "大创项目对保研有加分吗？"),
    ("kb_qa", "暑期实习和日常实习有什么区别？"),
    # 简历咨询边界：只问写法不改简历 → resume_qa
    ("resume_qa", "简历上要不要写期望薪资？"),
    ("resume_qa", "简历里的项目经历按什么时间顺序排？"),
    ("resume_qa", "简历照片用证件照还是生活照？"),
    ("resume_qa", "没有实习经历简历怎么写？"),
    ("resume_qa", "简历一页放不下怎么办？"),
    # 改简历边界：带着自己的简历诉求 → resume_edit
    ("resume_edit", "我把简历发给你，帮我润色一下"),
    ("resume_edit", "帮我把这段实习经历写得更有说服力"),
    ("resume_edit", "这是我的简历文件", True),
    ("resume_edit", "帮我把简历改成英文版"),
    # 闲聊与超范围
    ("chitchat", "晚安"),
    ("chitchat", "你真棒"),
    ("out_of_scope", "帮我写一段 Python 爬虫代码"),
    ("out_of_scope", "学校附近有什么好吃的？"),
    ("out_of_scope", "帮我写期末作业"),
]


def call_route(client: httpx.Client, base: str, query: str, attachment: bool) -> dict:
    r = client.post(f"{base}/api/route",
                    json={"query": query, "has_attachment": attachment})
    r.raise_for_status()
    return r.json()


def matrix(rows: list[dict]) -> dict[str, dict[str, int]]:
    table = {actual: {pred: 0 for pred in INTENTS} for actual in INTENTS}
    for row in rows:
        predicted = row["predicted"] if row["predicted"] in INTENTS else "out_of_scope"
        table[row["expected"]][predicted] += 1
    return table


def per_class(table: dict[str, dict[str, int]]) -> dict[str, dict[str, float]]:
    metrics = {}
    for label in INTENTS:
        tp = table[label][label]
        actual = sum(table[label].values())
        predicted = sum(table[row][label] for row in INTENTS)
        precision = tp / predicted if predicted else 0.0
        recall = tp / actual if actual else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        metrics[label] = {
            "support": actual, "precision": round(precision, 3),
            "recall": round(recall, 3), "f1": round(f1, 3),
        }
    return metrics


def print_report(rows: list[dict], table: dict, metrics: dict, base: str) -> None:
    correct = sum(1 for row in rows if row["ok"])
    print(f"路由评测 · {base}")
    print(f"用例 {len(rows)} · 正确 {correct} · 准确率 {correct / len(rows):.1%}\n")

    print("混淆矩阵（行=标注，列=预测）")
    head = "".join(f"{INTENT_CN[name]:>10}" for name in INTENTS)
    print(f"{'':>10}{head}{'合计':>8}")
    for actual in INTENTS:
        cells = "".join(f"{table[actual][pred]:>10}" for pred in INTENTS)
        print(f"{INTENT_CN[actual]:>10}{cells}{sum(table[actual].values()):>8}")
    print()

    print("分类指标")
    for label in INTENTS:
        m = metrics[label]
        print(f"{INTENT_CN[label]:<10} support={m['support']:<3} "
              f"P={m['precision']:.2f} R={m['recall']:.2f} F1={m['f1']:.2f}")

    misses = [row for row in rows if not row["ok"]]
    if misses:
        print("\n错判明细")
        for row in misses:
            print(f"  标注 {INTENT_CN[row['expected']]} → 预测 {INTENT_CN.get(row['predicted'], row['predicted'])}"
                  f" | {row['query']} | action={row['route'].get('action')} "
                  f"conf={row['route'].get('confidence')}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--json-out", default="")
    parser.add_argument("--min-accuracy", type=float, default=0.0)
    args = parser.parse_args()
    base = args.base.rstrip("/")

    rows = []
    with httpx.Client(trust_env=False, timeout=args.timeout) as client:
        for case in CASES:
            expected, query = case[0], case[1]
            attachment = bool(case[2]) if len(case) > 2 else False
            try:
                route = call_route(client, base, query, attachment)
            except httpx.HTTPError as e:
                print(f"请求失败：{query} → {e}", file=sys.stderr)
                return 2
            predicted = route.get("intent", "")
            rows.append({
                "query": query,
                "has_attachment": attachment,
                "expected": expected,
                "predicted": predicted,
                "ok": predicted == expected,
                "route": route,
            })

    table = matrix(rows)
    metrics = per_class(table)
    print_report(rows, table, metrics, base)

    correct = sum(1 for row in rows if row["ok"])
    accuracy = correct / len(rows)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base": base,
        "total": len(rows),
        "correct": correct,
        "accuracy": round(accuracy, 4),
        "confusion_matrix": table,
        "per_class": metrics,
        "cases": rows,
    }
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\nJSON 报告：{args.json_out}")

    if accuracy < args.min_accuracy:
        print(f"\n准确率 {accuracy:.1%} 低于阈值 {args.min_accuracy:.1%}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())