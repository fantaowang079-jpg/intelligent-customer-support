# backend/config.py
# 全项目唯一的「配置中心」：从 .env.local 读取所有配置项，供任何模块取用。

from pydantic_settings import BaseSettings   # Pydantic 的「配置基类」，自动从环境变量/.env 读取并做类型校验
from functools import lru_cache              # 标准库装饰器：缓存函数结果，让函数实际只执行一次
import os

# 项目根目录（backend 的上一级）
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# .env.local 文件路径
env_local_path = os.path.join(_project_root, ".env.local")


class Settings(BaseSettings):

    class Config:
        """Pydantic 元配置：告诉 BaseSettings 怎么读取配置。"""
        env_file = env_local_path          # 从该文件读取配置
        env_file_encoding = "utf-8"        # 文件编码
        case_sensitive = False             # 大小写不敏感
        extra = "ignore"                   # .env.local 里多出来的字段一律忽略（兼容旧 EduAgent 字段）

    # ── 大模型（DeepSeek）──
    deepseek_api_key: str = ""                              # DeepSeek API Key
    deepseek_base_url: str = "https://api.deepseek.com/"    # DeepSeek 接口地址

    # ── 本地模型权重路径（相对 backend 目录）──
    reranker_model_path: str = "models/reranker/bge-reranker-large"   # 精排模型
    bge_m3_model_path: str = "models/embedding/bge-m3"                # 嵌入模型

    # ── 应用基础配置 ──
    app_env: str = "local"                   # 运行环境标识
    app_debug: bool = False                  # 是否调试模式
    app_host: str = "0.0.0.0"                # 监听地址
    app_port: int = 8000                     # 监听端口
    log_level: str = "INFO"                  # 日志级别
    default_tenant_id: str = "tenant_default"  # 多租户默认值


@lru_cache()
def get_settings() -> Settings:
    """获取全局唯一的配置对象。任何模块要用配置，都调用这个函数。"""
    return Settings()


config = Settings()


if __name__ == '__main__':
    print(Settings().deepseek_base_url)
