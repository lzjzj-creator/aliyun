# 知脉 RAG 问答 + 简历助手：开发方案

> 版本 v1.0 ｜ 2026-11-15 ｜ 赛事：2026 Qoder 高校创造力大赛 · StudyHack 学习科研赛道
> 截止：2026-11-30 18:00（天池赛题页论坛发作品帖）

---

## 1. 项目概述

双板块 Web 应用：

- **板块 A · 知识问答**：基于知脉知识库（Supabase 中 1945 篇大学规划/求职笔记）的 RAG 问答，带意图路由、来源引用、拒答能力；
- **板块 B · 简历助手**：上传简历 → 分维度诊断 → STAR 改写 → JD 匹配 → PDF 模板导出；
- **Decision Layer**：统一入口意图判断，串联两个板块（问答中识别简历意图 → 引导跳转）。

**交付物**：Qoder Sites 公开访问的网页 + 天池作品帖（含架构图、演示录屏、评测数据）。

---

## 2. 系统架构

```
Qoder Sites（静态托管）
  └─ Vite SPA（Vue3 或 React，双 Tab）
        │  HTTPS + CORS
        ▼
FastAPI on CloudBase（复用队友 knowledge-rag 服务）
  ├─ /api/public/chat/*        已有：问答（非流式+SSE流式）
  ├─ /api/route                新增：Decision Layer 意图路由
  ├─ /api/resume/*             新增：简历 pipeline
  │      │
  │      ├─ Decision Layer ──► L1规则 → L2语义路由(bge-m3) → L3 LLM分类(qwen-turbo)
  │      ├─ RAG Pipeline ────► Supabase pgvector 混合检索 → qwen-plus 生成+来源
  │      └─ Resume Pipeline ─► 解析→诊断→改写→JD匹配→导出
  ▼
Supabase Postgres + pgvector
  chunks（含 embedding 向量、visibility、status、kb_scope）
```

**目录结构（在队友后端基础上扩展）**：

```
backend/
  app.py                    # FastAPI 入口（已有）
  routers/
    public_chat.py          # 已有
    route.py                # 新增：POST /api/route 意图判定
    resume.py               # 新增：简历 5 接口
  services/
    retrieval.py            # 已有，改为 Supabase 查询
    decision/
      rules.py              # L1 规则
      semantic_router.py    # L2 bge-m3 语义路由
      llm_router.py         # L3 qwen-turbo JSON 分类
      intents.py            # 意图定义+示例语料
    resume/
      parser.py             # PDF/DOCX/MD → 结构化 JSON
      diagnose.py           # rubric 打分
      rewrite.py            # STAR 改写
      jd_match.py           # JD 匹配
      export_pdf.py         # PDF 模板渲染
  llm/
    qwen.py                 # 百炼 DashScope 客户端
    deepseek.py             # fallback（已有）
  db/
    supabase_client.py      # postgres 连接 + 检索 SQL
frontend/                   # 新建 Vite 工程
  src/
    pages/QaPage.vue        # 问答板块
    pages/ResumePage.vue    # 简历板块
    components/             # ChatStream, SourceCard, DiagReport,
                            # DiffView, RouteGuideCard, PdfExport...
```

---

## 3. 核心模块设计

### 3.1 Decision Layer（意图路由）

**输入**：`{query, has_attachment, page_context, history_tail}`
**输出**：

```json
{
  "intent": "kb_qa | resume_edit | resume_qa | chitchat | out_of_scope",
  "kb_scope": "freshman | sophomore_junior | senior | cases | major | video | all",
  "confidence": 0.0,
  "route_layer": "rule | semantic | llm",
  "clarify_question": null,
  "action": "answer | guide_resume | clarify | refuse"
}
```

**三级级联**：

| 层 | 触发 | 实现 | 退出条件 |
|---|---|---|---|
| L1 规则 | 所有请求 | 强关键词表：`简历/CV/resume/改简历/上传` 等；`has_attachment=true` → resume_edit | 命中即返回，cost=0 |
| L2 语义路由 | L1 未命中 | bge-m3 embed(query) vs 每意图 15-20 条示例语料质心，余弦相似度 | top1≥0.72 且 top1-top2≥0.08 → 采用 |
| L3 LLM 分类 | L2 不确定 | qwen-turbo，few-shot prompt，强制 JSON Schema 输出 | confidence<0.6 → `action=clarify` 反问 |

**意图定义**：

| intent | 判定 | 动作 |
|---|---|---|
| kb_qa | 知识库可答 | RAG pipeline，按 kb_scope 检索 |
| resume_edit | 要改/做简历 | 返回引导卡片 → 跳简历页 |
| resume_qa | 问简历方法论 | 走 RAG（senior 域优先）+ 尾部引导卡 |
| chitchat | 寒暄 | 轻量回复，不检索 |
| out_of_scope | 无关/敏感 | 固定话术拒答 |

