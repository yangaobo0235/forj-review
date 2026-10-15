"""Console-preserving daily file logging with graceful fallback."""
import logging
import os
from logging.handlers import TimedRotatingFileHandler

from forj_review_assistant.config.settings import load_dotenv


def configure_log_file(handler_logger: logging.Logger) -> None:
    """按 `REVIEW_LOG_DIR` 追加一个按天轮转的文件处理器；未设置时保持只输出控制台。

    这是**追加一条流，不是改一条流**：控制台输出必须保留，部署文档的排障流程
    整段建立在 `docker compose logs` 上，把日志搬走等于把那条路拆了。

    挂在 `uvicorn.error` 而不是 root：Uvicorn 给这个 logger 设了
    `propagate=False`，挂到 root 上一条都收不到。

    容器根文件系统是只读的，所以生产要落盘必须显式挂载可写卷；目录不可写时
    这里只降级成一条告警，不能让审核服务起不来。
    """
    # 先加载本地环境文件：`REVIEW_LOG_DIR` 通常写在 `.env` 里，而触发 dotenv 的
    # `ReviewService()` 要到本函数之后才构造。不先加载就读不到，配了也不生效。
    # `load_dotenv` 用 setdefault，进程环境变量仍然优先，重复调用幂等。
    load_dotenv()
    log_dir = os.getenv("REVIEW_LOG_DIR", "").strip()
    if not log_dir:
        return
    path = os.path.join(log_dir, "review-agent.log")
    target = os.path.abspath(path)
    if any(getattr(existing, "baseFilename", None) == target for existing in handler_logger.handlers):
        return
    try:
        os.makedirs(log_dir, exist_ok=True)
        handler = TimedRotatingFileHandler(
            path,
            when="midnight",
            backupCount=30,
            encoding="utf-8",
        )
    except OSError as exc:
        handler_logger.warning(
            "Review log file disabled: dir=%s error_type=%s",
            log_dir,
            type(exc).__name__,
        )
        return
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    handler_logger.addHandler(handler)
    handler_logger.info(
        "Review log file enabled: dir=%s rotation=daily backup_count=30",
        log_dir,
    )
