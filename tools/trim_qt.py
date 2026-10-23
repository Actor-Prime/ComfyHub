# -*- coding: utf-8 -*-
"""裁剪 PyInstaller 产物里未使用的 Qt 组件

我们只用到 QtWidgets（纯 Widgets 应用），以下组件全部用不到：
  · opengl32sw.dll   软件渲染器（SwiftShader，有 GPU 的机器用不到）19.7MB
  · Qt6Quick/Qml 全家 QML 引擎链                                     ~12.6MB
  · Qt6Pdf / QtPdf.pyd  PDF 模块                                      4.4MB
  · WebEngine 全家  Chromium 内核（如有）                              可达 100MB+

用法（打包流程最后一步）：
    python tools/trim_qt.py [--dist F:/ComfyHub/dist/ComfyHub]
"""
import os
import sys

CUT_FILES = [
    # 软件渲染器
    "opengl32sw.dll",
    # QML / Quick 引擎链
    "Qt6Quick.dll", "Qt6Qml.dll", "Qt6QmlModels.dll", "Qt6QuickWidgets.dll",
    "Qt6QuickShapes.dll", "Qt6QuickParticles.dll", "Qt6QuickTest.dll",
    "Qt6QuickControls2.dll", "QtQuick.pyd", "QtQml.pyd", "QtQuickWidgets.pyd",
    # PDF
    "Qt6Pdf.dll", "QtPdf.pyd", "QtPdfWidgets.pyd",
    # WebEngine（Chromium）
    "Qt6WebEngineCore.dll", "Qt6WebEngineWidgets.dll", "Qt6WebEngineQuick.dll",
    "QtWebEngineCore.pyd", "QtWebEngineWidgets.pyd", "QtWebEngineQuick.pyd",
]
# 相关目录（WebEngine 资源/翻译）
CUT_DIRS = ["QtWebEngine", "translations/QtWebEngine", "resources"]


def main():
    dist = sys.argv[sys.argv.index("--dist") + 1] if "--dist" in sys.argv else \
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dist", "ComfyHub")
    base = os.path.join(dist, "_internal")
    if not os.path.isdir(base):
        print(f"❌ 产物目录不存在: {base}")
        return 1

    def total(p):
        return sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(p) for f in fs)

    before = total(dist)
    freed = 0
    pyside = os.path.join(base, "PySide6")
    for name in CUT_FILES:
        p = os.path.join(pyside, name)
        if os.path.isfile(p):
            freed += os.path.getsize(p)
            os.remove(p)
            print(f"  ✓ 删 {name}")
    for d in CUT_DIRS:
        p = os.path.join(pyside, d.replace("/", os.sep))
        if os.path.isdir(p):
            freed += total(p)
            shutil.rmtree(p)
            print(f"  ✓ 删目录 {d}/")

    after = total(dist)
    print(f"\n裁剪释放 {freed/1048576:.1f} MB")
    print(f"产物体积: {before/1048576:.1f} MB → {after/1048576:.1f} MB")
    return 0


import shutil

if __name__ == "__main__":
    sys.exit(main())
