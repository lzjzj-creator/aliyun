# 知脉 RAG 问答 + 简历助手：产品功能细化与调研

> 调研日期：2026-11-14 ｜ 赛事：2026 Qoder 高校创造力大赛（天池 532536）｜ 赛道：StudyHack 学习科研

---

## 一、赛事约束解读（先做这件事）

| 约束 | 内容 | 对架构的影响 |
|---|---|---|
| 开发工具 | **必须使用 Qoder 完成项目** | 全程在 Qoder 里开发，保留过程记录，作品帖里要能体现 |
| 部署 | **必须部署到 Qoder Sites 并公开访问** | Qoder Sites 是静态托管 → 前端静态页放 Qoder Sites，**后端 API 必须独立托管**（可复用队友的 CloudBase `knowledge-rag` 服务，前端跨域调用即可） |
| 作品帖 | 11 月 30 日 18:00 前在天池赛题页论坛发作品帖 | 需要提前准备：产品介绍、截图、演示录屏、架构图 |
| 原创 | 作品须原创、规定时间内创作 | 参考开源项目可以，但代码要自己写，README 写清楚自己的设计 |
| 组队 | 1–3 人，提交份数不限，每团队限获一项奖 | 你们团队注意人数上限；可以迭代多次发帖 |

**时间线**：报名+发布作品截止 2026-11-30 18:00（**还剩约 16 天**）；初筛 12/1–12/7；决赛结果 12/15 前。

**现状提醒**：队友的 CloudBase 后端 `knowledge-rag-307709-12-1480606328.sh.run.tcloudbase.com` 今天访问返回 503（可能是冷启动或服务停用），动手前先确认服务还活着，健康检查 `/api/health` 要返回正常。

---

## 二、产品定位

**一句话**：面向大学生的"学习规划知识库问答 + AI 简历助手"双板块 Web 应用。

**差异化在哪**（评委视角）：
1. **有真实数据壁垒**——知脉知识库已有 1945 篇大学规划/AI 学习/求职笔记，不是空壳 demo；
2. **两个板块不是拼盘，而是同一场景闭环**：学生在知识库里问"怎么规划大三找实习"，转头就能用简历助手把简历改好；简历诊断时还能引用知识库里的求职方法论（这是别的参赛项目很难复制的联动点）；
3. **decision layer 是架构亮点**：一个入口，模型先判断意图再分发——这是真实的 multi-skill agent 模式，不是两个独立页面硬拼。

---

## 三、整体架构

```
                    ┌─────────────────────────────┐
                    │   Web 前端（Qoder Sites 静态托管）│
                    │  ┌──────────┐  ┌──────────┐ │
                    │  │ RAG问答板块 │  │ 简历助手板块 │ │
                    │  └────┬─────┘  └────┬─────┘ │
                    └───────┼─────────────┼───────┘
                            │   HTTPS     │
                    ┌───────▼─────────────▼───────┐
                    │   FastAPI 后端（CloudBase 云托管）│
                    │                              │
                    │  ┌────────────────────────┐  │
                    │  │ Decision Layer 意图路由   │  │
                    │  │ 规则快通道→语义路由→LLM分类 │  │
                    │  └───────┬─────────┬──────┘  │
                    │     RAG Pipeline   Resume Pipeline │
                    │  ┌───────▼──────┐ ┌▼─────────────┐│
                    │  │混合检索+来源  │ │解析→诊断→改写→导出││
                    │  └───────┬──────┘ └──────┬───────┘│
                    └──────────┼───────────┼────────┘
                BGE-M3 Embedding    百炼 Qwen（DeepSeek兜底）
                Supabase(pgvector)：chunks/文档元数据/会话
```

意图路由同时在**两个层级**生效：
- **入口级**：用户在问答板块输入"帮我改简历"→ 识别意图 → 引导/跳转到简历板块（或直接在对话里开启简历流程）；
- **板块内级**：问答板块内，判断该检索哪个知识库子集、该不该拒答；简历板块内，判断用户要"诊断"还是"改写"还是"针对某岗位定制"。

---

## 四、板块 A：RAG 知识库问答（功能细化）

