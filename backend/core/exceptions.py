# backend/core/exceptions.py
# 统一异常体系：所有自定义异常都继承同一基类，
# 便于「一次捕获全部」以及按「可重试 / 不可重试」分类处理。

class EduAgentBaseError(Exception):
    """所有自定义异常的基类。"""
    def __init__(self, message: str, agent_type: str = "", details: dict = None):
        super().__init__(message)
        self.agent_type = agent_type
        self.details = details or {}


class LLMAPIError(EduAgentBaseError):
    """大模型 API 调用失败（超时 / 限流 / 网络错误）。属于【可重试】异常。"""
    pass


class AgentExecutionError(EduAgentBaseError):
    """Agent 业务逻辑执行失败。"""
    pass


class PipelineError(EduAgentBaseError):
    """多 Agent Pipeline（流水线编排）失败。"""
    pass


class IntentRouteError(EduAgentBaseError):
    """意图识别路由失败。"""
    pass


class InvalidInputError(EduAgentBaseError):
    """用户输入不合法。属于【不可重试】异常。"""
    pass


class AuthenticationError(EduAgentBaseError):
    """认证失败。属于【不可重试】异常。"""
    pass


# ── 客服业务异常 ─────────────────────────────────────────────

class OrderNotFoundError(EduAgentBaseError):
    """订单不存在（订单号查不到）。"""
    pass


class AfterSaleAuditError(EduAgentBaseError):
    """退换货审核执行失败。"""
    pass


class EscalationError(EduAgentBaseError):
    """投诉升级 / 转人工失败。"""
    pass
