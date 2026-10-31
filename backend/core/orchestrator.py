# backend/core/orchestrator.py
# Orchestrator：Agent 编排服务。职责：意图路由 → 单 Agent 直达 / 串联 Pipeline。

from __future__ import annotations

from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field
from langchain_core.messages import HumanMessage

from backend.core.logger import get_logger
from backend.core.retry import with_retry
from backend.core.exceptions import (
    AgentExecutionError,
    PipelineError,
    IntentRouteError,
)

logger = get_logger(__name__)


class ExecutionMode(str, Enum):
    SINGLE   = "single"
    PIPELINE = "pipeline"
    CLARIFY  = "clarify"


class AgentType(str, Enum):
    """客服 Agent 类型标识。"""
    FAQ       = "faq"        # FAQ 问答（RAG）
    ORDER     = "order"      # 订单状态（状态机）
    RETURN    = "return"     # 退换货流程（HitL）
    COMPLAINT = "complaint"  # 投诉升级（HitL）


class AgentRequest(BaseModel):
    """所有 Agent 请求的统一入参 Schema。"""
    customer_id:   str = Field(..., description="客户 ID")
    tenant_id:     str = Field(default="tenant_default", description="租户 ID")
    session_id:    str = Field(..., description="会话 ID，用于 thread_id 拼接")
    agent_type:    AgentType = Field(..., description="目标 Agent 类型")
    user_message:  str = Field(..., description="用户输入的原始文本")
    context:       dict[str, Any] = Field(default_factory=dict, description="附加上下文")
    pipeline_mode: bool = Field(default=False, description="是否强制走串联 Pipeline")

    @property
    def thread_id(self) -> str:
        return f"customer_{self.customer_id}_session_{self.session_id}"


class AgentResponse(BaseModel):
    """所有 Agent 响应的统一出参 Schema。"""
    success:       bool = Field(..., description="执行是否成功")
    agent_type:    AgentType = Field(..., description="实际执行的 Agent 类型")
    content:       str = Field(default="", description="主要文本响应内容")
    structured:    Optional[dict[str, Any]] = Field(default=None, description="结构化数据")
    fallback_used: bool = Field(default=False, description="是否触发了降级")
    error_msg:     Optional[str] = Field(default=None, description="失败时的错误信息")
    metadata:      dict[str, Any] = Field(default_factory=dict, description="附加元数据")


class PipelineResult(BaseModel):
    steps:    list[AgentResponse] = Field(default_factory=list)
    combined: dict[str, Any] = Field(default_factory=dict)


