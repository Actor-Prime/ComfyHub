# -*- coding: utf-8 -*-
"""组件包国内加速源回归测试 — 2026-11-19

现场：用户反馈「组件卡在下载，一直显示 0M」。根因不是程序 bug，是**组件包只托管在
GitHub Releases**，国内直连实测 65 KB/s 且经常「连上但不给数据」——下载器 20 秒超时、
重试 8 次，全程进度 0。模型走 hf-mirror 不受影响，所以只有组件这一步会卡。

覆盖（离线，不真下载）：
    A. _is_github_release 判定（别把 HF / 普通链接误判）
    B. _endpoint_urls：GitHub 链接追加代理且代理排前；HF 链接不动；不重复
    C. 代理名单本身是 https、带结尾斜杠（拼接正确性）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.local_llm as L

FAILS = []
_N = [0]


def check(label, cond, detail=""):
    _N[0] += 1
    if cond:
        print("[PASS] %s" % label)
    else:
        FAILS.append("%s %s" % (label, detail))
        print("[FAIL] %s %s" % (label, detail))


def t_detect():
    """A. 判定哪些链接需要加速"""
    ok_cases = [
        "https://github.com/ggml-org/llama.cpp/releases/download/b12383/"
        "llama-b12383-bin-win-cpu-x64.zip",
        "https://github.com/foo/bar/releases/download/v1.0/x.zip",
    ]
    for u in ok_cases:
        check("识别 GitHub release: %s" % u.split("/")[-1][:24],
              L._is_github_release(u) is True)

    no_cases = [
        "https://hf-mirror.com/unsloth/Qwen3-VL-2B-Instruct-GGUF/resolve/main/x.gguf",
        "https://huggingface.co/foo/bar/resolve/main/x.gguf",
        "https://download.pytorch.org/whl/cu130/torch-2.9.1.whl",
        "https://github.com/ggml-org/llama.cpp",          # 仓库页，不是 release
        "",
    ]
    for u in no_cases:
        check("不误判: %s" % (u[:40] or "空串"),
              L._is_github_release(u) is False)


def t_endpoint_urls():
    """B. 源列表拼接"""
    gh = ("https://github.com/ggml-org/llama.cpp/releases/download/b12383/"
          "llama-b12383-bin-win-cpu-x64.zip")
    res = L._endpoint_urls([gh])
    check("GitHub 链接被扩展成多个源", len(res) > 1, len(res))
    check("官方源仍在列表里", gh in res)
    check("代理排在官方源之前",
          res.index(gh) == len(res) - 1, res)
    check("代理 URL 前缀正确",
          all(u.startswith("https://gh") for u in res[:-1]),
          res[:-1])
    check("每个代理都完整包含原始 URL",
          all(gh in u for u in res[:-1]))
    check("没有重复项", len(res) == len(set(res)), res)

    # HF 模型链接：只做镜像改写，不追加 GitHub 代理
    hf = "https://hf-mirror.com/foo/bar/resolve/main/x.gguf"
    res2 = L._endpoint_urls([hf])
    check("HF 链接不追加 GitHub 代理", len(res2) == 1, res2)
    check("HF 链接内容保持", res2[0] == hf, res2[0])

    # 混合列表
    res3 = L._endpoint_urls([hf, gh])
    check("混合列表里只有 GitHub 那项被扩展", len(res3) == 1 + len(res), len(res3))

    # 空输入
    check("空输入返回空列表", L._endpoint_urls([]) == [])
    check("None 输入返回空列表", L._endpoint_urls(None) == [])


def t_mirror_list():
    """C. 代理名单本身的约束"""
    check("代理名单非空", bool(L.GITHUB_MIRRORS))
    for m in L.GITHUB_MIRRORS:
        check("%s 是 https" % m, m.startswith("https://"))
        check("%s 以斜杠结尾（拼接才不出错）" % m, m.endswith("/"), m)


def t_bin_urls_wired():
    """D. 两个后端包真的都接上了代理（防止只改了一处）"""
    for key, pkg in L.BIN_VARIANTS.items():
        urls = L._endpoint_urls(pkg["urls"])
        check("%s 包的候选源多于 1 个" % key, len(urls) > 1, len(urls))
        check("%s 包的首个源是代理" % key,
              urls[0].startswith("https://gh"), urls[0][:40])
        # sha256 校验是代理能用的大前提：不校验就等于信任第三方
        check("%s 包有固定 sha256 兜底校验" % key,
              len(str(pkg.get("sha256") or "")) == 64, pkg.get("sha256"))


def main():
    print("=" * 62)
    t_detect()
    print("-" * 62)
    t_endpoint_urls()
    print("-" * 62)
    t_mirror_list()
    print("-" * 62)
    t_bin_urls_wired()
    print("=" * 62)
    print("结果: %d/%d 通过" % (_N[0] - len(FAILS), _N[0]))
    if FAILS:
        print("失败项：")
        for f in FAILS:
            print("  -", f)
        sys.exit(1)


if __name__ == "__main__":
    main()
