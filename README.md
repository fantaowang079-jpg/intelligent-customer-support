# 智能客服中台 · CustomerService Agent

> 基于 **LangGraph + FastAPI** 的多 Agent 客服系统：统一对话入口 → 意图路由 → 5 个业务 Agent，支持 RAG 问答、订单多轮查询、退换货人工审核与投诉升级，全链路 SSE 流式输出，内置三层容错兜底。

个人练习项目，从零搭建并持续迭代。代码可运行，克隆后按下方「快速开始」即可启动。

---

## 一、为什么做这个

企业客服的四类高频诉求——**FAQ 咨询、订单查询、退换货、投诉**——处理逻辑完全不同：

- FAQ 是**知识检索**问题（查得到、要能引用）
- 订单是**多轮对话**问题（缺订单号要追问，不能直接报错）
- 退换货是**流程审批**问题（涉及金额，模型不能自己拍板）
- 投诉是**风控**问题（识别到高危必须转人工）

用一个通用对话模型套所有场景，结果是：知识问答会编、订单查询会瞎猜、退换货敢直接答应、投诉识别不出来。

所以这里按场景拆成独立 Agent，每个 Agent 用适合它的范式，再由一个路由层统一分发。

---

## 二、系统架构

```
                     用户消息
                        │
            ┌───────────▼────────────┐
            │   意图路由（Orchestrator）│
            │  ① 规则前置：零 Token 拦截 │
            │  ② LLM 路由：语义分流      │
            │  ③ 高危词 / 紧急度判定      │
            └──┬──────┬──────┬──────┬──┘
               │      │      │      │
             FAQ    订单   退换货   投诉
               │      │      │      │
              RAG   状态机  HitL   HitL 转人工
               │      │      │      │
            ┌──▼──────▼──────▼──────▼──┐
            │      三层容错兜底          │
            │  重试 → Agent 降级 → 兜底  │
            └───────────┬──────────────┘
                        │
                    SSE 流式返回
```

---

## 三、五个 Agent 的设计

| Agent | 解决的问题 | 技术范式 | 关键实现 |
|---|---|---|---|
| **意图路由** | 无效调用多、分流不准 | 规则 + LLM 双段 | 问候语等高频无信息量输入由规则直接返回，不进模型；未命中再走 LLM 路由，叠加高危词与紧急度 |
| **FAQ 问答** | 政策类问题要准、要能溯源 | RAG | Milvus + BGE-M3 向量检索，BGE-Reranker 精排，答案强制附带引用来源；置信度低于阈值转人工 |
| **订单状态** | 用户只说「查订单」，没给单号 | 多轮状态机 | 槽位收集（订单号 / 手机号）→ 齐全后查询 → 播报；缺槽位时追问而非报错 |
| **退换货** | 涉及退款金额，不能让模型自己决定 | Human-in-the-loop | 规则先审，金额超阈值强制人工；`interrupt` 挂起并持久化，人工审批后 `resume` 执行 |
| **投诉升级** | 高危投诉漏判会酿成舆情 | 情绪识别 + HitL | 情绪与高危词判定，普通投诉走安抚话术，高危立即 `interrupt` 转人工坐席 |

---

## 四、几个值得说的设计点

### 1. 规则前置的零 Token 拦截

「你好」「谢谢」「再见」这类输入走大模型纯属浪费。路由层先做规则匹配，命中即返回预设话术，**不产生任何 token 消耗**；规则未命中才进入 LLM 路由。

### 2. 高风险操作的人机边界

退换货金额超过阈值、投诉命中高危词时，Agent 不直接执行，而是用 LangGraph 的 `interrupt` 挂起工作流：

```
申请 → 规则审核 → interrupt（挂起，状态持久化）
                          ↓
              人工在工作台审批（批准 / 驳回）
                          ↓
              resume → 执行后续业务动作
```

暂停态可查询、可审批、可恢复，审批记录可追溯——这类流程如果交给模型自由决策，出事时无法复盘。

### 3. 三层容错

| 层级 | 触发 | 处理 |
|---|---|---|
| 自动重试 | 网络抖动 / LLM 超时 | 间隔 1s / 3s，最多 2 次 |
| Agent 降级 | 重试仍失败 | 各 Agent 返回客服降级话术 |
| 系统兜底 | 降级也失败 | 友好提示，保证任何情况下都有响应 |

