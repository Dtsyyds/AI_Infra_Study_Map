# AI Infra Study Map

## 项目目标

本仓库用于记录 AI Infra / Agent Infra / LLM Serving / RAG 方向的系统学习与项目实践。

目标是在 6 个月内完成从 Agent 应用开发到 AI Infra 工程能力的过渡，最终形成可用于实习、校招或初级岗位投递的作品集。

## 当前阶段

- 身份：研二学生
- 目标岗位：AI Infra / Agent Infra / LLM Serving / RAG 平台 / LLMOps 相关岗位
- 每日学习时长：约 3 小时
- 当前主线：Agent 基础 + Python 编程能力 + LeetCode

## 学习主线

1. Agent 基础与框架实践
2. RAG / Memory / 上下文工程 / MCP
3. LLM Serving / vLLM / 推理性能指标
4. Docker / FastAPI / Kubernetes / Ray
5. LLMOps / 监控 / 评估 / Prompt 管理
6. 最终项目与面试准备

## 每日固定安排

| 时间 | 内容 | 目标 |
|---|---|---|
| 45 min | 理论学习 | 学习 PDF / 高阶资料，整理概念与方法 |
| 60 min | 项目实践 | 敲代码、改代码、跑 demo，把概念变成工程能力 |
| 45 min | LeetCode | 主刷力扣，重在独立写出 |
| 20 min | 复盘整理 | 记录卡点、总结收获、更新错题与笔记 |
| 10 min | GitHub 提交 | 至少一次 commit，保持持续输出 |

## 六个月路线

| 月份 | 学习重点 | 项目产出 |
|---|---|---|
| 第 1 月 | Python 基础、Agent Loop、ReAct、Reflection、LangGraph | 最小 Agent Loop |
| 第 2 月 | Memory、RAG、上下文工程、MCP、评估 | 文档问答 Agent |
| 第 3 月 | LLM Serving、Tokenizer、KV Cache、vLLM、TGI | LLM 推理服务与压测报告 |
| 第 4 月 | Linux、Docker、FastAPI、Kubernetes、Ray | Docker + 本地 K8s 部署 |
| 第 5 月 | LLMOps、MLflow、Prometheus、Grafana、评估 | RAG + Serving + 监控 mini 平台 |
| 第 6 月 | 简历项目、面试复习、系统总结 | 可投递作品集 |

## 项目目录

```text
docs/                       学习笔记与资料整理
leetcode/                   力扣刷题记录
month01_agent_loop/         最小 Agent Loop
month02_rag_agent/          文档问答 Agent
month03_llm_serving/        LLM Serving 与压测
month04_deploy/             Docker / K8s 部署
month05_llmops_platform/    LLMOps / 监控 / 评估
final_project/              最终作品集项目