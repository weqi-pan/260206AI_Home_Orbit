import gc
import os
import shutil
import uuid
from pathlib import Path


PYTEST_TEMP_ROOT = Path(r"C:\Users\31346\AppData\Local\Temp\kilo\ai-customer-service-pytest")


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