class Orchestrator:
    """
    Agent 编排服务。
    职责：
        - 单 Agent 模式：直接调用对应 Agent 的 LangGraph 图
        - 串联 Pipeline 模式：按顺序调用多个 Agent，前序输出注入后序输入
    所有 Agent 图懒加载，避免启动时全量加载。
    """

    def __init__(self):
        self._agent_graphs: dict[AgentType, Any] = {}
        self._pipelines: dict[str, list[AgentType]] = {
            # 退换货全链路：先 FAQ 确认政策，再发起退换货流程
            "return_flow": [AgentType.FAQ, AgentType.RETURN],
        }
        logger.info("orchestrator.initialized")

    def _get_agent_graph(self, agent_type: AgentType) -> Any:
        """懒加载 Agent 的 LangGraph 编译图。"""
        if agent_type not in self._agent_graphs:
            if agent_type == AgentType.FAQ:
                from backend.agents.faq.graph import build_faq_graph
                self._agent_graphs[agent_type] = build_faq_graph()
            elif agent_type == AgentType.ORDER:
                from backend.agents.order.graph import build_order_graph
                self._agent_graphs[agent_type] = build_order_graph()
            elif agent_type == AgentType.RETURN:
                from backend.agents.return_.graph import build_return_graph
                self._agent_graphs[agent_type] = build_return_graph()
            elif agent_type == AgentType.COMPLAINT:
                from backend.agents.complaint.graph import build_complaint_graph
                self._agent_graphs[agent_type] = build_complaint_graph()
            else:
                raise ValueError(f"未知 AgentType: {agent_type}")
            logger.info("orchestrator.agent_graph_loaded", agent_type=agent_type.value)
        return self._agent_graphs[agent_type]

    async def handle(self, request: AgentRequest) -> AgentResponse:
        """统一请求处理入口。"""
        logger.info(
            "orchestrator.handle_start",
            agent_type=request.agent_type.value,
            customer_id=request.customer_id,
            pipeline_mode=request.pipeline_mode,
        )

        try:
            if request.pipeline_mode:
                result = await self._run_pipeline(request)
                return self._aggregate_pipeline(result, request)
            else:
                return await self._run_single_agent(request)
        except Exception as e:
            logger.error(
                "orchestrator.handle_failed",
                agent_type=request.agent_type.value,
                error=str(e),
                exc_info=True,
            )
            return AgentResponse(
                success=False,
                agent_type=request.agent_type,
                content="系统处理请求时遇到问题，请稍后再试。",
                error_msg=str(e),
            )

    async def _run_single_agent(self, request: AgentRequest) -> AgentResponse:
        """单 Agent 直达模式。"""
        graph = self._get_agent_graph(request.agent_type)

        initial_state = {
            "messages": [HumanMessage(content=request.user_message)],
            "customer_id": request.customer_id,
            "tenant_id": request.tenant_id,
            "session_id": request.session_id,
            **request.context,
        }

        config = {
            "configurable": {
                "thread_id": request.thread_id,
            }
        }

        @with_retry(agent_type=request.agent_type.value)
        async def _invoke():
            return await graph.ainvoke(initial_state, config=config)

        result_state = await _invoke()

        last_message = result_state["messages"][-1]
        content = last_message.text if hasattr(last_message, "text") else str(last_message.content)

        return AgentResponse(
            success=True,
            agent_type=request.agent_type,
            content=content,
            structured=result_state.get("structured_output"),
            fallback_used=result_state.get("fallback_used", False),
        )

    async def _run_pipeline(self, request: AgentRequest) -> PipelineResult:
        """串联 Pipeline：前序 structured 注入后序 context。"""
        pipeline_key = request.context.get("pipeline_key", "return_flow")
        if pipeline_key not in self._pipelines:
            raise PipelineError(f"未知 Pipeline 类型: {pipeline_key}")

        agent_sequence = self._pipelines[pipeline_key]
        result = PipelineResult()
        current_context = dict(request.context)

        for idx, agent_type in enumerate(agent_sequence):
            step_request = AgentRequest(
                customer_id=request.customer_id,
                tenant_id=request.tenant_id,
                session_id=f"{request.session_id}_step{idx}",
                agent_type=agent_type,
                user_message=request.user_message,
                context=current_context,
                pipeline_mode=False,
            )

            logger.info(
                "orchestrator.pipeline_step_start",
                step=idx + 1, total=len(agent_sequence), agent_type=agent_type.value,
            )

            step_response = await self._run_single_agent(step_request)
            result.steps.append(step_response)

            if not step_response.success:
                logger.warning(
                    "orchestrator.pipeline_step_failed",
                    step=idx + 1, agent_type=agent_type.value, error=step_response.error_msg,
                )
                break

            if step_response.structured:
                current_context[f"{agent_type.value}_result"] = step_response.structured
                logger.info(
                    "orchestrator.pipeline_context_passed",
                    from_agent=agent_type.value,
                    keys=list(step_response.structured.keys()),
                )

        return result

    def _aggregate_pipeline(
        self,
        pipeline_result: PipelineResult,
        request: AgentRequest,
    ) -> AgentResponse:
        """聚合 Pipeline 各步骤结果。"""
        combined = {}
        all_contents = []
        any_success = False

        for idx, step in enumerate(pipeline_result.steps):
            step_key = f"step_{idx + 1}"
            combined[step_key] = {
                "agent_type": step.agent_type.value,
                "success": step.success,
                "structured": step.structured,
            }
            if step.success:
                any_success = True
                if step.content:
                    all_contents.append(step.content)

        return AgentResponse(
            success=any_success,
            agent_type=request.agent_type,
            content="\n\n---\n\n".join(all_contents),
            structured=combined,
            fallback_used=any(s.fallback_used for s in pipeline_result.steps),
        )


_orchestrator_instance: Optional[Orchestrator] = None


def get_orchestrator() -> Orchestrator:
    """获取 Orchestrator 单例。"""
    global _orchestrator_instance
    if _orchestrator_instance is None:
        _orchestrator_instance = Orchestrator()
    return _orchestrator_instance
