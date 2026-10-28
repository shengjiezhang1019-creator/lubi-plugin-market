from pathlib import Path
import pytest


@pytest.fixture
def plugin_manifest():
    return {"name": "test_plugin", "version": "1.0.0", "description": "测试插件",
            "plugin_dependencies": [], "author": {"name": "test"}, "license": "MIT"}


def pytest_configure(config):
    # pytest 的 --basetemp 只创建最后一级目录，首次 checkout 也应能直接运行。
    if config.option.basetemp:
        Path(config.option.basetemp).resolve().parent.mkdir(parents=True, exist_ok=True)
