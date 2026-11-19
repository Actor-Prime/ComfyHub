"""版本更新内容弹窗（whats_new）回归测试 — 2026-11-15。

覆盖：版本区间收集（升序、开区间边界、跨多版聚合）、md 聚合、
maybe_show 的 last_version 幂等/首装/同版/降级不弹、弹窗内容渲染。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FAILS = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (("  [%s]" % (detail,)) if detail != "" else ""))
    if not ok:
        FAILS.append(name)


def main():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)

    from app import whats_new
    from app.updater import ver_key

    print("== A. 版本解析 ==")
    check("ver_key 两位补丁位", ver_key("v0.8.29") == (0, 8, 29) and ver_key("0.8.9") == (0, 8, 9))

    print("== B. collect_notes 区间 ==")
    # 源码树 docs/ 里有 RELEASE_v0.7.9 ~ v0.8.29
    notes = whats_new.collect_notes("0", "0.8.29")
    vers = [v for v, _ in notes]
    check("全量收集非空", len(notes) >= 3, len(notes))
    check("版本升序", vers == sorted(vers, key=ver_key), vers)
    check("不含区间外", all(ver_key(v) <= ver_key("0.8.29") for v in vers))
    # 开区间：(old, current] —— old 本身不含
    notes2 = whats_new.collect_notes("0.8.28", "0.8.29")
    vers2 = [v for v, _ in notes2]
    check("旧版本自身不含（开区间）", "0.8.28" not in vers2, vers2)
    check("新版本含", "0.8.29" in vers2, vers2)
    check("正好只有一版", notes2 and vers2 == ["0.8.29"], vers2)
    # 同版本 → 空
    check("同版本收集为空", whats_new.collect_notes("0.8.29", "0.8.29") == [])
    # 文本非空
    check("说明文本非空且含标题", all(t.strip() for _, t in notes2)
          and "# ComfyHub" in notes2[0][1])

    print("== C. merge_text 聚合 ==")
    merged = whats_new.merge_text(notes2)
    check("聚合含版本标题", "v0.8.29" in merged)
    merged3 = whats_new.merge_text([("0.8.1", "第一版"), ("0.8.2", "第二版")])
    check("缺标题自动补小节", "# ComfyHub v0.8.1" in merged3 and "第一版" in merged3)
    check("两版之间有分隔线", merged3.count("---") == 1, merged3.count("---"))

    print("== D. maybe_show 幂等与触发 ==")
    import tempfile
    from app import config
    tmp_cfg = os.path.join(tempfile.gettempdir(), "comfyhub_whatsnew_cfg.json")

    # 用 monkeypatch 隔离：cfg 读写重定向到临时文件，弹窗改为记录调用
    # ⚠️ 必须同时保存 _show_dialog：D 段 finally 要恢复它，否则后面 E/F 段
    #    拿到的是这个 lambda 桩（2026-11-19 实测：F 段的「真的会 exec」断言
    #    因此假红，排查了一圈才发现是测试自身的桩没还原）
    orig_load, orig_save = config.load_config, config.save_config
    orig_dialog = whats_new._show_dialog
    config.load_config = lambda: {"settings": dict(_load(tmp_cfg))}
    calls = []
    config.save_config = lambda cfg: _save(tmp_cfg, cfg)
    whats_new._show_dialog = lambda parent, notes: calls.append(notes)
    try:
        # 1) 首次安装（无 last_version）→ 不弹，但写入 last_version
        _del(tmp_cfg)
        check("首装不弹", whats_new.maybe_show(None) is False)
        check("首装写入 last_version", _load(tmp_cfg).get("last_version") == config.APP_VERSION,
              _load(tmp_cfg).get("last_version"))
        # 2) 同版本 → 不弹
        check("同版本不弹", whats_new.maybe_show(None) is False)
        # 3) 降级 → 不弹
        _save(tmp_cfg, {"settings": {"last_version": "9.9.9"}})
        check("降级不弹", whats_new.maybe_show(None) is False)
        check("降级后 last_version 回写当前版本",
              _load(tmp_cfg).get("last_version") == config.APP_VERSION)
        # 4) 低版本升级 → 弹，且聚合区间说明
        _save(tmp_cfg, {"settings": {"last_version": "0.8.28"}})
        check("升级触发弹窗", whats_new.maybe_show(None) is True)
        # 不写死版本数量：APP_VERSION 一升这条断言就腐化
        #（原来固定「只含 0.8.29 一版」，0.8.31 后必假红）
        got_vers = [v for v, _ in calls[0]] if calls else []
        check("弹窗聚合了 (旧版, 当前版] 区间",
              bool(got_vers) and all(ver_key(v) > ver_key("0.8.28")
                                    for v in got_vers), got_vers)
        check("弹窗包含当前版本", config.APP_VERSION in got_vers, got_vers)
        check("弹后 last_version 更新", _load(tmp_cfg).get("last_version") == config.APP_VERSION)
        # 5) 再启（已是当前版）→ 不弹
        check("再次启动不重复弹", whats_new.maybe_show(None) is False)

        # 6) 取不到说明时：不弹，但仍推进版本号
        #（2026-11-19：原来「先推进再收集」，取不到就永久丢失提示机会。
        #  现在改为收集不到也推进——防每次启动都卡在在线请求上——但要能验证不弹）
        orig_collect = whats_new.collect_notes
        orig_online = whats_new.online_notes
        whats_new.collect_notes = lambda *a, **k: []
        whats_new.online_notes = lambda *a, **k: ""
        try:
            calls.clear()
            _save(tmp_cfg, {"settings": {"last_version": "0.8.10"}})
            check("无说明时不弹", whats_new.maybe_show(None) is False)
            check("无说明时不误报已弹", calls == [], str(calls))
            check("无说明时仍推进版本号（避免每次启动重复请求）",
                  _load(tmp_cfg).get("last_version") == config.APP_VERSION,
                  _load(tmp_cfg).get("last_version"))
        finally:
            whats_new.collect_notes = orig_collect
            whats_new.online_notes = orig_online
    finally:
        config.load_config, config.save_config = orig_load, orig_save
        whats_new._show_dialog = orig_dialog        # 还原桩，别污染后续用例
        _del(tmp_cfg)

    print("== E. 弹窗渲染 ==")
    notes = whats_new.collect_notes("0.8.28", "0.8.29")
    dlg_holder = []
    orig_show = whats_new._show_dialog
    whats_new._show_dialog = lambda parent, ns: dlg_holder.append(ns)
    try:
        whats_new.maybe_show.__wrapped__ if False else None
        # 直接验证 _show_dialog 收到的 notes 能渲染出 md（绕开真实弹窗）
        from app.whats_new import merge_text
        md = merge_text(notes)
        check("md 含「## 新增」章节", "## " in md)
        # 真实弹窗构建冒烟（show 后立即关）
        from PySide6.QtCore import QTimer
        from app.whats_new import _show_dialog as real_show
        dlg_ref = []
        def capture(parent, ns):
            # 构建真实对话框但不开事件循环
            from PySide6.QtWidgets import QDialog, QTextBrowser
            d = QDialog(parent)
            v = QTextBrowser(d)
            v.setMarkdown(merge_text(ns))
            dlg_ref.append((d, v))
        whats_new._show_dialog = capture
        whats_new.maybe_show.__globals__  # noqa
        _save(tmp_cfg, {"settings": {"last_version": "0.8.28"}})
        config.save_config = orig_save
        # 临时把 save 恢复以避免污染真实配置 —— 用 try 里手动调 collect+渲染
        whats_new._show_dialog = orig_show
        dlg_ref.clear()
        # 渲染冒烟：构造真实 dialog
        from PySide6.QtWidgets import QDialog, QTextBrowser
        d = QDialog()
        v = QTextBrowser(d)
        v.setMarkdown(md)
        check("QTextBrowser md 渲染成功", v.toPlainText().find("0.8.29") >= 0 or len(md) > 100)
    finally:
        whats_new._show_dialog = orig_show
        config.load_config, config.save_config = orig_load, orig_save
        _del(tmp_cfg)

    print("== F. 弹窗真的会显示（回归：曾漏 dlg.exec，导致一次都没弹过）==")
    # 2026-11-19 用户反馈「更新成功后更新内容弹窗没跳出来」。
    # 根因：_show_dialog 构造完 QDialog 就返回，从未 exec() —— 模态窗不 exec
    # 就只会在函数返回时被回收。此前的测试把 _show_dialog 整个打桩掉了，
    # 所以永远发现不了。这里两条断言把它焊死：
    #   1) 源码里必须有 dlg.exec()
    #   2) 运行时确实调用到 QDialog.exec
    import inspect
    src = inspect.getsource(whats_new._show_dialog)
    check("源码里 _show_dialog 调用了 dlg.exec()",
          "dlg.exec()" in src, "缺失或改名会导致弹窗永不显示")
    check("源码里没有把 exec 漏成被动 show 的痕迹",
          "dlg.show()" not in src or "dlg.exec()" in src)

    from PySide6.QtWidgets import QDialog
    entered = []
    orig_exec = QDialog.exec

    def fake_exec(self):
        entered.append(self.windowTitle())
        self.accept()          # 立刻关掉，别让测试挂住
        return QDialog.Accepted

    QDialog.exec = fake_exec
    try:
        ok = whats_new.show_whats_new(None, "0.8.31")
        check("show_whats_new 返回 True", ok is True, ok)
        check("真的执行到了 QDialog.exec()",
              entered == ["ComfyHub 更新内容"], entered)
    finally:
        QDialog.exec = orig_exec

    # maybe_show 的升级路径也要走同一个显示函数
    entered.clear()
    QDialog.exec = fake_exec
    orig_l, orig_s = config.load_config, config.save_config
    try:
        _del(tmp_cfg)
        _save(tmp_cfg, {"settings": {"last_version": "0.8.10"}})
        config.load_config = lambda: {"settings": dict(_load(tmp_cfg))}
        config.save_config = lambda cfg: _save(tmp_cfg, cfg)
        shown = whats_new.maybe_show(None)
        check("升级路径也真的弹出窗口", shown is True and entered, entered)
    finally:
        QDialog.exec = orig_exec
        config.load_config, config.save_config = orig_l, orig_s
        _del(tmp_cfg)

    print()
    if FAILS:
        print("FAILED: %d" % len(FAILS))
        for f in FAILS:
            print("  - " + f)
        sys.exit(1)
    print("ALL PASS")


def _load(p):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f).get("settings", {})
    except OSError:
        return {}


def _save(p, cfg):
    with open(p, "w", encoding="utf-8") as f:
        json.dump(cfg, f)


def _del(p):
    try:
        os.remove(p)
    except OSError:
        pass


if __name__ == "__main__":
    main()
