from pathlib import Path


def pytest_configure(config):
    # pytest 的 --basetemp 只创建最后一级目录，首次 checkout 也应能直接运行。
    if config.option.basetemp:
        Path(config.option.basetemp).resolve().parent.mkdir(parents=True, exist_ok=True)
