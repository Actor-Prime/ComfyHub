# -*- coding: utf-8 -*-
"""UI 状态持久化（2026-11-21：注册表 → 数据目录 INI）

背景：客户卸载后用 Geek 扫出残留 `HKCU\\Software\\LeiMengStudio\\ComfyHub`。
根因是 QSettings("LeiMengStudio","ComfyHub") 把界面偏好写进注册表，而卸载脚本
只删安装器自己的 `Software\\ComfyHub`。改成写数据目录内的 ui_state.ini 后，
卸载删数据目录即一并归零；老用户首次启动自动迁移旧值并删除旧键。

本测试全程用临时目录 + 测试专用注册表键名，不碰真实配置。
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FAILS = []


def check(name, cond, extra=""):
    tag = "PASS" if cond else "FAIL"
    print("%s  %s %s" % (tag, name, extra))
    if not cond:
        FAILS.append(name)


TEST_ORG = "LmsUiStateTest"
TEST_APP = "ComfyHubTest"


def _reset(ui_state, tmp):
    """把模块指到临时数据目录，并重置一次性迁移标记"""
    ui_state.data_root = lambda: tmp
    ui_state._ready = False


def _reg_key_exists(path):
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path)
        winreg.CloseKey(k)
        return True
    except OSError:
        return False


def _reg_cleanup():
    try:
        import winreg
        for sub in ("Software\\%s\\%s" % (TEST_ORG, TEST_APP),
                    "Software\\%s" % TEST_ORG):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)
            except OSError:
                pass
    except Exception:
        pass


def main():
    from app import ui_state

    ui_state._LEGACY_ORG = TEST_ORG
    ui_state._LEGACY_APP = TEST_APP

    # ---------- A. 基本读写 ----------
    print("== A. 基本读写 ==")
    tmp = tempfile.mkdtemp(prefix="ui_state_a_")
    try:
        _reset(ui_state, tmp)
        check("缺失键返回默认值", ui_state.get("nope", "dflt") == "dflt")
        ui_state.set_value("model_scope", "local")
        check("写入后可读回", ui_state.get("model_scope") == "local")
        check("INI 落在数据目录",
              os.path.exists(os.path.join(tmp, "ui_state.ini")))
        check("未写别的文件",
              sorted(os.listdir(tmp)) == ["ui_state.ini"],
              str(sorted(os.listdir(tmp))))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ---------- B. 布尔解析 ----------
    print("== B. 布尔解析 ==")
    tmp = tempfile.mkdtemp(prefix="ui_state_b_")
    try:
        _reset(ui_state, tmp)
        ui_state.set_value("r1", True)
        check("True 回读为 True", ui_state.get("r1", False, as_bool=True) is True)
        ui_state.set_value("r2", False)
        check("False 回读为 False", ui_state.get("r2", True, as_bool=True) is False)
        ui_state.set_value("r3", "yes")
        check("字符串 yes 视为 True", ui_state.get("r3", False, as_bool=True) is True)
        ui_state.set_value("r4", "0")
        check("字符串 0 视为 False", ui_state.get("r4", True, as_bool=True) is False)
        check("缺失 + as_bool 用默认", ui_state.get("nope", True, as_bool=True) is True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ---------- C. 老注册表 → INI 迁移 ----------
    print("== C. 老注册表迁移 ==")
    _reg_cleanup()
    try:
        import winreg
        k = winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                             "Software\\%s\\%s" % (TEST_ORG, TEST_APP))
        winreg.SetValueEx(k, "model_scope", 0, winreg.REG_SZ, "all")
        winreg.SetValueEx(k, "ai_right_open", 0, winreg.REG_SZ, "true")
        winreg.CloseKey(k)
        check("测试前注册表键已就位",
              _reg_key_exists("Software\\%s\\%s" % (TEST_ORG, TEST_APP)))

        tmp = tempfile.mkdtemp(prefix="ui_state_c_")
        try:
            _reset(ui_state, tmp)
            # 第一次读就触发迁移
            check("迁移后读到老值", ui_state.get("model_scope") == "all")
            check("迁移出布尔值",
                  ui_state.get("ai_right_open", False, as_bool=True) is True)
            check("子键已从注册表删除",
                  not _reg_key_exists("Software\\%s\\%s" % (TEST_ORG, TEST_APP)))
            check("空的发布者父键也删了",
                  not _reg_key_exists("Software\\%s" % TEST_ORG))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    finally:
        _reg_cleanup()

    # ---------- D. 父键非空时不误删 ----------
    print("== D. 父键有别的产品 ==")
    try:
        import winreg
        k = winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                             "Software\\%s\\%s" % (TEST_ORG, TEST_APP))
        winreg.SetValueEx(k, "k", 0, winreg.REG_SZ, "v")
        winreg.CloseKey(k)
        other = winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                                 "Software\\%s\\OtherProduct" % TEST_ORG)
        winreg.SetValueEx(other, "x", 0, winreg.REG_SZ, "1")
        winreg.CloseKey(other)

        tmp = tempfile.mkdtemp(prefix="ui_state_d_")
        try:
            _reset(ui_state, tmp)
            ui_state.get("k")
            check("本产品子键被删",
                  not _reg_key_exists("Software\\%s\\%s" % (TEST_ORG, TEST_APP)))
            check("同发布者其它产品保留",
                  _reg_key_exists("Software\\%s\\OtherProduct" % TEST_ORG))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    finally:
        _reg_cleanup()

    # ---------- E. INI 已存在则不迁移覆盖 ----------
    print("== E. 已有 INI 不覆盖 ==")
    tmp = tempfile.mkdtemp(prefix="ui_state_e_")
    try:
        _reset(ui_state, tmp)
        ui_state.set_value("model_scope", "mid")
        # 再造一份老注册表值，重新走一次（_ready 复位）
        import winreg
        k = winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                             "Software\\%s\\%s" % (TEST_ORG, TEST_APP))
        winreg.SetValueEx(k, "model_scope", 0, winreg.REG_SZ, "old")
        winreg.CloseKey(k)
        ui_state._ready = False
        check("已有 INI 时不被老值覆盖",
              ui_state.get("model_scope") == "mid",
              str(ui_state.get("model_scope")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        _reg_cleanup()

    print()
    if FAILS:
        print("FAILED: %d" % len(FAILS))
        for f in FAILS:
            print("  - " + f)
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
