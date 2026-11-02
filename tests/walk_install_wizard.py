# -*- coding: utf-8 -*-
"""一键安装 E2E 实测（验收标准②③的前置）：真实用户路径全真驱动安装向导

链路：主窗口「＋ 添加」→ _run_setup_wizard（modal exec）→ 计时器状态机驱动：
  选址 F:\\ComfyUI_e2e → 检测 → 下一步 → 开始安装（真下载 Python 源码/依赖/torch）
  → 等全部步骤+最终验证 → 点「完成」→ 主窗口自动注册实例 → 自动关注册成功弹窗

终态判定：wiz.p2.done_ok == True 且 setup_result 有值。
用法：cd F:/ComfyHub && .venv_build/Scripts/python tests/walk_install_wizard.py
"""
import os
import sys
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

TARGET_DIR = r"F:\ComfyUI_e2e"
OUT = os.path.join(ROOT, "analysis", "walkthrough")
os.makedirs(OUT, exist_ok=True)
LOG = os.path.join(OUT, "install_e2e_drive.log")
T0 = time.time()
DEADLINE = T0 + 90 * 60          # 90 分钟硬顶（源码 24min+依赖 13min+torch 3GB 续传）


def log(msg):
    line = f"[{time.time() - T0:7.1f}s] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


_state = {"wiz": None, "phase": "init", "done": False, "ok": False, "detail": ""}


class _WizPatch:
    """包装 SetupWizard 以捕获实例（主窗口在方法内 import，改模块属性即可）"""
    _orig = None

    @classmethod
    def install(cls):
        import app.ui.setup_wizard as sw
        cls._orig = sw.SetupWizard
        orig = cls._orig

        class _Wrap(orig):
            def __init__(self, *a, **kw):
                super().__init__(*a, **kw)
                _state["wiz"] = self
                log("向导实例已创建")

        sw.SetupWizard = _Wrap
        # _run_setup_wizard 里 import 的是模块属性，exec 用的类常量也走父类
        _Wrap.Accepted = orig.Accepted