大部分能力队友已经实现（混合检索、来源引用、流式输出、拒答、权限过滤），本板块重点是**移植到网页端 + 加意图路由 + 体验打磨**。

| 功能 | 细化 | 优先级 |
|---|---|---|
| 意图识别 | 见第五节 decision layer 设计 | P0 |
| 知识库问答 | 向量+关键词混合检索 → LLM 生成 → 返回答案+来源（文件名/页码/片段分） | P0（已有，复用） |
| 多知识库作用域 | 知识库按目录分域（大一/大二大三/大四/专业规划…），decision model 输出 `kb_scope` 缩小检索范围，提升准确率——**这是和普通 RAG 拉开差距的点** | P1 |
| 流式输出 | SSE，已有 `/api/public/chat/stream` | P0 |
| 来源展示 | 点击来源可预览原文片段；标注更新时间 | P1 |
| 无依据拒答 | 相似度低于阈值时明确说"知识库未收录"，并给出建议问法 | P0（已有逻辑，调阈值） |
| 追问/多轮 | 携带 history；指代消解（"那大二呢"→ 补全问题再检索） | P1 |
| 常见问题推荐 | 首屏给 6-8 个引导问题，降低空白恐惧 | P1 |
| 反馈 | 赞/踩 + 可选文字反馈，沉淀评测数据 | P2 |
| 简历导流 | 意图=简历类时返回引导卡片："这属于简历修改，是否前往简历助手？" | P0 |

---

## 五、Decision Layer（决策模型）设计——本项目的技术亮点

调研了当前主流的四种意图路由机制（参考：语义路由综述 arXiv:2502.00409、aurelio-labs/semantic-router、Anthropic "Building Effective Agents"）：

| 机制 | 延迟 | 成本 | 准确率 | 适用 |
|---|---|---|---|---|
| 规则/关键词 | ~0ms | 0 | 对明显意图高 | "改简历""上传简历"等强信号词 |
| Embedding 语义路由 | ~10-50ms | 仅一次 embedding | ~90%（有论文支撑） | 每个意图配 10-20 条示例语料 |
| LLM 分类（小模型） | ~200-500ms | 极低（用 turbo 级模型） | 处理模糊/复合意图最强 | 兜底 + 需要推理的情况 |
| 微调分类器 | 快 | 要训练数据 | 高但重 | 本项目不需要 |

### 推荐方案：三层级联路由

```
用户输入
  │
  ├─① 规则快通道：强关键词（"简历/CV/resume/上传文件"）或带附件 → 直接路由，0 成本
  │
  ├─② Embedding 语义路由：bge-m3（已有！）对 query 和各意图的示例语料算相似度
  │     ├─ 最高分 ≥ 阈值(如0.72) → 采用该意图
  │     └─ 前两名差距 < margin → 意图模糊，进入③
  │
  └─③ LLM 分类兜底：小模型（Qwen-turbo / deepseek-chat）输出 JSON：
        {"intent": "kb_qa|resume_edit|chitchat|out_of_scope",
         "kb_scope": "freshman|internship|resume|...|all",
         "confidence": 0-1, "clarify_question": "..."}
        confidence 低 → 反问澄清而不是瞎猜
```

**为什么这样设计（写进作品帖的卖点）**：
- 大部分请求在 ①② 层解决，**决策成本接近零**，比"每问都先调一次大模型"的方案快且便宜；
- ③层只在模糊时触发，且用小模型，不拖慢体验；
- 有明确的 `clarify` 分支，体现"拒答/澄清"的工程素养；
- `kb_scope` 输出让路由不止分板块，还能**收窄知识库检索域**。

**意图清单（初版，可按评测集扩充）**：
| intent | 说明 | 路由去向 |
|---|---|---|
| `kb_qa` | 知识库问答 | RAG pipeline，按 kb_scope 检索 |
| `resume_edit` | 简历诊断/改写/优化 | 简历板块流程 |
| `resume_qa` | 问简历方法论但不改简历（"简历要写照片吗"） | RAG pipeline（求职域知识库）→ 附简历板块引导卡 |
| `chitchat` | 闲聊 | 轻量回复，不带检索 |
| `out_of_scope` | 明显无关/敏感 | 礼貌拒答 |

