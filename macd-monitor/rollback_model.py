#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回滚线上模型到 models/ 下的指定版本
用法: python3 rollback_model.py <YYYYMMDD>
"""
import json
import os
import shutil
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = os.path.join(BASE, "models")
MODEL_PATH = os.path.join(BASE, "model.json")

if len(sys.argv) != 2:
    print("用法: python3 rollback_model.py <YYYYMMDD>")
    sys.exit(1)
v = sys.argv[1].strip()
src = os.path.join(MODELS_DIR, f"model_v{v}.json")
assert os.path.exists(src), f"{src} 不存在"
tmp = MODEL_PATH + ".tmp"
shutil.copy(src, tmp)
os.replace(tmp, MODEL_PATH)
print("回滚到", v, "AUC=",
      json.load(open(src, encoding="utf-8")).get("auc"))