def tick(app):
    """500ms 状态机：驱动向导走完全程"""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox, QWizard

    def _shot(tag):
        w = _state["wiz"]
        (w or QApplication.activeWindow())
        target = w if w is not None else QApplication.activeWindow()
        if target is not None:
            try:
                target.grab().save(os.path.join(OUT, f"install_{tag}.png"))
            except Exception:
                pass

    def _step():
        try:
            wiz = _state["wiz"]
            now = time.time()

            # ---- 已收尾：timer 在 modal 嵌套循环里可能还活着，别再刷 ----
            if _state["done"]:
                return

            # ---- 抓向导内部日志（pip/下载明细在 p2.log，光看心跳是瞎子）----
            p2log = getattr(getattr(wiz, "p2", None), "log", None)
            if p2log is not None:
                try:
                    txt = p2log.toPlainText()
                    n = _state.get("log_n", 0)
                    if len(txt) > n:
                        for ln in txt[n:].splitlines():
                            if ln.strip():
                                log("[向导] " + ln[:200])
                        _state["log_n"] = len(txt)
                except Exception:
                    pass

            # ---- 全局超时保护 ----
            if now > DEADLINE:
                log("!! 90 分钟硬顶到，放弃")
                _state["done"], _state["ok"] = True, False
                _state["detail"] = "timeout"
                _shot("timeout")
                # ★ 2026-10-31 修：app.quit() 退不出 modal exec() 的嵌套事件循环
                #   （实测死循环刷日志 1 小时）——先 reject() 关向导破掉嵌套循环，
                #   exec() 返回后主流程自然收尾。
                try:
                    if wiz is not None:
                        wiz.reject()
                except Exception:
                    log("reject 向导异常:\n" + traceback.format_exc())
                app.quit()
                return

            # ---- 自动关掉一切模态弹窗（注册成功提示等）----
            modal = QApplication.activeModalWidget()
            if isinstance(modal, QMessageBox):
                log(f"自动关闭弹窗：{modal.text()[:60]!r}")
                modal.accept()
                return

            if wiz is None:
                return

            ph = _state["phase"]

            # ---- 阶段 1：填目录/名称 → 触发检测 ----
            if ph == "init":
                wiz.ed_dir.setText(TARGET_DIR)
                wiz.ed_name.setText("E2E测试")
                wiz._detect_existing()
                _state["phase"] = "detect"
                log(f"已填目录 {TARGET_DIR}，触发检测")
                _shot("p1_filled")

            elif ph == "detect":
                # 等 p1 的检测落定：下一步按钮可用且非检测中
                nb = wiz.button(QWizard.NextButton)
                if nb is not None and nb.isEnabled():
                    _state["phase"] = "next"
                    log("检测落定，准备下一步")

            elif ph == "next":
                _shot("p1_ready")
                wiz.button(QWizard.NextButton).click()
                _state["phase"] = "commit"
                log("已点下一步")

            elif ph == "commit":
                # ★ 不点 CommitButton：向导 _on_page_change 进 p2 即自动开装
                #   （2026-10-31 实测确认），乱点 Commit 反而可能触发 accept 关向导。
                #   只监测进入 p2 → 转 installing 等 done_ok。
                if (getattr(wiz, "p2", None) is not None
                        and wiz.currentPage() is wiz.p2):
                    _state["phase"] = "installing"
                    log("★ 已进入安装页，安装自动开始（真下载中）")
                    _shot("p2_before")

            elif ph == "installing":
                p2 = getattr(wiz, "p2", None)
                if p2 is None:
                    return
                if getattr(p2, "done_ok", False):
                    _state["phase"] = "finish"
                    log("★ 全部步骤+最终验证通过")
                    _shot("p2_done")
                    return
                # 失败决策（重试按钮可见=某步挂了）
                b_act = getattr(p2, "b_act", None)
                if (b_act is not None and b_act.isVisible()
                        and getattr(p2, "_phase", "") != "final_verify"
                        and "重新验证" not in b_act.text()
                        and "重试" not in b_act.text()):
                    pass
                if b_act is not None and b_act.isVisible() and "重试" in b_act.text():
                    st = getattr(p2, "lb_state", None)
                    txt = st.text() if st else ""
                    if "失败" in txt or "❌" in txt:
                        log(f"!! 步骤失败：{txt[:100]}")
                        _state["detail"] = txt[:200]
                        _shot("p2_failed")
                        # 尝试重试一次（真实用户行为），失败两次才弃
                        if not _state.get("retried"):
                            _state["retried"] = True
                            log("自动点重试（第 1 次）")
                            b_act.click()
                # 每 60s 心跳记录步骤状态
                if now - _state.get("hb", 0) > 60:
                    _state["hb"] = now
                    st = getattr(p2, "lb_state", None)
                    step = getattr(p2, "lb_step", None)
                    log(f"心跳：{step.text() if step else ''} | {st.text() if st else ''}")

            elif ph == "finish":
                wiz.button(QWizard.FinishButton).click()
                _state["phase"] = "wait_exec"
                log("已点「完成」")

            elif ph == "wait_exec":
                # exec() 返回后 wiz 会被 deleteLater，注册已完成
                if not wiz.isVisible():
                    _state["done"] = True
                    _state["ok"] = True
                    log("向导已关闭，注册链路完成")
                    app.quit()
        except Exception:
            log("驱动异常:\n" + traceback.format_exc())
            _state["done"] = True
            _state["ok"] = False
            _state["detail"] = "driver-exception"
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

    _WizPatch.install()
    tick(app)
    log("调用 _run_setup_wizard（modal）…")

    # modal exec：计时器在 exec 事件循环里照常触发
    win._run_setup_wizard()

    # exec 返回（向导关闭或异常退出）
    if not _state["done"]:
        _state["done"] = True
        # 检查注册结果
    import json
    cfgp = os.path.join(os.environ.get("APPDATA", ""), "ComfyHub", "config.json")
    found = None
    try:
        with open(cfgp, encoding="utf-8") as f:
            cfg = json.load(f)
        for inst in cfg.get("instances", []):
            if inst.get("comfyui_path", "").rstrip("\\/") == TARGET_DIR:
                found = inst
    except Exception as e:
        log(f"读配置异常: {e}")

    log("=" * 50)
    if found:
        log(f"✅ 验收通过：实例已注册 id={found.get('id')} name={found.get('name')!r} "
            f"python={found.get('python_path')}")
    else:
        log(f"❌ 未在 config.json 找到 {TARGET_DIR} 的注册实例（detail={_state.get('detail')}）")
    sys.exit(0 if found else 1)


if __name__ == "__main__":
    main()
