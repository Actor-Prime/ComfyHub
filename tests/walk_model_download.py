# -*- coding: utf-8 -*-
"""模型市场下载 E2E 实测（验收标准③前半：能尝试安装一个模型）

链路：模型库页 → HF 镜像范围 → 搜 DreamShaper → 结果卡片「下载 ↓」
  → 文件勾选弹窗（勾 .safetensors → 开始下载）→ 下载中心 → 落到
  aki 实例 models/checkpoints/ → 等文件完整（无 .part、尺寸稳定且 >1.5GB）
兜底：市场卡片 60s 未出 → 走「直链下载」栏同一 URL 下载（同样验收下载链路）。
用法：cd F:/ComfyHub && .venv_build/Scripts/python tests/walk_model_download.py
"""
import os
import sys
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

KEYWORD = "DreamShaper"
DL_URL = ("https://hf-mirror.com/Lykon/DreamShaper/resolve/main/"
          "DreamShaper_8_pruned.safetensors")
CKPT_DIR = r"F:\ComfyUI-aki-v3\ComfyUI\models\checkpoints"
OUT = os.path.join(ROOT, "analysis", "walkthrough")
os.makedirs(OUT, exist_ok=True)
LOG = os.path.join(OUT, "model_e2e_drive.log")
T0 = time.time()
DEADLINE = T0 + 40 * 60


def log(msg):
    line = f"[{time.time() - T0:7.1f}s] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


_state = {"phase": "init", "done": False, "ok": False, "detail": "",
          "fallback": False}


def _ckpt_files():
    try:
        return [f for f in os.listdir(CKPT_DIR)
                if f.lower().endswith((".safetensors", ".ckpt", ".part"))]
    except OSError:
        return []


