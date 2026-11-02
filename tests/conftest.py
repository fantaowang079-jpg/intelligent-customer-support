# tests/conftest.py
# 让 pytest 从仓库根目录导入 backend 包（无需安装为 package）。

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
