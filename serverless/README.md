# 反馈接收：两条路线（都不用公网 IP、不用买服务器）

> 2026-11-06 国内家宽实测：Cloudflare Workers、formsubmit.co **超时不可用**；
> Web3Forms、Formspree、金数据可达。

---

## 路线 A（最稳，推荐长期）：腾讯云函数 + 金数据

见本目录 `feedback_relay/README.md`。要点：

- 需注册腾讯云并**实名认证**（国内云的法律要求），**不需要服务器、不需要公网 IP、不用备案**
- 函数 URL 由平台分配，形如 `https://<id>.scf.tencentcs.com`
- 密钥（金数据 Token）只放函数环境变量，客户端零密钥
- 免费额度内 0 元；反馈沉淀在金数据后台，可筛选/导出 Excel
- 缺点：一次性配置约 15 分钟

## 路线 B（最省事，5 分钟）：Web3Forms 直连

- 打开 <https://web3forms.com> → 填邮箱 → 收 **Access Key**（免费 250 条/月）
- 不需要服务器、不需要实名，Key 的设计就是**给前端/客户端公开使用**的
- 客户端直接 POST `https://api.web3forms.com/submit`，你的邮箱收到一条反馈
- 代价（必须知情）：
  1. **境外服务**，诊断文本会经过境外服务器
  2. 国内可达性会变（今天通不代表明年通）——客户端已做**提交失败自动落盘**，
     最坏情况退化成“保存文件”，不会丢内容
  3. 邮件可能进垃圾箱，建议在邮箱里给发件域加白名单
  4. Key 公开 => 任何人可拿它给你发邮件（有 250 条/月上限，最坏是额度被刷完）

### 接入方式（代码已就绪）

拿到 Key 后只改两行常量（`app/feedback.py`）：

```python
_DEFAULT_ENDPOINT = "https://api.web3forms.com/submit"
_DEFAULT_ACCESS_KEY = "你的 Access Key"
```

`submit()` 会自动补 `access_key` / `subject` / `from_name` 字段（已测）。
重新打包后，设置页「反馈与诊断」里就会亮出「匿名提交」按钮。

---

## 怎么选

| 你的情况 | 选 |
|---|---|
| 想长期稳定、能接受实名注册 | **A（腾讯云函数）** |
| 只想马上能用、不注册国内云 | **B（Web3Forms）** |
| 两条都不想碰 | 保持现状：复制 / 保存文件 / Gitee Issues（零网络零风险） |
