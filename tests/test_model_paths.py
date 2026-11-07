# -*- coding: utf-8 -*-
"""模型目录解析回归（2026-11-07）

覆盖：extra_model_paths.yaml 极简解析 / 相对与绝对路径 / custom_nodes 跳过 /
内置+外部合并 / unet 与 diffusion_models 别名互兜 / 缺文件不炸。

用法：.venv_build/Scripts/python.exe tests/test_model_paths.py
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import model_paths as MP  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))


def touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "wb").write(b"0")


def main():
    root = tempfile.mkdtemp(prefix="ch_mp_")
    ext = tempfile.mkdtemp(prefix="ch_ext_")
    try:
        comfy = os.path.join(root, "ComfyUI")
        os.makedirs(os.path.join(comfy, "models", "checkpoints"))
        os.makedirs(os.path.join(comfy, "models", "loras"))
        touch(os.path.join(comfy, "models", "checkpoints", "a.safetensors"))
        touch(os.path.join(comfy, "models", "loras", "b.safetensors"))
        # 外部库：相对路径 + 绝对路径各一
        os.makedirs(os.path.join(ext, "diffusion_models"))
        os.makedirs(os.path.join(ext, "vae"))
        os.makedirs(os.path.join(ext, "te_abs"))
        touch(os.path.join(ext, "diffusion_models", "c.safetensors"))
        touch(os.path.join(ext, "vae", "d.safetensors"))
        touch(os.path.join(ext, "te_abs", "e.safetensors"))

        yml = os.path.join(comfy, "extra_model_paths.yaml")
        with open(yml, "w", encoding="utf-8") as f:
            f.write(
                "# 注释行应被忽略\n"
                "my_ext:\n"
                f"    base_path: {ext.replace(os.sep, '/')}\n"
                "    diffusion_models: diffusion_models\n"
                "    vae: vae\n"
                f"    text_encoders: {os.path.join(ext, 'te_abs').replace(os.sep, '/')}\n"
                "    custom_nodes: cn_should_skip\n"
                "    is_default: true\n"
            )

        # 1. 解析
        kv = MP.parse_extra_paths(yml)
        check("1 解析出段与子键", "my_ext" in kv and kv["my_ext"].get("vae") == "vae", str(kv))
        check("1b 注释被忽略", all(not k.startswith("#") for k in kv))

        # 2. 外部目录（相对拼 base_path，绝对原样，custom_nodes 不进来）
        ed = MP.extra_model_dirs(comfy)
        check("2 相对路径拼到 base_path",
              ed.get("diffusion_models") == [os.path.join(ext, "diffusion_models")], str(ed))
        check("2b 绝对路径原样使用",
              ed.get("text_encoders") == [os.path.normpath(os.path.join(ext, "te_abs"))], str(ed))
        check("2c custom_nodes 不当模型目录", "custom_nodes" not in ed, str(list(ed)))

        # 3. 合并：内置在前、外部在后
        md = MP.model_dirs(comfy)
        check("3 内置类别在位",
              md.get("checkpoints") == [os.path.join(comfy, "models", "checkpoints")], str(md))
        check("3b 外部类别并入", "vae" in md and len(md["vae"]) == 1)

        # 4. 别名互兜
        check("4 unet 能扫到 diffusion_models 的目录",
              MP.dirs_for(comfy, "unet") == [os.path.join(ext, "diffusion_models")],
              str(MP.dirs_for(comfy, "unet")))
        check("4b 内置 unet 与外部共存",
              len(MP.dirs_for(comfy, "diffusion_models")) == 1)

        # 5. 外部根目录提示
        check("5 extra_roots 报出外部库", MP.extra_roots(comfy) == [os.path.normpath(ext)],
              str(MP.extra_roots(comfy)))

        # 6. 缺配置/空路径不炸
        empty = tempfile.mkdtemp(prefix="ch_empty_")
        check("6 没有配置文件返回空", MP.extra_roots(empty) == [] and MP.model_dirs(empty) == {})
        check("6b 空路径不炸", MP.model_dirs("") == {} and MP.dirs_for("", "unet") == [])
        shutil.rmtree(empty, ignore_errors=True)

        # 7. 坏 YAML 不抛
        bad = os.path.join(comfy, "user", "default")
        os.makedirs(bad, exist_ok=True)
        open(os.path.join(bad, "extra_model_paths.yaml"), "w", encoding="utf-8").write(
            "!!!\n  ::\n\tbroken")
        check("7 坏配置不抛异常", isinstance(MP.extra_model_dirs(comfy), dict))

        # 8. 实例设置里的「模型目录」（2026-11-07：这项原来存了没人读，等于摆设）
        custom_root = tempfile.mkdtemp(prefix="ch_root_")
        os.makedirs(os.path.join(custom_root, "vae"))
        touch(os.path.join(custom_root, "vae", "f.safetensors"))
        check("8 自定义模型根优先于 <comfyui>/models",
              MP.model_root(comfy, custom_root) == os.path.normpath(custom_root))
        check("8b 自定义根下的类别能被扫到",
              MP.dirs_for(comfy, "vae", custom_root)[0] == os.path.join(custom_root, "vae"),
              str(MP.dirs_for(comfy, "vae", custom_root)))
        check("8c 空值回落默认 models",
              MP.model_root(comfy, "") == os.path.join(comfy, "models"))

        # 9. 启动命令：只有跟默认根不同才传 --models-directory
        from app.process import build_command
        base_inst = {"comfyui_path": comfy, "python_path": "python.exe", "port": 8188}
        check("9 默认根不传 --models-directory",
              "--models-directory" not in build_command(base_inst),
              str(build_command(base_inst)))
        cmd2 = build_command(dict(base_inst, models_dir=custom_root))
        check("9b 自定义根会传 --models-directory",
              "--models-directory" in cmd2 and custom_root in cmd2, str(cmd2[-3:]))
        shutil.rmtree(custom_root, ignore_errors=True)
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(ext, ignore_errors=True)

    bad = [r for r in RESULTS if not r[1]]
    print(f"\n== {len(RESULTS) - len(bad)}/{len(RESULTS)} 通过 ==")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
