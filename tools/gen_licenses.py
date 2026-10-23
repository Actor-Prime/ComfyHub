# -*- coding: utf-8 -*-
"""生成 THIRD_PARTY_LICENSES.txt：扫描打包环境的全部依赖，列出名称/版本/许可/来源

用法：
    .venv_build/Scripts/python.exe tools/gen_licenses.py

发布前必须重跑一次（依赖变化时）。
"""
import importlib.metadata as md
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "THIRD_PARTY_LICENSES.txt")

HEADER = """ComfyHub 第三方组件许可声明
================================

ComfyHub 本体以 MIT 许可发布（见 LICENSE）。

本程序在运行与构建过程中使用以下第三方组件。各组件的版权归其各自作者所有，
并遵循其各自的许可条款。完整的许可文本见各组件官方仓库。
"""

LGPL_NOTE = """
重要说明（LGPL 组件）
---------------------
本程序使用 PySide6（Qt for Python），遵循 GNU Lesser General Public License v3 (LGPL-3.0)。

为满足 LGPL 关于"允许用户替换库"的要求：
  · 本程序以**目录形式**（onedir）发布，Qt 动态库位于 _internal/PySide6/ 目录下，
    用户可自行替换为兼容版本的 Qt 库。
  · 本程序未修改 PySide6 / Qt 的源代码。
  · LGPL-3.0 全文：https://www.gnu.org/licenses/lgpl-3.0.txt
  · Qt 源码：https://www.qt.io/
  · PySide6 源码：https://code.qt.io/cgit/pyside/pyside-setup.git/
"""


def license_of(dist):
    lic = (dist.metadata.get("License") or "").strip()
    if len(lic) > 60:          # 有些包把整段文本塞进 License 字段
        lic = ""
    if not lic:
        for c in (dist.metadata.get_all("Classifier") or []):
            if c.startswith("License ::"):
                lic = c.replace("License ::", "").replace("OSI Approved ::", "").strip()
                break
    return lic or "（见项目主页）"


def main():
    rows = []
    for dist in md.distributions():
        name = (dist.metadata["Name"] or "").strip()
        if not name:
            continue
        ver = (dist.version or "").strip()
        lic = license_of(dist)
        home = ""
        for u in (dist.metadata.get_all("Project-URL") or []):
            if "home" in u.lower() or "source" in u.lower() or "repository" in u.lower():
                home = u.split(",", 1)[-1].strip()
                break
        if not home:
            home = f"https://pypi.org/project/{name}/"
        rows.append((name, ver, lic, home))

    rows.sort(key=lambda r: r[0].lower())
    # 去重（同名多版本只留一个）
    seen, uniq = set(), []
    for r in rows:
        if r[0].lower() in seen:
            continue
        seen.add(r[0].lower())
        uniq.append(r)

    lines = [HEADER, f"共 {len(uniq)} 个第三方组件\n", "-" * 78, ""]
    for name, ver, lic, home in uniq:
        lines.append(f"{name} {ver}")
        lines.append(f"    License: {lic}")
        lines.append(f"    Source : {home}")
        lines.append("")
    lines.append("-" * 78)
    lines.append(LGPL_NOTE)

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"✅ 已生成 {OUT}（{len(uniq)} 个组件）")
    # 提示高风险许可（PyInstaller 有 bootloader 例外条款，打包产物不受 GPL 传染）
    BUILD_EXEMPT = {"pyinstaller"}
    risky = [r for r in uniq
             if any(k in r[2].upper() for k in ("GPL", "AGPL", "SSPL"))
             and "LGPL" not in r[2].upper()
             and r[0].lower() not in BUILD_EXEMPT]
    if risky:
        print("\n⚠️ 检测到传染性许可组件（需复核）：")
        for r in risky:
            print(f"   · {r[0]} {r[1]} — {r[2]}")
    else:
        print("✓ 未发现传染性许可组件")
        print("  （PyInstaller 为 GPLv2 + Bootloader 例外条款：打包出的程序不受 GPL 约束）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
