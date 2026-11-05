# -*- coding: utf-8 -*-
"""反馈与诊断（app/feedback.py + 设置页 FeedbackDialog）回归
2026-11-06 方案A：本地收集 + 脱敏 + 零网络回传"""
import json
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PASS = 0


def check(name, ok, detail=""):
    global PASS
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        sys.exit(1)
    PASS += 1


from app.feedback import sanitize_text, collect, issues_url  # noqa: E402

# ---- 1. 脱敏 ----
home = os.path.expanduser("~")
s = sanitize_text(f"模型在 {home}\\models 下，配置在 {os.environ.get('APPDATA')}\\ComfyHub")
check("1a 用户主目录脱敏", "%USER%" in s and home not in s, s)
check("1b APPDATA 脱敏", "%APPDATA%" in s, s)

s = sanitize_text("api_key = sk-abcdef1234567890\npassword: hunter2\ntoken\t=\tghp_xyz123")
check("1c api_key 整值打码", "sk-abcdef" not in s and "***" in s, s)
check("1d password 打码", "hunter2" not in s, s)
check("1e token 打码", "ghp_xyz" not in s, s)

s = sanitize_text("Authorization: Bearer eyJhbGciOi.JKSDF.aaa")
check("1f Bearer 打码", "eyJhbGciOi" not in s, s)

s = sanitize_text("正常日志行不动：epoch 3 loss 0.1")
check("1g 普通行原样", s == "正常日志行不动：epoch 3 loss 0.1", s)

# ---- 2. 收集 ----
report = collect()
check("2a 报告含版本头", "===== ComfyHub 诊断信息" in report and "版本: v" in report)
check("2b 报告含硬件段", "CPU: " in report and "显卡: " in report)
check("2c 报告含实例段与日志段", "---- 实例配置 ----" in report and "app.log" in report)
check("2d 报告正常收尾", report.rstrip().endswith("==== 诊断信息结束 ===="))
check("2e 报告无真实用户名泄漏", home not in report,
      f"home={home!r}")
node = __import__("platform").uname().node
check("2f 报告无机器名", (not node) or (node not in report), node)

# ---- 3. 真实 API Key 反扫（铁律：任何收集/导出功能上线前必测）----
cfg_path = os.path.join(os.environ.get("APPDATA", ""), "ComfyHub", "config.json")
if os.path.isfile(cfg_path):
    try:
        st = (json.load(open(cfg_path, encoding="utf-8")).get("settings") or {})
        key = (st.get("ai_api_key") or "").strip()
    except Exception:
        key = ""
    if key:
        check("3a 真实 key 不出现在报告", key not in report)
        check("3b 真实 key 不在词表/源码", key not in
              pathlib.Path("app/feedback.py").read_text(encoding="utf-8"))
    else:
        print("SKIP 3a/3b（本机配置无 key）")
else:
    print("SKIP 3a/3b（无真实 config）")

# ---- 4. Issues 链接 ----
u = issues_url()
check("4a 直达 Gitee 新建页", "gitee.com/" in u and "/issues/new" in u, u)
check("4b 标题预填版本", "title=" in u and "v" in u.split("title=")[1], u)

# ---- 5. 对话框可实例化（offscreen）----
from PySide6.QtWidgets import QApplication  # noqa: E402
app = QApplication.instance() or QApplication([])
from app.ui.pages.settings_page import FeedbackDialog  # noqa: E402
os.environ["COMFYHUB_FEEDBACK_ENDPOINT"] = ""     # 显式关闭通道，验“未配置即隐藏”
d = FeedbackDialog()
check("5a 对话框生成文本非空且已脱敏",
      len(d.txt.toPlainText()) > 200 and home not in d.txt.toPlainText())
d._copy()
check("5b 复制按钮写入剪贴板", app.clipboard().text() == d.txt.toPlainText())
check("5c 未配置地址时提交按钮隐藏", d.b_submit.isHidden() and d.ed_contact.isHidden())
d.close()
os.environ.pop("COMFYHUB_FEEDBACK_ENDPOINT", None)

# ---- 6. 匿名提交链路（本地假云函数，端到端）----
import http.server, socketserver, threading  # noqa: E402
from app.feedback import submit, MAX_REPORT_BYTES  # noqa: E402


class _H(http.server.BaseHTTPRequestHandler):
    bodies = []

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        _H.bodies.append(self.rfile.read(n))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):     # 别把访问日志灌进测试输出
        pass


srv = socketserver.TCPServer(("127.0.0.1", 0), _H)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()

os.environ["COMFYHUB_FEEDBACK_ENDPOINT"] = f"http://127.0.0.1:{port}/submit"
ok, msg = submit("测试诊断内容" * 10, "user@example.com")
check("6a 正常提交返回成功", ok is True, msg)
check("6b 服务端收到一条 POST", len(_H.bodies) == 1)
body = _H.bodies[0].decode("utf-8")
check("6c 载荷含版本与报告", "version" in body and "测试诊断内容" in body, body[:200])
check("6d 载荷含选填联系方式", "user@example.com" in body)

