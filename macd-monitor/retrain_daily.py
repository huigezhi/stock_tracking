#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""交易日早上自动迭代重训模型分(每日随新信号/新标签更新 model.json, 提升胜率)

由 systemd timer(或 cron) 在周一~周五 06:00 触发; 脚本内再校验当天是否为 A股交易日,
节假日/周末即使被 cron 误触发也会被跳过 -> 真正做到"非交易日不跑"。

首次为当前年份烘焙了官方休市(工作日)表: 见 HOLIDAYS_2026, 来源为沪深北交易所
2025-12-22 发布的《2026年部分节假日休市安排》。换年份时按其年度通知同步更新即可。

前置依赖(训练需要): pip3 install scikit-learn numpy requests
"""
import datetime
import os
import subprocess
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(BASE, "retrain.log")

# ---- 2026年 A股非交易日(仅列"落在工作日的休市日"; 周六日由 weekday 判断兜底) ----
HOLIDAYS_2026 = {
    "2026-01-01", "2026-01-02",                                   # 元旦
    "2026-02-16", "2026-02-17", "2026-02-18",
    "2026-02-19", "2026-02-20", "2026-02-23",                      # 春节
    "2026-04-06",                                                 # 清明
    "2026-05-01", "2026-05-04", "2026-05-05",                     # 劳动节
    "2026-06-19",                                                 # 端午
    "2026-09-25",                                                 # 中秋
    "2026-10-01", "2026-10-02", "2026-10-05",
    "2026-10-06", "2026-10-07",                                   # 国庆
}


def log(msg):
    line = "[{}] {}".format(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def is_trading_day(d):
    """当天是否 A股交易日(周末排除; 若当年烘焙了休市表则进一步排除节假日)"""
    if d.weekday() >= 5:                     # 周六 / 周日
        return False
    key = "HOLIDAYS_{}".format(d.strftime("%Y"))
    table = globals().get(key)
    if table is None:                        # 当年无休市表: 默认周一~五视为交易日
        return True
    return d.strftime("%Y-%m-%d") not in table


def main():
    today = datetime.date.today()
    if not is_trading_day(today):
        log("跳过: {} 非交易日".format(today))
        return
    log("== 交易日, 开始每日模型迭代重训 ==")
    try:
        p = subprocess.run([sys.executable, "train_model.py"],
                           capture_output=True, text=True, cwd=BASE)
        out = ((p.stdout or "") + (p.stderr or "")).strip()
        if out:
            log("\n".join("  " + l for l in out.splitlines()))
        if p.returncode == 0:
            log("训练完成: model.json 已更新")
        else:
            log("训练结束但返回值非0(可能是样本不足或数据不可用), 请查看上方日志")
    except Exception as e:
        log("训练异常: {!r}".format(e))


if __name__ == "__main__":
    main()