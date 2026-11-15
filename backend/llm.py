"""LLM/Embedding 调用层：百炼 DashScope（OpenAI 兼容）+ DeepSeek 兜底。"""
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

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

    def chat(self, messages, model=None, json_mode=False, temperature=0.3):
        """非流式对话，主用百炼；失败且配置了 DEEPSEEK_API_KEY 时兜底。"""
        try:
            return self._chat(DS_BASE, DASHSCOPE_KEY, model or QWEN_CHAT,
                              messages, json_mode, temperature)
        except Exception:
            if not DEEPSEEK_KEY:
                raise
            return self._chat(DEEPSEEK_BASE, DEEPSEEK_KEY, "deepseek-chat",
                              messages, json_mode, temperature)

    def chat_stream(self, messages, model=None):
        """流式对话，逐行产出 SSE delta 文本（生成器）。"""
        body = {"model": model or QWEN_CHAT, "messages": messages,
                "stream": True, "temperature": 0.5}
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
                import json as _json
                try:
                    delta = _json.loads(data)["choices"][0].get("delta", {})
                except Exception:
                    continue
                if delta.get("content"):
                    yield delta["content"]

    def decide(self, content: str, questions: dict, ticket_id: str = "q") -> dict:
        """decision-model-preview 结构化决策（systemone 专用端点）。
        返回 answers 字典：choice→{choice,confidence,probabilities}，noul→{noul}，score→{score,confidence}"""
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
    def _chat(base, key, model, messages, json_mode, temperature):
        body = {"model": model, "messages": messages, "temperature": temperature}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        r = httpx.post(f"{base}/chat/completions", json=body,
                       headers={"Authorization": f"Bearer {key}"}, timeout=60)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


llm = LLM()
