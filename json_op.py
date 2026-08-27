import json
import os

import requests

from config import DOWNLOAD_TIMEOUT_SECONDS, JSON_URL
from print_info import *


# 下载/重下载都设超时，避免主循环被网络卡死。（定义于 config.py）


def download_json(json_path):
    file = requests.get(JSON_URL, timeout=DOWNLOAD_TIMEOUT_SECONDS)
    file.raise_for_status()

    with open(json_path, "wb") as f:
        f.write(file.content)


def read_json(re_download=False):
    dir_path = os.path.dirname(__file__)
    if dir_path == "":
        dir_path = "."
    json_path = dir_path + "/cards.json"

    if not os.path.exists(json_path):
        sys_print("未找到cards.json,试图通过网络下载文件")
        download_json(json_path)
    elif re_download:
        sys_print("疑似有新版本炉石数据，正在重新下载最新文件")
        download_json(json_path)
    else:
        sys_print("cards.json已存在")

    with open(json_path, "r", encoding="utf8") as f:
        json_string = f.read()
        json_list = json.loads(json_string)
        json_dict = {}
        for item in json_list:
            json_dict[item["id"]] = item
        return json_dict


# 懒加载：首次 query 时才读取（cards.json 缺失时首次会联网下载一次）。
# 原先在模块导入时同步联网，无网/CI 环境会卡在 import。
JSON_DICT = {}
_load_attempted = False
# 未知卡牌触发"重新下载最新数据"最多一次（本次会话），
# 避免主循环里每遇到一张新卡就反复全量下载。
_reload_attempted = False


def query_json_dict(key):
    global JSON_DICT, _load_attempted, _reload_attempted

    if key == "":
        return "Unknown"

    if not _load_attempted:
        _load_attempted = True
        try:
            JSON_DICT = read_json(re_download=False)
        except Exception as exc:
            error_print(f"加载卡牌数据失败：{type(exc).__name__}: {exc}")
            JSON_DICT = {}

    if key in JSON_DICT:
        return JSON_DICT[key]["name"]

    # 认为是炉石更新了，出现了新卡，需要重新下载一次；
    # 若重下载后仍没有，就用占位名继续，且本次会话不再尝试。
    if not _reload_attempted:
        _reload_attempted = True
        try:
            JSON_DICT = read_json(re_download=True)
        except Exception as exc:
            error_print(f"重新下载卡牌数据失败：{type(exc).__name__}: {exc}")
        if key in JSON_DICT:
            return JSON_DICT[key]["name"]

    error_print(f"出现未识别卡牌，使用占位名称继续：{key}")
    return f"Unknown:{key}"


if __name__ == "__main__":
    JSON_DICT = read_json()
    with open("id-name.txt", "w", encoding="utf8") as f:
        for key, val in JSON_DICT.items():
            f.write(key + " " + val["name"] + "\n")

    query_json_dict("SW_085t")