# 用户描述与诊断报告分开传（邮件里描述在前，好读）
submit("诊断报告正文" * 5, "c@example.com", "启动后一直显示未运行")
body_msg = _H.bodies[-1].decode("utf-8")
check("6d1 用户描述单独成字段",
      '"message"' in body_msg.replace(" ", "") and "启动后一直显示未运行" in body_msg, body_msg[:200])

# Web3Forms 适配：配了公开 access key 时自动带进载荷（key 设计上可公开）
os.environ["COMFYHUB_FEEDBACK_KEY"] = "demo-public-key"
submit("测试诊断内容" * 5)
body_key = _H.bodies[-1].decode("utf-8")
check("6d2 配置公开 key 后自动附带", "access_key" in body_key and "demo-public-key" in body_key)
check("6d3 自动补 subject", "subject" in body_key and "ComfyHub" in body_key)
os.environ.pop("COMFYHUB_FEEDBACK_KEY", None)

ok, msg = submit("x" * (MAX_REPORT_BYTES + 1000))
check("6e 超长报告被截断后仍能提交", ok is True and len(_H.bodies[-1]) <= MAX_REPORT_BYTES + 4000,
      str(len(_H.bodies[-1])))

# 失败路径：指向一个必然拒绝的端口
os.environ["COMFYHUB_FEEDBACK_ENDPOINT"] = "http://127.0.0.1:1/submit"
ok, msg = submit("hello")
check("6f 网络失败不抛异常且返回 False", ok is False and msg, msg)

# 未配置地址
os.environ["COMFYHUB_FEEDBACK_ENDPOINT"] = ""
ok, msg = submit("hello")
check("6g 未配置地址直接拒绝", ok is False and "未配置" in msg, msg)

# ---- 7. 有地址时按钮可见 + 提交失败自动落盘 ----
os.environ["COMFYHUB_FEEDBACK_ENDPOINT"] = f"http://127.0.0.1:{port}/submit"
d2 = FeedbackDialog()
check("7a 配置地址后提交按钮与联系框可见",
      not d2.b_submit.isHidden() and not d2.ed_contact.isHidden())

d2.ed_msg.setPlainText("")
d2._submit()                       # 空描述：应本地拦截，不发请求
check("7a2 描述为空时拦截提交",
      "请先填写" in d2.lb_state.text() and d2.b_submit.isEnabled(), d2.lb_state.text())
d2.ed_msg.setPlainText("启动后一直显示未运行，要手动刷新")
check("7a3 导出文本包含用户描述（描述在前）",
      d2._full_text().startswith("启动后一直显示未运行"), d2._full_text()[:40])
import glob  # noqa: E402
before = set(glob.glob(os.path.join(os.path.expanduser("~"), "Desktop",
                                    "ComfyHub_feedback_*.txt")))
d2._on_submitted(False, "TimeoutError: 连接超时")
after = set(glob.glob(os.path.join(os.path.expanduser("~"), "Desktop",
                                   "ComfyHub_feedback_*.txt")))
new_files = after - before
check("7b 提交失败自动保存到文件", len(new_files) == 1 and "已自动保存到" in d2.lb_state.text(),
      d2.lb_state.text())
if new_files:
    os.remove(new_files.pop())
d2.close()
srv.shutdown()
os.environ.pop("COMFYHUB_FEEDBACK_ENDPOINT", None)

# ---- 8. 单机防刷（隔离 APPDATA，别污染真实配置）----
import tempfile, shutil, time as _t  # noqa: E402
tmp_home = tempfile.mkdtemp(prefix="ch_quota_")
old_appdata = os.environ.get("APPDATA")
os.environ["APPDATA"] = tmp_home

from app.feedback import quota_check, quota_record, _quota_path  # noqa: E402

check("8a 首次提交放行", quota_check("报告A")[0] is True)
quota_record("报告A")
ok_dup, why_dup = quota_check("报告A")
check("8b 同内容重复提交被拦", ok_dup is False and "重复" in why_dup, why_dup)
ok_fast, why_fast = quota_check("报告B（换个问题）")
check("8c 60 秒内再提交被拦", ok_fast is False and "秒后再试" in why_fast, why_fast)

# 状态文件里只能有哈希/时间，不能有正文（隐私）
raw_state = open(_quota_path(), encoding="utf-8").read()
check("8d 状态文件不含反馈正文", "报告A" not in raw_state, raw_state[:80])

import json  # noqa: E402
json.dump({"sent": [{"ts": _t.time() - (i + 2) * 300, "h": f"h{i}"} for i in range(3)]},
          open(_quota_path(), "w", encoding="utf-8"))
ok_day, why_day = quota_check("报告C")
check("8e 每日 3 条上限", ok_day is False and "今天" in why_day, why_day)

json.dump({"sent": [{"ts": _t.time() - (i + 2) * 86400, "h": f"m{i}"} for i in range(10)]},
          open(_quota_path(), "w", encoding="utf-8"))
ok_mon, why_mon = quota_check("报告D")
check("8f 30 天 10 条上限", ok_mon is False and "30 天" in why_mon, why_mon)

os.environ["APPDATA"] = old_appdata if old_appdata is not None else ""
if old_appdata is None:
    os.environ.pop("APPDATA", None)
shutil.rmtree(tmp_home, ignore_errors=True)

print(f"共 {PASS} 项，全部通过")
