# backend/core/intent_router.py
# 意图路由：规则前置拦截（零 Token）+ LLM 路由 + 高危词/紧急度识别（对应 8.4）。

import json
import re
from dataclasses import dataclass
from enum import Enum

from langchain_core.messages import HumanMessage

from backend.core.llm_factory import get_llm
from backend.core.logger import get_logger

logger = get_logger(__name__)


class Intent(str, Enum):
    FAQ       = "faq"        # 产品/政策/售后咨询
    ORDER     = "order"      # 订单状态/物流查询
    RETURN    = "return"     # 退换货/退款申请
    COMPLAINT = "complaint"  # 投诉/不满
    HUMAN     = "human"      # 明确要求转人工
    CLARIFY   = "clarify"    # 意图不明


@dataclass
class RouteResult:
    intent:     str    # faq/order/return/complaint/human/clarify
    urgency:    str    # normal / high
    route_to:   str    # 实际派发目标：faq/order/return/complaint/human
    reason:     str
    confidence: float


# ── 高危词 / 强情绪词（命中即标高优先级，优先转人工通道）─────────

_HIGH_RISK_KEYWORDS = (
    "投诉", "差评", "曝光", "12315", "报警", "律师", "举报", "维权",
    "媒体", "起诉", "消协", "监管", "索赔",
)

_STRONG_EMOTION_KEYWORDS = (
    "气死", "太差", "垃圾", "骗子", "坑", "恶心", "差劲", "失望",
    "愤怒", "火大", "受不了", "烂透", "欺诈",
)


# ── 规则前置拦截：五类元话术（零 Token）─────────────────────────

_STRIP_TAIL_RE = re.compile(r"[\s!！?？。~～,.，。]+$")

_HELLO_KEYWORDS = frozenset([
    "你好", "您好", "hi", "hello", "hey", "哈喽", "嗨",
    "在吗", "在不在", "在线吗", "有人吗",
])
_THANKS_KEYWORDS = frozenset([
    "谢谢", "感谢", "多谢", "谢了", "非常感谢", "万分感谢",
    "辛苦了", "辛苦", "麻烦了", "不好意思",
])
_BYE_KEYWORDS = frozenset([
    "再见", "拜拜", "拜", "88", "886", "bye", "goodbye", "byebye",
])
_HUMAN_KEYWORDS = frozenset([
    "人工", "转人工", "人工客服", "找人工", "我要人工", "人工服务",
])
_IDENTITY_RE = re.compile(
    r"(你|您)(是谁|叫什么|的名字|是什么|是.*机器人|是.*助手)"
    r"|介绍.{0,4}(你自己|自己|一下)",
    re.IGNORECASE,
)
_CAPABILITY_RE = re.compile(
    r"(你|您)(能|可以|会).{0,6}(做|帮|干)"
    r"|(你|您).{0,4}(功能|用途|能力|特点)"
    r"|怎么(用|使用)(你|您|这个)?",
    re.IGNORECASE,
)

_REPLY_HELLO = "您好！我是智能客服小助手。\n\n可以帮您：查订单、申请退换货、咨询产品与政策。请问有什么可以帮到您？"
_REPLY_THANKS = "不客气，很高兴能帮到您！如还有其它问题，随时告诉我。"
_REPLY_BYE = "再见！感谢您的咨询，祝您生活愉快！"
_REPLY_HUMAN = "好的，正在为您转接人工客服，请稍候（预计等待 1-2 分钟）…"
_REPLY_IDENTITY = "我是智能客服小助手，可以帮您查订单、申请退换货、咨询产品政策，复杂问题会自动为您转接人工客服。"
_REPLY_CAPABILITY = "我可以帮您：\n- 查订单状态与物流\n- 申请退换货 / 退款\n- 咨询产品与售后政策\n- 处理投诉（高危诉求将优先转人工）\n请直接告诉我您的需求。"


def _pre_filter(text: str) -> str | None:
    """规则前置拦截：命中五类元话术则返回模板回复；无命中返回 None。"""
    t = text.strip()
    t_lower = _STRIP_TAIL_RE.sub("", t.lower())

    if t_lower in _HELLO_KEYWORDS:
        return _REPLY_HELLO
    if t_lower in _THANKS_KEYWORDS:
        return _REPLY_THANKS
    if t_lower in _BYE_KEYWORDS:
        return _REPLY_BYE
    if t_lower in _HUMAN_KEYWORDS:
        return _REPLY_HUMAN
    if _IDENTITY_RE.search(t):
        return _REPLY_IDENTITY
    if _CAPABILITY_RE.search(t):
        return _REPLY_CAPABILITY
    return None


# ── LLM 路由 Prompt ──────────────────────────────────────────

_ROUTE_PROMPT = """判断用户消息应路由到哪个客服能力。

可选能力：
- faq       : 产品/售后政策/规则咨询（无需订单号）
- order     : 查询订单状态、物流、发货进度
- return    : 退换货、退款申请相关
- complaint : 投诉、不满、情绪化表达
- human     : 明确要求转人工客服
- clarify   : 意图不明，需要追问

严格按以下 JSON 格式返回，不要有其他内容：
{{"label": "能力名", "reason": "一句话判断依据"}}

用户输入：{message}"""

_VALID_LABELS = frozenset({"faq", "order", "return", "complaint", "human", "clarify"})


async def route(message: str, history: list | None = None) -> RouteResult:
    """
    意图路由主入口：高危词/强情绪 → 直接标高优先级转投诉/人工；否则 LLM 分类。

    返回 RouteResult{intent, urgency, route_to, reason, confidence}
    """
    t = message.strip()

    # ── 高危词 / 强情绪 → 直接高优先级，走投诉通道（优先转人工）──
    if any(k in t for k in _HIGH_RISK_KEYWORDS) or any(k in t for k in _STRONG_EMOTION_KEYWORDS):
        logger.info("intent_router.high_risk_by_keyword", preview=t[:40])
        return RouteResult(
            intent="complaint", urgency="high", route_to="complaint",
            reason="命中高危/强情绪词，标高优先级，优先转人工通道",
            confidence=1.0,
        )

    # ── LLM 分类 ─────────────────────────────────────────────
    label = "faq"
    reason = "路由判断异常，默认转 FAQ"
    confidence = 0.85
    try:
        llm = get_llm("intent", temperature=0)
        resp = await llm.ainvoke([HumanMessage(content=_ROUTE_PROMPT.format(message=t))])
        raw = resp.text.strip()
        parsed = json.loads(raw)
        label = parsed.get("label", "faq").strip().lower()
        reason = parsed.get("reason", "LLM 路由判断")
        if label not in _VALID_LABELS:
            logger.warning("intent_router.unknown_label", label=label, fallback="faq")
            label = "faq"
        logger.info("intent_router.llm_result", label=label, reason=reason)
    except Exception as e:
        logger.warning("intent_router.llm_failed", error=str(e), fallback="faq")

    return RouteResult(
        intent=label, urgency="normal", route_to=label,
        reason=reason, confidence=confidence,
    )
