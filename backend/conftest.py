import gc
import os
import shutil
import tempfile
import uuid
from pathlib import Path


PYTEST_TEMP_ROOT = Path(tempfile.gettempdir()) / "ai-customer-service-pytest"

# CI 和全新克隆没有仓库根 .env。测试进程固定使用隔离的 test 配置，
# 避免触发生产启动校验；真实密钥仍只通过部署环境注入。
TEST_ENV_DEFAULTS = {
    "APP_MODE": "test",
    "JWT_SECRET": "pytest-only-jwt-secret-0123456789abcdef0123456789abcdef",
    "ADMIN_INITIAL_PASSWORD": "AdminTest@12345",
    "DEMO_INITIAL_PASSWORD": "DemoTest@123456",
    "LLM_CREDENTIAL_MASTER_KEY": "AAECAwQFBgcICQABAgMEBQYHCAkAAQIDBAUGBwgJAA==",
}


def _ensure_test_environment() -> None:
    for key, value in TEST_ENV_DEFAULTS.items():
        os.environ.setdefault(key, value)


def _process_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _cleanup_stale_runs() -> None:
    for path in PYTEST_TEMP_ROOT.glob("run-*-*"):
        try:
            pid = int(path.name.split("-", 2)[1])
        except (IndexError, ValueError):
            continue
        if not _process_is_running(pid):
            shutil.rmtree(path, ignore_errors=True)


def pytest_configure(config):
    _ensure_test_environment()
    if config.option.basetemp:
        return
    PYTEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    _cleanup_stale_runs()
    run_temp = PYTEST_TEMP_ROOT / f"run-{os.getpid()}-{uuid.uuid4().hex}"
    config.option.basetemp = str(run_temp)
    config._ai_customer_service_basetemp = run_temp


def pytest_unconfigure(config):
    run_temp = getattr(config, "_ai_customer_service_basetemp", None)
    if run_temp:
        gc.collect()
        shutil.rmtree(run_temp, ignore_errors=True)
