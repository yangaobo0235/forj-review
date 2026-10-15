"""测试收集策略。

历史上这里用一个子串名单把「过户业务」的旧夹具整体跳过。那些测试在扩展包
收口后已经完全失效（断言的是已改名的方法、已清空的策略和已停用的业务），
留着只会掩盖真实失败，因此已随本次重构删除。**不要再引入按名字静默跳过的
机制**——需要停用的测试直接删掉，或写成显式的 `pytest.mark.skip`。
"""

import os
import tempfile

# 测试进程不能往开发者的日志文件里写：`configure_log_file` 会读 `.env` 里的
# `REVIEW_LOG_DIR`，混进去的测试行会污染按天聚合的性能数据。这里先占位成一个
# 临时目录——`load_dotenv` 用 `setdefault`，已存在的进程环境变量优先级更高，
# 不会被 `.env` 覆盖。conftest 先于任何测试模块导入，因此也先于 `forj_review_assistant.main`。
os.environ.setdefault("REVIEW_LOG_DIR", tempfile.mkdtemp(prefix="review-agent-test-logs-"))