**评测**：`eval/intents.jsonl` 50-80 条标注语料，`pytest eval/test_router.py` 统计分层命中率与总准确率（目标 ≥92%）。

### 3.2 RAG Pipeline（复用改造）

现有逻辑保留，改三点：

1. **检索换 Supabase**：
   ```sql
   SELECT id, source, page_no, text,
          1 - (embedding <=> %(qvec)::vector) AS vec_score,
          ts_rank(text_tsv, plainto_tsquery('simple', %(q)s)) AS kw_score
   FROM chunks
   WHERE visibility='public' AND status='ready'
     AND (%(scope)s = 'all' OR kb_scope = %(scope)s)
   ORDER BY (0.7*vec_score + 0.3*kw_score) DESC   -- 加权可用子查询
   LIMIT 8;
   ```
   无 `text_tsv` 列就用 `ILIKE` 退化方案，先跑通再优化。
2. **接入路由结果**：`kb_scope` 收窄检索域；`resume_edit` 短路返回引导卡。
3. **生成层换 qwen-plus**（DashScope OpenAI 兼容接口），保留 DeepSeek fallback：`LLM_PROVIDER=qwen` 环境变量切换。

### 3.3 Resume Pipeline（新增）

**简历结构化 JSON Schema**（parse 输出、后续所有环节共用）：

```json
{
  "basics": {"name": "", "phone": "", "email": "", "school": "", "major": "", "grad_year": ""},
  "education": [{"school":"","major":"","degree":"","period":"","highlights":[]}],
  "internships": [{"company":"","role":"","period":"","bullets":[""]}],
  "projects": [{"name":"","role":"","period":"","bullets":[""],"tech_stack":[]}],
  "skills": [""], "awards": [""], "raw_text": ""
}
```

**五个接口**：

| 接口 | 输入 → 输出 | 说明 |
|---|---|---|
| `POST /api/resume/parse` | file/text → ResumeJSON | pypdf/python-docx 提取 → qwen-plus 结构化抽取（JSON Schema 约束）→ 校验兜底 |
| `POST /api/resume/diagnose` | ResumeJSON → DiagReport | 5 维 anchored rubric 打分：结构完整性/STAR量化率/ATS兼容/动词强度/篇幅，各 0-5 + 逐条问题清单 |
| `POST /api/resume/rewrite` | ResumeJSON + section_ids → 改写结果（SSE） | 逐条 STAR 改写；**红线：不虚构数据**，缺量化→生成反问项 `questions[]` |
| `POST /api/resume/jd-match` | ResumeJSON + jd_text → MatchReport | 关键词覆盖率、硬性要求比对、差距清单、定制建议 |
| `POST /api/resume/export` | ResumeJSON + template → PDF | 服务端渲染（HTML 模板 → headless Chrome/weasyprint） |

**诊断报告结构**（前端雷达图渲染）：

```json
{"scores":{"structure":4,"quantification":2,"ats":3,"verbs":3,"length":4},
 "issues":[{"section":"internships[0].bullets[1]","type":"no_metric",
            "original":"负责用户增长","suggestion":"补充量化结果"}],
 "questions":["实习期间拉新数据大概是多少？"],
 "kb_refs":[{"doc":"大四核心信息差","note":"简历方法论参见该目录"}]}
```

**存储策略**：简历内容**不落库**，session 内存 + 前端 localStorage；日志只记 hash 和 token 数。页面顶部固定隐私声明条。

### 3.4 前端

| 页面 | 组件 |
|---|---|
| 问答页 | ChatStream（SSE 打字机）、SourceCard（来源/页码/片段）、RouteGuideCard（"前往简历助手"引导卡）、SuggestedQuestions、FeedbackButtons |
| 简历页 | UploadZone（拖拽+粘贴）、StructuredPreview（解析 JSON 可视化）、DiagReport（雷达图+问题清单）、DiffView（原文 vs 改写逐条 accept/reject）、JdInput、ExportBar |
| 联动 | 问答页识别 resume_edit → 弹 RouteGuideCard，点击跳 `/resume` 并带上上下文 |

技术栈：**Vue3 + Vite + Tailwind**（或沿用队友网页端已有栈，优先复用其样式体系）；雷达图用 ECharts；Markdown 渲染 marked + DOMPurify。

---

## 4. LLM 接入

| 用途 | 模型 | 理由 |
|---|---|---|
| 意图分类（L3） | qwen-turbo | 快、便宜、JSON 输出稳定 |
| 问答/简历生成 | qwen-plus | 质量足够，百炼学生额度 |
| 兜底 | deepseek-chat | 已接入，qwen 故障时切换 |
| Embedding | BGE-M3（SiliconFlow 已有） | 向量已入库，不动 |

