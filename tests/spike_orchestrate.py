"""#8 编排实测 spike（真实 DashScope，非 mock）。

测三件事：
  A. qwen-turbo + json_mode 跑 orchestrator：5 条固定用户输入 ×1 轮，
     统计 JSON 合法率、act 分布、单次延迟（含修复重试）
  B. qwen-plus enable_thinking：同一深问 prompt 流式，记录首 token 延迟 / 总时延 /
     是否出现 reasoning_content（服务端丢弃，不透出）
  C. extractor JSON：1 条富文本输入，统计合法率与字段齐全度

运行：python tests/spike_orchestrate.py     （需要 .env 的 DASHSCOPE_API_KEY）
输出：控制台表格；把结果抄进 docs/spike-编排.md
"""
import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("SUPABASE_URL", "https://supabase.invalid")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "x")

from backend import coach
from backend.llm import llm, QWEN_CHAT, QWEN_ROUTER

USER_TURNS = [
    "我是大一新生，在杭电读计算机，还没什么经历",
    "实习怎么找啊，完全没头绪",
    "做过一个二手书小程序，我负责后端",
    "嗯嗯",
    "差不多就这样吧，帮我弄简历",
]


def _bench(fn, n=1):
    lats = []
    out = None
    for _ in range(n):
        t0 = time.perf_counter()
        out = fn()
        lats.append(time.perf_counter() - t0)
    return out, lats


def spike_orchestrate():
    print(f"== A. orchestrator ({QWEN_ROUTER} + json_mode) ==")
    profile = {"identity": {"grade": "大一", "school": "杭电", "major": "计算机"}}
    detail = {"filled": {"identity.grade": {}, "identity.school": {}, "identity.major": {}}}
    for text in USER_TURNS:
        t0 = time.perf_counter()
        plan = coach._orchestrate(text, profile, detail, [])
        ms = (time.perf_counter() - t0) * 1000
        tag = "规则兜底" if plan.get("degraded") else "模型"
        print(f"  {ms:7.0f}ms  [{tag}] act={plan['act']} focus={plan.get('focus_slot')} "
              f"need_kb={plan.get('need_kb')} deep={plan.get('deep')} | {text[:16]}")


def spike_thinking():
    print(f"\n== B. {QWEN_CHAT} enable_thinking 对比（流式）==")
    msgs = [{"role": "system", "content":
             "你是求职教练，针对用户的项目经历做一次 STAR 深挖追问，150字内。"},
            {"role": "user", "content": "我做过一个二手书小程序，负责后端"}]
    for flag in (False, True):
        t0 = time.perf_counter()
        first = None
        body = ""
        for tok in llm.chat_stream(msgs, model=QWEN_CHAT, enable_thinking=flag,
                                   max_tokens=400):
            if first is None:
                first = time.perf_counter() - t0
            body += tok
        total = time.perf_counter() - t0
        print(f"  thinking={flag}: 首token {first*1000:6.0f}ms 总 {total*1000:6.0f}ms "
              f"len={len(body)}")


def spike_extract():
    print(f"\n== C. extractor ({QWEN_ROUTER} + json_mode) ==")
    text = "我大二，在浙大读软件工程，做过二手书小程序，负责后端，用 Java 和 MySQL，日活涨了30%"
    t0 = time.perf_counter()
    p, d, u, _raw = coach._extract(text, {}, {}, [{"role": "user", "content": text}])
    ms = (time.perf_counter() - t0) * 1000
    print(f"  {ms:7.0f}ms  updates={u}")
    print(f"  profile={json.dumps(p, ensure_ascii=False)[:300]}")


if __name__ == "__main__":
    if not os.getenv("DASHSCOPE_API_KEY"):
        sys.exit("缺少 DASHSCOPE_API_KEY，无法实测")
    spike_orchestrate()
    spike_thinking()
    spike_extract()
