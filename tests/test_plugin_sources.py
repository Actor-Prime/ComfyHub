# -*- coding: utf-8 -*-
"""插件清单多源轮换回归（2026-10-31 修复：快速轮换 + 用户代理源优先）"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import plugin_store as ps

# 1) 每源 1 次×12s 快速轮换：_fetch_one 默认超时必须是 12
sig = inspect.signature(ps._fetch_one)
assert sig.parameters["timeout"].default == 12, \
    f"_fetch_one 默认超时应为 12s，实际 {sig.parameters['timeout'].default}"

# 2) 无用户代理时：源列表 == 公共 SOURCES
srcs = ps._sources()
assert srcs == ps.SOURCES, "无 gh_proxy 时 _sources() 应原样返回 SOURCES"

# 3) 有用户代理时：代理源排第一且 URL 由 gh_proxy 前缀拼接
import app.config as cfg
_orig = cfg.load_config
cfg.load_config = lambda: {"settings": {"gh_proxy": "https://ghproxy.net/"}}
try:
    srcs = ps._sources()
    assert srcs[0][0].startswith("用户代理"), f"首源应为用户代理，实际 {srcs[0][0]}"
    assert srcs[0][1] == ("https://ghproxy.net/"
                          "https://raw.githubusercontent.com/ltdrdata/ComfyUI-Manager"
                          "/main/custom-node-list.json"), f"代理拼接错误: {srcs[0][1]}"
    assert srcs[1:] == ps.SOURCES, "公共源应保持原顺序跟在后面"

    # 4) gh_proxy 不带尾斜杠也能正确拼接
    cfg.load_config = lambda: {"settings": {"gh_proxy": "https://ghproxy.net"}}
    srcs = ps._sources()
    assert srcs[0][1].startswith("https://ghproxy.net/https://"), \
        f"缺尾斜杠时拼接错误: {srcs[0][1]}"

    # 5) 配置读取炸了也不能拖垮源列表（防御）
    def _boom():
        raise RuntimeError("config broken")
    cfg.load_config = _boom
    assert ps._sources() == ps.SOURCES, "配置异常时应回退公共源"
finally:
    cfg.load_config = _orig

# 6) 公共源必须覆盖 5 个既有源（顺序即优先级，别偷偷删）
assert len(ps.SOURCES) == 5, f"公共源应 5 个，实际 {len(ps.SOURCES)}"

print("test_plugin_sources: OK")