**评测**：准备 50-80 条真实问句标注意图，统计路由准确率——这个数字本身就是作品帖素材（队友文档里的评测方法论直接适用）。

---

## 六、板块 B：简历助手（功能细化）

调研了 Resume-Matcher（29.3k★）、career-ops（82.5k★）、ai-job-search（71.7k★）、多个简历优化 skill 项目及 MIT 职业中心的 AI 简历指南，主流方法论收敛为：**解析 → 诊断打分 → STAR 改写 → JD 匹配 → 导出**。

### 6.1 功能清单

| # | 功能 | 细化 | 优先级 |
|---|---|---|---|
| 1 | 简历上传 | PDF/DOCX/MD/TXT 拖拽上传 + 纯文本粘贴；解析成结构化 JSON（教育/实习/项目/技能/奖项分段） | P0 |
| 2 | 诊断报告 | 分维度 anchored rubric 打分（每项 0-5）：结构完整性、量化程度(STAR覆盖率)、ATS 兼容性、动词强度、篇幅；输出雷达图+逐条问题清单 | P0 |
| 3 | STAR 改写 | 逐条 bullet 改写：Situation→Task→Action→Result，每条至少 1 个量化点、动词开头、≤3 行；**严禁虚构数据，缺数据时向用户反问采集** | P0 |
| 4 | 前后对比 | 左原文右改写，改动处高亮，逐条"接受/拒绝/微调" | P1 |
| 5 | JD 匹配 | 粘贴目标岗位 JD → 关键词覆盖率、匹配度评分、差距清单、定制版简历 | P1（差异化亮点） |
| 6 | 知识库联动 | 诊断报告里引用知识库中的简历/求职方法论条目（"据《大四核心信息差》第X篇…"）——**两板块的化学反应** | P1 |
| 7 | 导出 | Markdown 直接复制；**PDF 模板精排版导出（已确定做）**，参考 rendercv/reactive-resume | P0/P1 |
| 8 | 对话式微调 | "再简洁点""突出技术栈"多轮修改 | P2 |
| 9 | 隐私声明 | 简历含个人信息：明确提示数据用途、可一键删除、不用于训练 | P0（合规底线） |

**入口形态（已确定）**：独立「知识问答」「简历助手」两个页面；问答板块中 decision layer 识别到 `resume_edit` 意图时，返回引导卡片一键跳转简历板块。

### 6.2 关键原则（调研共识）

- **不虚构**：所有改写只能重组用户提供的真实信息；缺量化数据时用"约X%/X-Y倍"区间或反问。MIT 指南和各开源 skill 都把这条列为红线——写进 prompt 和评测集；
- **双产物**：每轮优化输出「改写稿 + 结构化报告」（分数、改动说明、待补充问题），报告比改写本身更能体现专业性；
- **ATS 兼容检查**：识别图片/双栏/特殊符号等解析杀手——正好和你们已有的文件解析能力复用。

### 6.3 简历板块接口草案

```
POST /api/resume/parse        上传→结构化JSON
POST /api/resume/diagnose     结构化JSON→诊断报告(分数+问题清单)
POST /api/resume/rewrite      指定条目→STAR改写（stream）
POST /api/resume/jd-match     简历+JD→匹配分析
POST /api/resume/export       结构化JSON→PDF/MD
```

---

## 七、技术选型建议

| 层 | 建议 | 理由 |
|---|---|---|
| 前端 | Vue3 + Vite 或 React + Vite，单页双 Tab | Qoder Sites 静态托管；队友已有网页前端可参考 |
| 后端 | 复用 FastAPI（Python 3.11）on CloudBase 云托管 | 队友已实现检索/权限/流式，新增 resume 路由即可，不重写 |
| LLM | **阿里云百炼 Qwen（已确定）**：decision 分类层 qwen-turbo，生成层 qwen-plus/max | 贴赛事主题（阿里系主办），百炼有学生额度；DeepSeek 留作 fallback |
| Embedding | 继续 BGE-M3（SiliconFlow）或百炼 text-embedding-v4 | 已有，别折腾 |
| 数据库/向量检索 | **Supabase（Postgres + pgvector，已确定数据在此）** | 向量检索直接走 pgvector `vector_cosine_ops` + SQL 过滤 `visibility='public' AND status='ready'`，不需要再引 Qdrant；文档/片段/会话元数据同库管理 |
| 简历 PDF 解析 | pypdf + python-docx（已有依赖）；扫描件 OCR 用 PaddleOCR | 复用现有 ingest 管线 |
| PDF 导出 | 前端排版 + `window.print` 或后端 rendercv 式 YAML→PDF | 先做前端打印，最省事 |

