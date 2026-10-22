"""将维护者配置输出为 Actions 参数；不读取申请提供的工具来源。"""
import os
from market.common import COMMIT, read_json, repository

config = read_json("config/market.json")
if not COMMIT.fullmatch(config["host_commit"]):
    raise SystemExit("请在 config/market.json 配置完整 host_commit；不能使用浮动分支")
slug = repository(config["host_repository"]).split("github.com/")[1]
with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
    stream.write(f"repository={slug}\ncommit={config['host_commit']}\n")