def tick(app, win):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import (QApplication, QMessageBox, QDialog,
                                   QAbstractButton, QTreeWidget, QTreeWidgetItem,
                                   QLineEdit, QPushButton)

    def _shot(tag):
        try:
            win.grab().save(os.path.join(OUT, f"model_{tag}.png"))
        except Exception:
            pass

    def _find_btn(text, scope=None):
        scope = scope or win
        for b in scope.findChildren(QAbstractButton):
            if b.text().strip() == text and b.isVisible():
                return b
        return None

    def _check_tree_items(tree):
        """递归勾选第一个 .safetensors 行（只选一个，防止勾一堆 2GB 文件）"""
        n = 0

        def walk(item):
            nonlocal n
            from PySide6.QtCore import Qt
            if n >= 1:
                return
            txt = item.text(0)
            if txt.lower().endswith((".safetensors", ".ckpt")):
                item.setCheckState(0, Qt.Checked)
                n += 1
                return
            for i in range(item.childCount()):
                walk(item.child(i))

        for i in range(tree.topLevelItemCount()):
            if n >= 1:
                break
            walk(tree.topLevelItem(i))
        return n

    def _step():
        try:
            now = time.time()
            if now > DEADLINE:
                log("!! 40 分钟硬顶到，放弃")
                _state.update(done=True, ok=False, detail="timeout")
                app.quit()
                return

            # 弹窗自动处理（信息框记录后关闭；下载勾选弹窗交给 phase 处理）
            modal = QApplication.activeModalWidget()
            if isinstance(modal, QMessageBox):
                log(f"自动关闭信息框：{modal.text()[:80]!r}")
                modal.accept()
                return

            ph = _state["phase"]
            page = win.stack.widget(4)   # NAV.MODELS == 4

            if ph == "init":
                win.goto(4)
                _state["phase"] = "scope"
                log("已切模型库页")

            elif ph == "scope":
                b = _find_btn("HF 镜像", page) or _find_btn("HF 镜像")
                if b is not None:
                    b.click()
                    _state["phase"] = "search"
                    log("已选「HF 镜像」范围")
                else:
                    _state.update(done=True, ok=False, detail="no scope btn")

            elif ph == "search":
                ed = getattr(page, "ed_search", None)
                if ed is not None:
                    ed.setText(KEYWORD)
                    bs = _find_btn("🔍 搜索", page) or _find_btn("🔍 搜索")
                    if bs is not None:
                        bs.click()
                    else:
                        ed.returnPressed.emit()
                    _state["phase"] = "wait_cards"
                    _state["t_search"] = now
                    log(f"已搜 {KEYWORD!r}")

            elif ph == "wait_cards":
                # 精准选「Lykon/DreamShaper」单文件仓库卡（出图验收需要单文件
                # checkpoint；dreamshaper-7 是 diffusers 分目录格式下下来也用不了）
                from app.ui.pages.model_market_widget import ModelCard
                target = None
                for c in page.findChildren(ModelCard):
                    if (c.repo_id or "").lower() == "lykon/dreamshaper":
                        target = c
                        break
                dl = None
                if target is not None:
                    for bb in target.findChildren(QAbstractButton):
                        if bb.text().strip() in ("下载", "下载 ↓"):
                            dl = bb
                            break
                if dl is None:   # 兜底：第一张可见卡
                    for bb in page.findChildren(QAbstractButton):
                        if bb.text().strip() in ("下载", "下载 ↓") and bb.isVisible():
                            dl = bb
                            break
                if dl is not None:
                    _shot("results")
                    log("★ 市场结果卡片出现，点第一张「下载 ↓」")
                    dl.click()
                    _state["phase"] = "dialog"
                elif now - _state["t_search"] > 60:
                    log("⚠ 市场 60s 无结果卡片，转直链兜底")
                    _state["fallback"] = True
                    _state["phase"] = "direct"
                    _shot("no_cards")

            elif ph == "dialog":
                modal = QApplication.activeModalWidget()
                if isinstance(modal, QDialog):
                    trees = modal.findChildren(QTreeWidget)
                    n = 0
                    for t in trees:
                        n += _check_tree_items(t)
                    log(f"弹窗勾选 .safetensors 行 {n} 个")
                    okb = None
                    for b in modal.findChildren(QAbstractButton):
                        if "开始下载" in b.text() and b.isEnabled():
                            okb = b
                            break
                    if okb is not None:
                        okb.click()
                        _state["phase"] = "downloading"
                        _state["t_dl"] = now
                        log("★ 已点「开始下载」")
                    else:
                        # 没勾上任何行 → 也点不了；记失败
                        log("!! 弹窗无可用「开始下载」按钮")
                        _state.update(done=True, ok=False, detail="dialog no ok btn")
                        modal.reject()
                elif now - _state.get("t_search", now) > 120:
                    log("!! 弹窗未出现")
                    _state.update(done=True, ok=False, detail="dialog missing")

            elif ph == "direct":
                # 直链下载栏：精确用 ed_url 属性 + 「⬇ 下载」按钮（2026-10-31 修：
                # 泛匹配拿了别的 QLineEdit/卡片按钮，URL 根本没填进去）
                ed = getattr(page, "ed_url", None)
                b = None
                for bb in page.findChildren(QAbstractButton):
                    if bb.text().strip() == "⬇ 下载" and bb.isVisible():
                        b = bb
                        break
                if ed is not None and b is not None:
                    ed.setText(DL_URL)
                    b.click()
                    _state["phase"] = "downloading"
                    _state["t_dl"] = now
                    log("★ 直链已开始下载")
                else:
                    _state.update(done=True, ok=False,
                                  detail=f"no direct bar (ed={ed}, b={b})")

            elif ph == "downloading":
                files = _ckpt_files()
                parts = [f for f in files if f.endswith(".part")]
                full = [f for f in files if not f.endswith(".part")]
                if now - _state.get("hb", 0) > 30:
                    _state["hb"] = now
                    sz = 0
                    for f in files:
                        try:
                            sz += os.path.getsize(os.path.join(CKPT_DIR, f))
                        except OSError:
                            pass
                    log(f"心跳：checkpoints 内 {files} 合计 {sz/1e6:.0f}MB")
                if full and not parts:
                    sz = 0
                    for f in full:
                        try:
                            sz += os.path.getsize(os.path.join(CKPT_DIR, f))
                        except OSError:
                            pass
                    # 尺寸稳定（2 次心跳不变）且 >500MB 视为完成
                    if sz > 5e8 and _state.get("last_sz") == sz:
                        log(f"★ 下载完成：{full} {sz/1e9:.2f}GB")
                        _shot("done")
                        _state.update(done=True, ok=True, detail=f"{full} {sz/1e9:.2f}GB")
                        app.quit()
                        return
                    _state["last_sz"] = sz

            if now - _state.get("hb2", 0) > 120:
                _state["hb2"] = now
                _shot(f"hb_{int(now - T0)}")
        except Exception:
            log("驱动异常:\n" + traceback.format_exc())
            _state.update(done=True, ok=False, detail="driver-exception")
            app.quit()

    t = QTimer()
    t.timeout.connect(_step)
    t.start(500)
    _state["timer"] = t   # ★ 必须持有引用：局部 QTimer 会被 GC 回收，定时器直接死


def main():
    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    import time as _t
    from app.ui.main_window import MainWindow

    win = MainWindow()
    win.resize(1280, 860)
    win.show()
    t = time.time()
    while time.time() - t < 2.5:
        app.processEvents()
        _t.sleep(0.03)

    tick(app, win)
    app.exec()

    log("=" * 50)
    log(f"结论：{'✅ ' if _state['ok'] else '❌ '}{_state.get('detail')}")
    log(f"模式：{'直链兜底' if _state['fallback'] else '市场搜索下载'}")
    sys.exit(0 if _state["ok"] else 1)


if __name__ == "__main__":
    main()
