# 知脉 · 简历成长助手

> 2026 Qoder 高校创造力大赛 · StudyHack 学习科研赛道参赛作品
> 面向大学生求职的双板块工具：**知识库问答 + AI 简历助手**，由 Decision Layer 意图路由串联。
> 在线体验：https://zhimai-resume-uxvajmzewfe.qoder.zone

## 产品简介

- **知识问答**：基于知脉知识库（2790 篇大学规划/求职笔记、5576 个向量分段）的 RAG 问答，流式输出 + 真实来源引用 + 无依据拒答；
- **简历助手**：上传/粘贴简历 → 证据诊断 → STAR 改写（**不虚构事实**，缺数据反问）→ JD 岗位匹配；
- **Decision Layer**：L1 规则快通道 + L3 `decision-model-preview` 结构化路由，识别意图并引导跨板块跳转（L2 语义层预留二期）。

## 仓库结构

```
docs/                 方案文档（统一开发方案、调研、思维导图）
research/             调研过程资料（搜索缓存、思维导图渲染）
data/                 知识库原始数据（contentIndex.json，来自公开 Quartz 站）
vendor-resume-audit/  复用的简历分析引擎源码（详见下方说明）
backend/              FastAPI 服务（问答/路由/改写/管理），另有 Node 简历诊断服务
frontend/             Vue 3 + Vite 单页前端
ingest/               知识库镜像、入库与管理密码脚本
tests/                路由评测、RAG 端到端、管理鉴权测试
```

## 说明

- `vendor-resume-audit/` 为团队前成员开发的简历压力测试工具源码（公开仓库 [huangding-portfolio](https://github.com/230893812-a11y/huangding-portfolio)，上游未附 LICENSE，`package.json` 标注 ISC），本项目在原有基础上重新部署并扩展改写链路，作品帖中已如实标注新旧贡献边界。
- `data/contentIndex.json` 为公开知识库站点的静态内容索引，用于重建检索库。
- 简历内容不落库；所有模型密钥仅存服务端环境变量。

## 开发与验证

- 评测：`python tests/route_cases.py`、`python tests/rag_e2e.py`（默认打线上，可用 `--base` 覆盖）
- 鉴权单测：`python -m unittest tests.test_admin_auth -v`
- 评测数据与结论：`docs/评测报告.md`
- 部署：`docs/部署清单.md`；管理入口为线上站 `/#admin`