**复用 vs 新建**：强烈建议**在队友 FastAPI 代码上加 `/api/resume/*` 路由**，而不是新起服务——上传/解析/鉴权/限流全复用，你们只做前端新页面 + resume pipeline + decision layer。16 天时间紧，复用是唯一正确答案。

**Supabase 架构要点**（数据在 Supabase 后带来的变化）：

- 检索改为 Supabase 内完成：`embedding <=> query_vec` 余弦距离 + `WHERE visibility='public' AND status='ready' AND kb_scope=...` 一把梭，public 过滤逻辑从 SQLite 原样平移；
- 连接方式二选一：后端用 `supabase-py`/PostgREST（最省事），或直连 Postgres + `psycopg`+原生 pgvector SQL（混合排序更灵活，推荐这个，因为还要叠关键词匹配分）；
- **不要把 Supabase anon key 放前端直连数据库做问答**——检索可以，但 LLM key 和路由逻辑必须在后端；anon key 若用务必开 RLS，只允许读 `public+ready` 片段；
- 延迟注意：CloudBase（腾讯）→ Supabase 是跨云公网调用，单次问答会多 50-200ms，检索时一次性取够 Top-K 减少往返；若 Supabase 实例在新加坡而服务在国内，要实测 P95 延迟；
- 需要跟队友确认：chunks 表是否已存 embedding 向量（pgvector 列）还是只存文本？向量维度是否匹配 bge-m3（1024维）？这决定检索是走 pgvector 还是得在后端重算。

---

## 八、GitHub 参考项目（已按"近2月高星/高活跃 + 质量可靠"筛选）

### A. 场景最近的直接参考（重点看）

