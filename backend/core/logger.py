# backend/core/logger.py
# 全项目的日志工具：在标准库 logging 之上做一层包装，
# 支持「事件名 + 键值对」的结构化日志写法。

import logging
import sys
import os

_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(_project_root)

from backend.config import get_settings   # 读取配置（需要里面的 log_level）


class _Logger:
    """日志包装类。用 logger.info("事件名", key=value) 这种结构化写法。"""

    def __init__(self, name: str):
        self._log = logging.getLogger(name)

    def _fmt(self, event: str, **kw) -> str:
        if kw:
            return event + " | " + " ".join(f"{k}={v!r}" for k, v in kw.items())
        return event

    def debug(self, event: str, **kw):
        self._log.debug(self._fmt(event, **kw))

    def info(self, event: str, *args, **kw):
        if args:
            self._log.info(event, *args)
        else:
            self._log.info(self._fmt(event, **kw))

    def warning(self, event: str, *args, **kw):
        if args:
            self._log.warning(event, *args)
        else:
            self._log.warning(self._fmt(event, **kw))

    def error(self, event: str, **kw):
        exc_info = kw.pop("exc_info", False)
        self._log.error(self._fmt(event, **kw), exc_info=exc_info)

    def critical(self, event: str, **kw):
        self._log.critical(self._fmt(event, **kw))


def configure_logging() -> None:
    """全局日志配置：应用启动时（main.py）调用一次。"""
    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
        level=level,
        force=True,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_logger(name: str) -> _Logger:
    """对外工厂函数：每个模块用 get_logger(__name__) 拿自己的日志器。"""
    return _Logger(name)


configure_logging()
