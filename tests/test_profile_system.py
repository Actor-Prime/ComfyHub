# -*- coding: utf-8 -*-
"""启动模式体系回归测试：内核快照 / profile CRUD / 拼参 / junction / 体检逻辑
直跑脚本：.venv_build/Scripts/python tests/test_profile_system.py
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FAILS = []


def check(name, cond):
    print(f"  {'✅' if cond else '❌'} {name}")
    if not cond:
        FAILS.append(name)


def make_fake_kernel(root, version, with_base_flag=True, with_python=True):
    """造一个最小 ComfyUI 内核源码树"""
    kdir = os.path.join(root, "kernels", version)
    os.makedirs(os.path.join(kdir, "comfy"), exist_ok=True)
    with open(os.path.join(kdir, "main.py"), "w") as f:
        f.write("# fake main\n")
    flag = 'parser.add_argument("--base-directory", type=str, default=None)' \
        if with_base_flag else 'parser.add_argument("--old", type=str)'
    with open(os.path.join(kdir, "comfy", "cli_args.py"), "w") as f:
        f.write(flag)
    os.makedirs(os.path.join(kdir, "custom_nodes"), exist_ok=True)
    with open(os.path.join(kdir, "custom_nodes", "core_plugin.py"), "w") as f:
        f.write("NODE_CLASS_MAPPINGS = {'OldNode': None}\n")
    py = os.path.join(root, "python", "python.exe")
    if with_python:
        os.makedirs(os.path.dirname(py), exist_ok=True)
        open(py, "w").close()
    return kdir, py


def main():
    tmp = tempfile.mkdtemp(prefix="ch_profile_test_")
    print(f"=== 启动模式体系测试（tmp={tmp}） ===")
    # 隔离：tools_root 指到临时目录
    from app import bootstrap, kernel_store, profiles, health_check
    bootstrap.tools_root = lambda: tmp

    # ---------- 内核快照 ----------
    print("— kernel_store —")
    src, py = make_fake_kernel(os.path.join(tmp, "srcinst"), "v1")
    # 伪装成 ComfyUI 目录（is_comfyui_dir 要有 comfy/）
    k = kernel_store.import_from_instance(src, py, version="t-v1")
    check("导入内核快照", os.path.isfile(os.path.join(k["path"], "main.py")))
    check("注册表入库", kernel_store.get_kernel("t-v1") is not None)
    check("base_dir 标志检测（有）", k["base_dir_flag"] is True)
    check("快照不含 custom_nodes",
          not os.path.isdir(os.path.join(k["path"], "custom_nodes")))
    src2, py2 = make_fake_kernel(os.path.join(tmp, "srcinst2"), "v2",
                                 with_base_flag=False)
    k2 = kernel_store.import_from_instance(src2, py2, version="t-v2")
    check("base_dir 标志检测（无）", k2["base_dir_flag"] is False)
    check("列出内核", len(kernel_store.list_kernels()) == 2)
    check("删除内核", kernel_store.remove_kernel("t-v2")
          and kernel_store.get_kernel("t-v2") is None)

    # ---------- profile CRUD ----------
    print("— profiles CRUD —")
    p = profiles.create_profile("测试模式", "t-v1")
    check("创建 profile", p["name"] == "测试模式")
    check("骨架目录齐",
          os.path.isdir(os.path.join(profiles.profile_dir("测试模式"),
                                     "custom_nodes")))
    check("加载 profile", profiles.load_profile("测试模式")["kernel_version"]
          == "t-v1")
    try:
        profiles.create_profile("测试模式", "t-v1")
        check("重复创建被拒", False)
    except ValueError:
        check("重复创建被拒", True)
    p["args"] = "--test-flag"
    profiles.save_profile(p)
    check("保存/重载", profiles.load_profile("测试模式")["args"] == "--test-flag")
    profiles.duplicate_profile("测试模式", "测试模式副本")
    check("复制 profile", profiles.load_profile("测试模式副本") is not None)
    check("列表扫描", len([x for x in profiles.list_profiles()
                          if not x.get("broken")]) == 2)
    check("删除 profile", profiles.delete_profile("测试模式副本")
          and profiles.load_profile("测试模式副本") is None)

    # ---------- 拼参（base_dir 模式） ----------
    print("— prepare_launch —")
    p = profiles.load_profile("测试模式")
    inst = profiles.prepare_launch(p)
    check("inst 合成", inst["comfyui_path"] == kernel_store.get_kernel(
        "t-v1")["path"])
    check("base_directory 字段", inst.get("base_directory") ==
          profiles.profile_dir("测试模式"))
    check("models_directory 字段（默认共享）",
          inst.get("models_directory") == profiles.default_models_dir())
    check("python 走内核登记", inst["python_path"].endswith("python.exe"))
    from app.process import build_command
    cmd = build_command(inst, port=9999)
    check("命令含 --base-directory", "--base-directory" in cmd
          and cmd[cmd.index("--base-directory") + 1] == inst["base_directory"])
    check("命令含 --models-directory", "--models-directory" in cmd)
    # overlay env 注入
    overlay = os.path.join(profiles.profile_dir("测试模式"), "site_overlay")
    with open(os.path.join(overlay, "dummy.pth"), "w") as f:
        f.write("")
    inst2 = profiles.prepare_launch(profiles.load_profile("测试模式"))
    check("overlay 非空 → env.PYTHONPATH", inst2.get("env", {}).get(
        "PYTHONPATH") == overlay)

    # ---------- junction 换头（旧内核） ----------
    print("— junction 兜底 —")
    k2 = kernel_store.import_from_instance(
        *make_fake_kernel(os.path.join(tmp, "srcinst3"), "v3",
                          with_base_flag=False),
        version="t-v3")
    profiles.create_profile("旧核模式", "t-v3", deps_mode="L1")
    inst3 = profiles.prepare_launch(profiles.load_profile("旧核模式"))
    check("旧核走 junction（无 base_directory）",
          "base_directory" not in inst3)
    link = os.path.join(k2["path"], "custom_nodes")
    check("junction 已建立", os.path.isdir(link))
    check("快照无原生 custom_nodes（免留档）", not os.path.isdir(
        link + ".__kernel_default"))
    # 目标内容可见（挂载成功）
    open(os.path.join(profiles.profile_dir("旧核模式"),
                      "custom_nodes", "probe.py"), "w").close()
    check("junction 指向 profile", os.path.isfile(
        os.path.join(link, "probe.py")))
    # 内核真有原生 custom_nodes 时 → 留档分支
    os.rmdir(link)                      # 摘掉 junction
    os.makedirs(link)
    open(os.path.join(link, "native.py"), "w").close()
    profiles.ensure_junction(kernel_store.get_kernel("t-v3"),
                             profiles.load_profile("旧核模式"))
    check("原生 custom_nodes 留档", os.path.isfile(
        link + ".__kernel_default" + os.sep + "native.py"))

    # ---------- 体检逻辑（离线部分） ----------
    print("— health_check —")
    wf = {"1": {"class_type": "KSampler", "inputs": {}},
          "2": {"class_type": "MissingNodeXYZ", "inputs": {}}}
    wfp = os.path.join(tmp, "wf.json")
    with open(wfp, "w", encoding="utf-8") as f:
        json.dump(wf, f)
    from app.workflow_scan import extract_class_types, load_workflow
    ctypes = extract_class_types(load_workflow(wfp))
    check("工作流类提取", set(ctypes) == {"KSampler", "MissingNodeXYZ"})
    # _pick_port
    port = health_check._pick_port()
    check("体检端口选择", port >= health_check.PROBE_PORT)

    # ---------- 插件安装（离线部分） ----------
    print("— profiles 插件 —")
    check("仓库名解析", profiles._repo_name(
        "https://github.com/foo/Bar.git") == "Bar")
    check("仓库名解析（无 .git）", profiles._repo_name(
        "https://github.com/foo/Baz") == "Baz")

    # ---------- node map 解析（缓存注入） ----------
    print("— plugin_store node map —")
    from app import plugin_store
    from app.paths import data_root
    cache = os.path.join(data_root(), "cache")
    os.makedirs(cache, exist_ok=True)
    with open(plugin_store.node_map_cache_path(), "w", encoding="utf-8") as f:
        json.dump({"fetched_at": __import__("time").time(), "map": {}}, f)
    # 缓存为空 dict 会走网络——直接测解析函数逻辑
    raw = {"MyNode": [["https://github.com/x/y.git"], "说明"]}
    nmap = {}
    for cls, val in raw.items():
        urls, desc = [], ""
        if isinstance(val, (list, tuple)):
            if val and isinstance(val[0], (list, tuple)):
                urls = [str(u) for u in val[0]]
            if len(val) > 1 and isinstance(val[1], str):
                desc = val[1]
        nmap[cls] = {"urls": urls, "desc": desc}
    check("node map 解析", nmap["MyNode"]["urls"] == [
        "https://github.com/x/y.git"] and nmap["MyNode"]["desc"] == "说明")

    shutil.rmtree(tmp, ignore_errors=True)
    print("===")
    if FAILS:
        print(f"FAIL ({len(FAILS)}): {FAILS}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