| 项目 | ★ | 说明 | 学什么 |
|---|---|---|---|
| [srbhr/Resume-Matcher](https://github.com/srbhr/Resume-Matcher) | 29.3k | 本地跑 AI 简历引擎：简历解析、JD 匹配、ATS 优化，Next.js+Python，支持 100+ LLM | **简历板块的完整流水线参考**，Apache-2.0 |
| [career-ops-hq/career-ops](https://github.com/career-ops-hq/career-ops) | 82.5k | 开源 AI 求职 agent：简历定制、ATS、求职信、面试准备，skill 化架构（4.3k commits，MIT） | 简历改写 prompt/skill 组织方式 |
| [ai-job-search-dev/ai-job-search](https://github.com/ai-job-search-dev/ai-job-search) | 71.7k | 本地求职框架：职位评估、CV 定制、求职信、面试准备 | 同上，偏 agent 流程编排 |
| [LingyiChen-AI/JadeAI](https://github.com/LingyiChen-AI/JadeAI) | 2.0k | AI 简历构建器：50+ 模板、PDF/图片解析、JD 匹配分析、多格式导出、一键 Docker | **和你们要做的最像的成品形态** |
| [LiuMengxuan04/shushu-internship-tool](https://github.com/LiuMengxuan04/shushu-internship-tool) | 2.2k | 中文项目："把岗位描述变项目，把项目变简历，把简历变面试" | JD→简历定制的中文产品思路 |
| [Hisn00w/ASu-skills](https://github.com/Hisn00w/ASu-skills) | 6.4k | 中文求职 AI skills 集：简历优化、投递、面试 | 中文简历优化方法论 |
| [bcefghj/ai-agent-interview-guide](https://github.com/bcefghj/ai-agent-interview-guide) | 3.8k | 中文面试攻略+简历模板+STAR 面试稿 | 简历内容方法论，可进你们知识库 |
| [BenyRonald77/uajy-academic-rag-chatbot](https://github.com/BenyRonald77/uajy-academic-rag-chatbot) | 453 | Streamlit+FAISS+Gemini 的大学学术手册 RAG，号称 production-grade | **和知脉几乎同场景**，看它的检索/评估怎么做 |

### B. 成熟大项目（看架构，别照抄）

| 项目 | ★ | 学什么 |
|---|---|---|
| [infiniflow/ragflow](https://github.com/infiniflow/ragflow) | 94k | RAG 引擎标杆：文档解析、chunking、引用溯源的工业级做法 |
| [langgenius/dify](https://github.com/langgenius/dify) | 161k | agentic workflow/RAG 编排，看它的"意图分类节点"怎么设计 |
| [aurelio-labs/semantic-router](https://github.com/aurelio-labs/semantic-router) | 4k | **decision layer 直接可用的库**，embedding 路由参考实现 |
| [reactive-resume/reactive-resume](https://github.com/reactive-resume/reactive-resume) | 45.7k | 简历编辑器 UI/模板渲染参考 |
| [rendercv/rendercv](https://github.com/rendercv/rendercv) | 18k | YAML→PDF 简历排版，导出功能参考 |
| [Zleap-AI/SAG](https://github.com/Zleap-AI/SAG) | 3k（1周内新项目） | 新检索架构，号称 RAG SOTA，GraphRAG 思路，有余力再看 |
| [Annyfee/agent-craft](https://github.com/Annyfee/agent-craft) | 535 | 中文 LangChain/RAG/LangGraph 实战教学仓 |

### C. 简历方法论参考（写 prompt 用）

- [Sumukhmg/resume-optimizer-claude-skill](https://github.com/Sumukhmg/resume-optimizer-claude-skill)：10 步简历优化流程 + anchored rubric + 双产物契约，直接借鉴；
- [wyh0626/resume-optimizer](https://github.com/wyh0626/resume-optimizer)、[agentenatalie/get-job.skill](https://github.com/agentenatalie/get-job.skill)、[shangsitongshizaitiantang/industry-resume-toolkit](https://github.com/shangsitongshizaitiantang/industry-resume-toolkit)：中文简历优化方法论；
- MIT CAPD《Using AI for your resume》：防虚构原则、JD 差距分析 prompt 范本。

> 注：career-ops / ai-job-search 这类 "Claude Code skills" 形态的仓库星数极高，但它们是 prompt/skill 集合而非可部署应用——**学它们的方法论和 prompt，不要指望直接跑起来**。

---

## 九、16 天里程碑（11/14 → 11/30）

| 时间 | 目标 |
|---|---|
| D1-D3 (11/14-16) | 确认后端存活；前端双板块骨架跑通；接通现有 `/api/public/chat/stream`；resume 解析接口 |
| D4-D7 (11/17-20) | decision layer 三层路由落地 + 意图评测集 50 条；简历诊断报告 v1（打分+问题清单） |
| D8-D11 (11/21-24) | STAR 改写 + 前后对比 + 导出；kb_scope 检索域收窄；JD 匹配 |
| D12-D14 (11/25-27) | 全流程打磨、错误处理、隐私声明；部署 Qoder Sites；录屏 |
| D15-D16 (11/28-30) | 写作品帖（含架构图、路由准确率、RAG 评测数字）；11/30 18:00 前发布 |

---

## 十、已敲定决策（2026-11-15）

| 决策点 | 结论 |
|---|---|
| LLM 供应商 | **阿里云百炼 Qwen**：分类层 qwen-turbo，生成层 qwen-plus/max；DeepSeek 留作 fallback |
| 简历板块入口 | **两个独立页面 + 智能引导**：问答中识别 resume_edit 意图 → 引导卡片一键跳转 |
| 简历导出 | **PDF 模板精排版**（参考 rendercv / reactive-resume）+ Markdown 复制 |
| 知识库边界 | **锁定现有 6 个目录**（1945 篇笔记），kb_scope 按目录收窄检索域，不开放用户上传 |
| 用户体系 | 游客免登录；简历内容仅存 session/本地，不做注册（省工时 + 规避隐私风险） |
