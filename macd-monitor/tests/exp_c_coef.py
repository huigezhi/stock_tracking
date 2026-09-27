#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实验C: 读取模型系数方向(score 系数为负/异常则执行 T1.5b)"""
import sys, os, json
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
import model

path = os.path.join(BASE, "model.json")
if not os.path.exists(path):
    print("实验C: 无 model.json, 无法判定, T1.5b 保持跳过")
    sys.exit(0)
m = json.load(open(path, encoding="utf-8"))
coefs = m.get("coef")
if not coefs:
    print("model.json 无 coef 键 → 先跳过, T1.3 中补存 coef")
else:
    names = list(model.FEATURES) + [f"{a}*{b}" for a, b in model.INTERACTIONS]
    for w, n in sorted(zip(coefs, names), key=lambda x: -abs(x[0]))[:15]:
        print(f"{w:+.3f}  {n}")
