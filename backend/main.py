"""知脉 · 简历成长助手 —— FastAPI 主服务

接口：
  GET  /health                  健康检查
  POST /api/route               Decision Layer 意图判定
  POST /api/chat/stream         RAG 问答（SSE 流式）
  POST /api/resume/rewrite      STAR 安全改写（缺数据反问，不虚构）

运行：uvicorn main:app --port 8788
"""
import json
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import decision
import kb
from llm import llm, QWEN_CHAT

app = FastAPI(title="zhimai-api", version="0.1.0")

origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS",
           "http://localhost:5173,http://127.0.0.1:5173").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins,
                   allow_methods=["POST", "GET", "OPTIONS"],
                   allow_headers=["Content-Type", "X-File-Name"])

MIN_SCORE = float(os.getenv("MIN_RETRIEVE_SCORE", "0.25"))


class RouteReq(BaseModel):
    query: str
    has_attachment: bool = False


class ChatReq(BaseModel):
    query: str
    has_attachment: bool = False
    history: list[dict] = []


class RewriteReq(BaseModel):
    bullets: list[str]
    confirmed_facts: str = ""


@app.get("/health")
def health():
    return {"ok": True, "service": "zhimai-api"}


@app.post("/api/route")
def api_route(req: RouteReq):
    return decision.route(req.query, req.has_attachment)


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/api/chat/stream")
def chat_stream(req: ChatReq):
    def gen():
        r = decision.route(req.query, req.has_attachment)
        yield _sse("route", r)

        if r["action"] == "refuse":
            yield _sse("token", "这个问题超出了知脉知识库的范围，换个大学规划/求职相关的问题试试？")
            yield _sse("done", {}); return
        if r["action"] == "clarify":
            yield _sse("token", r["clarify_question"] or "能再说具体一点吗？")
            yield _sse("done", {}); return
        if r["action"] == "guide_resume":
            yield _sse("guide", {"text": "这属于简历修改，建议去「简历助手」板块处理。",
                                 "href": "/resume"})
            yield _sse("done", {}); return

        # answer 路径：检索 → 生成（resume_qa 强制 senior 域优先）
        scope = "senior" if r["intent"] == "resume_qa" and r["kb_scope"] == "all" else r["kb_scope"]
        try:
            qvec = llm.embed(req.query)
        except Exception:
            qvec = None
        hits = kb.search(qvec, req.query, scope)

        if not hits or hits[0].get("score", 0) < MIN_SCORE:
            yield _sse("token", "知识库里暂时没有能回答这个问题的资料。可以换个问法，或先问我职业规划、简历、实习相关的问题。")
            yield _sse("done", {}); return

        yield _sse("sources", [
            {"title": h.get("title"), "url": h.get("source_url"),
             "seq": h.get("seq"), "score": round(h.get("score", 0), 3)}
            for h in hits[:5]])

        context = "\n\n---\n\n".join(h["text"] for h in hits)
        messages = [
            {"role": "system", "content":
             "你是知脉知识库助手。只根据给定资料回答，资料不足就明说，不要编造政策、日期或结论。"
             "回答简洁分段，末尾不要自己列来源（前端会展示）。"},
            *[{"role": m.get("role", "user"), "content": m.get("content", "")}
              for m in req.history[-6:]],
            {"role": "user", "content": f"资料：\n{context}\n\n问题：{req.query}"},
        ]
        try:
            for tok in llm.chat_stream(messages, model=QWEN_CHAT):
                yield _sse("token", tok)
        except Exception as e:
            yield _sse("error", {"code": "MODEL_FAILED", "message": str(e)[:200]})
        yield _sse("done", {})

    return StreamingResponse(gen(), media_type="text/event-stream")


STAR_PROMPT = """你是简历改写助手。把下面的简历条目逐条改成 STAR 结构：
动词开头、突出行动与结果、每条尽量有量化点、不超过 3 行。
红线：
1. 不得新增用户没提供的事实、职责、技能或成果；
2. 缺量化数据时，在 suggested 里写【待补充：需要的数据】占位符，严禁编造具体数字；
3. 同时把需要用户确认的数据列入 questions 反问。
用户已确认的事实：{facts}
输出 JSON：{{"rewritten":[{{"original":"...","suggested":"...","needs_user_data":false}}],"questions":["..."]}}
只输出 JSON。"""


def _guard_fabricated_numbers(data: dict, originals: list[str], facts: str):
    """确定性守卫：建议文本里的数字必须能在原文/已确认事实中找到，
    找不到的强制 needs_user_data 并生成确认问题（不依赖模型自觉）。"""
    import re
    num_re = re.compile(r"\d+(?:\.\d+)?")
    allowed = set(num_re.findall(" ".join(originals) + " " + facts))
    questions = list(data.get("questions") or [])
    for item in data.get("rewritten") or []:
        suggested = item.get("suggested", "")
        novel = [n for n in dict.fromkeys(num_re.findall(suggested)) if n not in allowed]
        # 排除占位符内部的说明文字误伤：占位符【待补充：...】里的数字同样要求确认
        if novel:
            item["needs_user_data"] = True
            shown = "、".join(novel[:5])
            questions.append(
                f"建议条目「{item.get('original', '')[:20]}…」中出现了原文没有的数字（{shown}），"
                "是真实数据还是占位估计？请提供准确数字。")
    if questions:
        data["questions"] = questions
    return data


@app.post("/api/resume/rewrite")
def resume_rewrite(req: RewriteReq):
    if not req.bullets:
        return {"ok": False, "error": {"code": "BULLETS_REQUIRED", "message": "请先提供要改写的条目。"}}
    raw = llm.chat(
        [{"role": "system", "content": STAR_PROMPT.format(facts=req.confirmed_facts or "（无）")},
         {"role": "user", "content": "\n".join(f"{i+1}. {b}" for i, b in enumerate(req.bullets[:20]))}],
        model=QWEN_CHAT, json_mode=True, temperature=0.4)
    try:
        data = json.loads(raw)
    except Exception:
        return {"ok": False, "error": {"code": "MODEL_OUTPUT_INVALID", "message": "模型输出异常，请重试。"}}
    data = _guard_fabricated_numbers(data, req.bullets, req.confirmed_facts)
    return {"ok": True, "data": data}