百炼用 **OpenAI 兼容模式**接入（`https://dashscope.aliyuncs.com/compatible-mode/v1`），和现有 DeepSeek 客户端同一抽象层。

**环境变量新增**：`DASHSCOPE_API_KEY`、`QWEN_MODEL_CHAT=qwen-plus`、`QWEN_MODEL_ROUTER=qwen-turbo`、`SUPABASE_URL`、`SUPABASE_DB_URL`（或 `SUPABASE_SERVICE_KEY`）。

---

## 5. Prompt 设计要点（骨架）

**意图分类（L3）**：system 给意图定义 + 输出 JSON Schema + 各 3 条 few-shot；要求只输出 JSON；附 `clarify_question` 字段规则（两种意图都可能时才反问）。

**诊断打分**：给完整 anchored rubric（每维 0/2/4/5 分锚点描述），要求逐项打分并给出 `issues[]`（定位到具体 bullet）、`questions[]`（缺数据反问）、禁止改写成事实。

**STAR 改写**：逐条输入 bullet → 输出 `{rewritten, changes[], needs_user_data}`；规则：动词开头、≤3 行、≥1 量化点、**不得新增用户未提供的事实/数字**，缺数据标记 `needs_user_data` 并生成反问。

**JD 匹配**：抽取 JD 硬性要求（学历/技能/经验年限）与加分项 → 对照简历 → 输出覆盖率、差距、排序建议。

---

## 6. 评测方案（作品帖素材）

| 评测集 | 规模 | 指标 |
|---|---|---|
| 意图路由 | 50-80 条标注 | 总准确率（≥92%）、L1/L2 分流占比（展示成本优势） |
| RAG 问答 | 30-50 题（沿用队友方法） | Top-K 召回率、来源匹配率、无依据拒答率、P95 延迟 |
| 简历诊断 | 5-10 份真实简历人工核对 | 问题命中准确率、STAR 改写人工评分 |
| 权限安全 | 自动化测试（沿用队友 11 项） | private 片段不泄漏、伪造参数拦截 |

---

## 7. 部署

- **前端**：`vite build` → Qoder Sites 静态部署；环境变量只放后端 base URL；
- **后端**：CloudBase 云托管更新服务，容器内 `pip install` 新增依赖（`pgvector`、`psycopg[binary]`、PDF 渲染依赖）；
- **CORS**：后端放开 Qoder Sites 域名；
- **验证清单**：问答流式 ✓ 来源展示 ✓ 简历上传→诊断→改写→导出 PDF 全链路 ✓ 跨域 ✓ 重启后知识库仍在（Supabase 天然持久）✓

---

## 8. 里程碑（16 天）与分工

| 天 | 交付 |
|---|---|
| D1 | 后端健康确认；Supabase schema 摸清（chunks 表结构、向量列、维度）；百炼 key 申请 |
| D2-3 | 前端骨架双 Tab 上线；接通已有问答接口流式输出 |
| D4-5 | Decision Layer L1+L2 落地 + intents.jsonl 评测集；Supabase 混合检索替换 |
| D6-7 | L3 LLM 分类 + clarify；简历 parse + diagnose 接口 |
| D8-9 | 简历页：上传、解析预览、诊断报告雷达图 |
| D10-11 | rewrite + DiffView；export PDF 模板 |
| D12 | jd-match；问答→简历引导卡联动 |
| D13 | 打磨：错误处理、加载态、隐私声明、移动端适配 |
| D14 | 部署 Qoder Sites + 全链路回归 + 录屏 |
| D15 | 作品帖撰写（架构图、评测数据、demo 链接） |
| D16 | 11/30 18:00 前发布 + 预留缓冲 |

**分工建议（按 2-3 人）**：A 前端、B 后端 pipeline、C 评测+作品帖+PM（可兼）。

---

## 9. 风险与对策

| 风险 | 对策 |
|---|---|
| CloudBase 后端不稳定（昨日 503） | D1 第一件事确认；必要时本地起服务 + 内网穿透临时顶替 |
| CloudBase→Supabase 跨云延迟 | 单次检索取足 Top-K；P95 超 3s 则结果短缓存或迁移后端到近 Supabase 区域 |
| chunks 表无向量列 | 后端补算 embedding 走 bge-m3，或加列回填（1945 篇量小，回填可行） |
| qwen 额度/稳定性 | provider 抽象层一键切 DeepSeek |
| 简历隐私 | 不落库、session 级、隐私声明、一键清除 |
| PDF 导出踩坑 | 兜底方案：前端排版 + window.print 引导用户存 PDF |
| 时间超支 | 砍单顺序：对话式微调 → JD匹配 → 前后对比UI → PDF精排（保底 MD+打印） |