本地模型（BGE-M3 / Reranker）缺失时，FAQ 自动降级为关键词检索，服务仍可启动——**降级路径是实际验证过的，不是写在文档里的**。

### 4. 可插拔的数据层

订单与售后数据通过 `services/` 层抽象（当前为 Mock 实现）。对接真实业务系统时只替换这一层，Agent 逻辑与路由都不用动。

---

## 五、技术栈

| 层 | 选型 |
|---|---|
| 语言 / 框架 | Python 3.11、FastAPI |
| Agent 编排 | LangChain、LangGraph（StateGraph / interrupt / Checkpointer） |
| 检索 | Milvus、BGE-M3（稠密 + 稀疏）、BGE-Reranker |
| 数据校验 | Pydantic |
| 传输 | SSE 流式 |
| 前端 | Vue 3 + Vite |
| 其他 | Docker、Redis、PostgreSQL（预留） |

---

## 六、快速开始

```bash
# 1. 依赖（Python 3.11）
pip install -r requirements.txt

# 2. 配置
cp .env.local.example .env.local   # 填入 DEEPSEEK_API_KEY 等

# 3. 启动（项目根目录）
uvicorn backend.main:app --host 0.0.0.0 --port 8000

# 4. 前端
cd frontend && npm install && npm run dev
```

> 本地模型权重不随仓库分发，放在 `backend/models/`。缺失时 FAQ 自动降级为关键词检索，不影响启动与其余 Agent 使用。

---

## 七、接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/chat/stream` | 统一对话入口（SSE）：路由 + 分发 |
| POST | `/api/v1/return/apply` | 发起退换货申请 |
| GET | `/api/v1/return/pending` | 待审批申请列表 |
| GET | `/api/v1/return/{id}/review` | 审批详情（读取暂停态 State） |
| POST | `/api/v1/return/{id}/confirm` | 人工审批（approve / reject） |
| GET | `/api/v1/complaint/pending` | 待接管投诉列表 |
| POST | `/api/v1/complaint/{id}/resolve` | 人工坐席接管 |
| GET | `/health` | 健康检查 |

### 验证示例

```bash
# 零 Token 拦截（问候语，不调用模型）
curl -N -X POST localhost:8000/api/v1/chat/stream \
  -H 'Content-Type: application/json' -d '{"session_id":"s1","message":"你好"}'

# 订单多轮：先追问槽位，再给单号
curl -N -X POST localhost:8000/api/v1/chat/stream \
  -H 'Content-Type: application/json' -d '{"session_id":"s1","message":"查一下我的订单"}'
curl -N -X POST localhost:8000/api/v1/chat/stream \
  -H 'Content-Type: application/json' -d '{"session_id":"s1","message":"订单号88002 手机13800138000"}'

# 退换货人工审核（金额超阈值触发 interrupt）
curl -X POST localhost:8000/api/v1/return/apply \
  -H 'Content-Type: application/json' -d '{"order_no":"88003","reason":"不想要了"}'
```

---

## 八、目录结构

```
backend/
├── main.py                 # FastAPI 入口（lifespan 内预热模型）
├── config.py               # 配置中心（从 .env.local 读取，禁止硬编码）
├── dependencies.py         # 轻量身份识别（X-Customer-Id）
├── core/                   # 地基：llm_factory / memory / retry / orchestrator
│                           #       intent_router / knowledge_store / reranker
├── services/               # 可插拔数据层：order / after_sale / knowledge_seed（Mock）
├── agents/                 # faq / order / return_ / complaint
│                           # 每个 Agent 含 graph / nodes / state / prompts
└── api/v1/                 # unified_chat / return_ / complaint
frontend/                   # Vue3 对话页与坐席工作台
```

---

## 九、已知限制与后续优化

- **知识库规模有限**：当前为种子数据，尚未做全量政策文档的分块重建与评测集回归。
- **单实例部署**：Checkpointer 目前为内存实现，多 worker 会导致暂停态分裂，生产需换持久化后端并固定路由。
- **缺少自动化测试**：以接口验证为主，尚未补 Agent 分支的单元测试。
- **下一步**：补 RAGAS 离线评测、把暂停态落到 PostgreSQL、增加 Agent 级单测与回放能力。

---

## 声明

个人练习与技术验证项目，用于展示多 Agent 编排、RAG 与 Human-in-the-loop 的工程实现方式。
