# CustomerService Agent —— 智能客服中台

> 基于 **LangChain + LangGraph + FastAPI** 的多 Agent 智能客服系统，由 EduAgent 同构迁移而来。
> 五个客服 Agent + 统一意图路由 + SSE 流式 + 三层兜底，订单/售后数据走 **Mock 可插拔接口**。

## Agent 架构

```
               用户消息
                  │
        ┌─────────▼─────────┐
        │  意图路由（规则前置  │  零 Token 拦截 + LLM 路由 + 高危词/紧急度
        │  + LLM 路由）      │
        └───┬───┬───┬───┬──┘
        FAQ  订单 退换货 投诉
         │    │    │     │
        RAG  状态机 HitL   HitL转人工
```

| Agent | 能力 | 范式 | 来源 |
|-------|------|------|------|
| 意图路由 | 规则拦截 + LLM 路由 + 高危词/紧急度 | LLM 路由（8.4） | `core/intent_router.py` |
| FAQ 问答 | RAG 检索 + 引用来源 + 低置信度转人工 | RAG（第 5 章） | `agents/faq/` |
| 订单状态 | 多轮收集槽位 → 查订单 → 播报 + 补偿 | 状态机（第 7 章） | `agents/order/` |
| 退换货流程 | 规则审核 → interrupt 转人工 → resume 执行 | HitL（第 6 章） | `agents/return_/` |
| 投诉升级 | 情绪识别 → 安抚 / 高危 interrupt 转人工 | HitL 兜底（第 6 章） | `agents/complaint/` |

## 快速开始

```bash
# 1. 安装依赖（Python 3.11）
pip install -r requirements.txt

# 2. 启动（根目录执行）
uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

本地模型（BGE-M3 / Reranker）已放在 `backend/models/`；若缺失，FAQ 会自动降级为关键词检索，不影响启动。

## 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/chat/stream` | 统一入口（SSE）：路由 + 分发 |
| POST | `/api/v1/return/apply` | 发起退换货申请（HitL 后台） |
| GET  | `/api/v1/return/pending` | 待审批申请列表 |
| GET  | `/api/v1/return/{id}/review` | 审批详情（读暂停 State） |
| POST | `/api/v1/return/{id}/confirm` | 人工审批（approve/reject） |
| GET  | `/api/v1/complaint/pending` | 待接管投诉列表 |
| POST | `/api/v1/complaint/{id}/resolve` | 人工坐席接管 |
| GET  | `/health` | 健康检查 |

## 验证示例

```bash
# ① 零 Token 拦截
curl -N -X POST localhost:8000/api/v1/chat/stream \
  -H 'Content-Type: application/json' -d '{"session_id":"s1","message":"你好"}'

# ② 订单状态机（多轮：先追问，再给订单号）
curl -N -X POST localhost:8000/api/v1/chat/stream \
  -H 'Content-Type: application/json' -d '{"session_id":"s1","message":"查一下我的订单"}'
curl -N -X POST localhost:8000/api/v1/chat/stream \
  -H 'Content-Type: application/json' -d '{"session_id":"s1","message":"订单号88002 手机13800138000"}'

# ③ FAQ RAG（命中政策库） / 低置信度转人工
curl -N -X POST localhost:8000/api/v1/chat/stream \
  -H 'Content-Type: application/json' -d '{"session_id":"s2","message":"七天无理由退货要什么条件？"}'

# ④ 投诉（高危 → 转人工）
curl -N -X POST localhost:8000/api/v1/chat/stream \
  -H 'Content-Type: application/json' -d '{"session_id":"s3","message":"我要投诉，太差劲了"}'

# ⑤ 退换货 HitL（金额 >500 强制人工）
curl -X POST localhost:8000/api/v1/return/apply \
  -H 'Content-Type: application/json' -d '{"order_no":"88003","reason":"不想要了"}'
curl -X POST localhost:8000/api/v1/return/apply \
  -H 'Content-Type: application/json' -d '{"order_no":"88001","reason":"质量问题"}'
```

## 目录结构

```
backend/
├── main.py                 # FastAPI 入口（lifespan 模型预热）
├── config.py               # 配置中心（.env.local）
├── dependencies.py         # 轻量身份识别（X-Customer-Id 头）
├── core/                   # 地基：llm_factory / memory / retry / orchestrator / intent_router / knowledge_store / reranker
├── services/               # 可插拔：order_service / after_sale_service / knowledge_seed（Mock）
├── agents/                 # faq / order / return_ / complaint（各含 graph/nodes/state/prompts）
└── api/v1/                 # unified_chat / return_ / complaint
```

## 三层兜底

| 层级 | 触发条件 | 处理 |
|------|---------|------|
| 自动重试 | 网络抖动 / LLM 超时 | 1s / 3s 重试，最多 2 次 |
| Agent 降级 | 重试仍失败 | 各 Agent 返回客服降级话术 |
| 系统兜底 | 降级也失败 | 友好提示，保证有响应 |

## License

仅供教学 / 学习使用。
