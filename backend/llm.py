"""LLM/Embedding 调用层：百炼 DashScope（OpenAI 兼容）+ DeepSeek 兜底。"""
import json
import os
import re
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

try:
    from . import model_budget as budget
except ImportError:  # FC flat-module entry point
    import model_budget as budget

DASHSCOPE_KEY = os.getenv("DASHSCOPE_API_KEY", "")
DEEPSEEK_KEY = os.getenv("DEEPSEEK_API_KEY", "")
QWEN_CHAT = os.getenv("QWEN_MODEL_CHAT", "qwen-plus")
QWEN_ROUTER = os.getenv("QWEN_MODEL_ROUTER", "qwen-turbo")
EMBED_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-v4")

DS_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
EMBED_URL = "https://dashscope.aliyuncs.com/api/v1/services/embeddings/text-embedding/text-embedding"
DEEPSEEK_BASE = "https://api.deepseek.com/v1"


class LLM:
    def __init__(self):
        self._client = httpx.Client(timeout=httpx.Timeout(60, connect=10))

    def chat(self, messages, model=None, json_mode=False, temperature=0.3,
             max_tokens=None):
        """非流式对话，主用百炼；失败且配置了 DEEPSEEK_API_KEY 时兜底。"""
        try:
            return self._chat(DS_BASE, DASHSCOPE_KEY, model or QWEN_CHAT,
                              messages, json_mode, temperature, max_tokens)
        except budget.BudgetUnavailable:
            # A rejected reservation is not an upstream outage; never bypass
            # the shared admission gate by trying another paid provider.
            raise
        except Exception:
            if not DEEPSEEK_KEY:
                raise
            return self._chat(DEEPSEEK_BASE, DEEPSEEK_KEY, "deepseek-chat",
                              messages, json_mode, temperature, max_tokens)

    def chat_json(self, messages, model=None, retries=1, temperature=0.2,
                  max_tokens=None):
        """json_mode 输出 → dict。解析失败把坏输出回喂修复重试（默认 1 次）。"""
        raw = self.chat(messages, model=model, json_mode=True,
                        temperature=temperature, max_tokens=max_tokens)
        try:
            return _parse_json(raw)
        except ValueError:
            if retries <= 0:
                raise
        fix = messages + [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": "上面的输出不是合法 JSON，请只输出修正后的 JSON 本体，不要解释。"},
        ]
        return _parse_json(self.chat(fix, model=model, json_mode=True,
                                     temperature=0, max_tokens=max_tokens))

    def chat_stream(self, messages, model=None, max_tokens=None,
                    enable_thinking=False):
        """流式对话，逐行产出 content delta 文本（生成器）。
        enable_thinking 时模型的 reasoning_content 只在此处被丢弃——原始 CoT 不离开服务端。
        成功终态为 [DONE] 或 finish_reason=stop 后的正常 EOF；usage 空 choices
        帧不算终态。其它终止原因、坏帧、流内错误与缺失终态均报固定错误。
        """
        body = {"model": model or QWEN_CHAT, "messages": messages,
                "stream": True, "temperature": 0.5}
        if max_tokens:
            body["max_tokens"] = max_tokens
        if enable_thinking:
            if budget.ENABLED:
                # This provider's hidden reasoning allowance is not bounded by
                # our text max_tokens contract. Fail closed instead of claiming
                # the reservation covers unbounded paid reasoning.
                raise budget.BudgetUnavailable("预算模式暂不支持思考输出")
            body["enable_thinking"] = True
        if budget.ENABLED:
            bounded_output = budget.bounded_output(body["model"], max_tokens)
            budget.reserve(messages, bounded_output)
            body["max_tokens"] = bounded_output
        finished = False
        try:
            with self._client.stream(
                "POST", f"{DS_BASE}/chat/completions", json=body,
                headers={"Authorization": f"Bearer {DASHSCOPE_KEY}"},
            ) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    frame = json.loads(data)
                    if not isinstance(frame, dict) or "error" in frame:
                        raise ValueError
                    choices = frame.get("choices")
                    if not isinstance(choices, list):
                        raise ValueError
                    # OpenAI-compatible include_usage trailers have no choices.
                    if not choices and isinstance(frame.get("usage"), dict):
                        continue
                    if len(choices) != 1 or not isinstance(choices[0], dict):
                        raise ValueError
                    choice = choices[0]
                    reason = choice.get("finish_reason")
                    if reason is not None and reason != "stop":
                        raise ValueError
                    delta = choice.get("delta", {} if reason == "stop" else None)
                    if not isinstance(delta, dict):
                        raise ValueError
                    content = delta.get("content")
                    if content is not None and not isinstance(content, str):
                        raise ValueError
                    if finished and content:
                        raise ValueError
                    if reason == "stop":
                        finished = True
                    if content:
                        yield content
                if not finished:
                    raise ValueError
        except (httpx.HTTPError, ValueError, TypeError):
            # Never expose provider error text, request echoes or JSON fragments.
            raise RuntimeError("LLM stream failed") from None

    def decide(self, content: str, questions: dict, ticket_id: str = "q") -> dict:
        """decision-model-preview 结构化决策（systemone 专用端点）。
        返回 answers 字典：choice→{choice,confidence,probabilities}，noul→{noul}，score→{score,confidence}"""
        if budget.ENABLED:
            # systemone 无公开 max_tokens 契约，按五问结构化回答的安全上限固定预留；
            # 不预留会让 v1 路由在 v2 开关打开后整体降级为 kb_qa 兜底。
            budget.reserve({"content": content, "questions": questions}, 512)
        r = self._client.post(
            "https://cn-beijing.maas.qianwenaiapi.com/compatible-mode/v1/systemone",
            json={"model": "decision-model-preview",
                  "state": {"ticket_id": ticket_id, "content": content},
                  "questions": questions},
            headers={"Authorization": f"Bearer {DASHSCOPE_KEY}"},
            timeout=15,
        )
        r.raise_for_status()
        return r.json()["answers"]

    def embed(self, text: str, text_type: str = "query") -> list[float]:
        budget.reserve({"texts": [text], "text_type": text_type}, 0)
        r = self._client.post(
            EMBED_URL,
            json={"model": EMBED_MODEL,
                  "input": {"texts": [text]},
                  "parameters": {"text_type": text_type}},
            headers={"Authorization": f"Bearer {DASHSCOPE_KEY}"},
        )
        r.raise_for_status()
        return r.json()["output"]["embeddings"][0]["embedding"]

    @staticmethod
    def _chat(base, key, model, messages, json_mode, temperature, max_tokens=None):
        body = {"model": model, "messages": messages, "temperature": temperature}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        if max_tokens:
            body["max_tokens"] = max_tokens
        if budget.ENABLED:
            bounded_output = budget.bounded_output(body["model"], max_tokens)
            budget.reserve(messages, bounded_output)
            body["max_tokens"] = bounded_output
        r = httpx.post(f"{base}/chat/completions", json=body,
                       headers={"Authorization": f"Bearer {key}"}, timeout=60)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


def _parse_json(raw: str):
    """容错解析模型 JSON 输出：剥 ```json 围栏/前后杂文本。"""
    text = (raw or "").strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    if fence:
        text = fence.group(1)
    try:
        return json.loads(text)
    except ValueError:
        start = min((i for i in (text.find("{"), text.find("[")) if i >= 0),
                    default=-1)
        if start >= 0:
            return json.loads(text[start:])
        raise


llm = LLM()
