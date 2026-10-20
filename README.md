# sn-submission-monitor

期刊投稿状态自动监控 + 邮件/弹窗通知。投稿状态一变，立刻发邮件、弹 Windows 通知，再也不用手动刷投稿系统。

> English blurb: A lightweight monitor for your manuscript submissions on Springer Nature's submission platform and Aries MTS (ScholarOne) journals. It logs in with your account, watches the status of **all** your submissions, and notifies you by email and/or Windows toast the moment anything changes. See `config.example.json` for setup.

## 支持的投稿系统

| 平台 | 覆盖期刊（举例） | 实现方式 |
|---|---|---|
| **Springer Nature 投稿平台**（submission.springernature.com） | Scientific Reports、Communications Earth & Environment、Communications Physics / Biology / Medicine、npj 系列、Earth Science Informatics 等 | Playwright 无头浏览器 |
| **Aries MTS / ScholarOne**（mts-*.nature.com） | Nature Communications 等 Nature 系列期刊 | 纯 HTTP 请求（无需浏览器） |

判断方法：你投稿时打开的网址是 `submission.springernature.com/...` 就是前者；是 `mts-xxx.nature.com/...` 就是后者。

## 特性

- **一次配置，监控全部投稿**：默认监控账号下所有稿件（含新投的），任何一篇状态变化都会通知；也可以在配置里只盯某几篇
- **变更即通知**：Windows 桌面弹窗 + 邮件（任意 SMTP：QQ / 163 / Gmail / Outlook …），两个通道可独立开关
- **通知内容丰富**：自动附上稿件详情页的当前状态全文和进度步骤（Submission received → Technical check → Editorial assignment → With editor → Peer review → 决定）
- **会话缓存 + 自动重登**：浏览器登录态缓存在本地，过期自动重新登录
- **失败告警**：连续多次检查失败（改密码、网页改版等）会主动发告警邮件
- **多种运行方式**：`--loop` 常驻单进程 / Windows 计划任务（附一键安装脚本）/ Linux cron / macOS launchd
- **敏感字段可用环境变量**：密码、SMTP 授权码可以不写进配置文件

## 安装

需要 Python 3.9+。

```bash
git clone https://github.com/<你的用户名>/sn-submission-monitor.git
cd sn-submission-monitor
pip install -r requirements.txt

# 使用 SN 平台监控（submission.springernature.com）需要下载无头浏览器（约 150MB）
python -m playwright install chromium
# 仅用 MTS 模式可跳过上一步
```

国内下载 Chromium 慢的话用镜像：

```bash
# Windows (cmd)
set PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright/
python -m playwright install chromium
```

## 配置

复制 `config.example.json` 为 `config.json`，按需填写：

```jsonc
{
  "platform": "sn",            // "sn" | "mts" | "both"
  "sn": {
    "email": "you@example.com",
    "password": "YOUR_PASSWORD",
    "watch": "all"             // "all" = 监控全部投稿；或填 URL 数组只盯某几篇
  },
  "mts": {
    "base": "https://mts-ncomms.nature.com",
    "email": "you@example.com",
    "password": "YOUR_PASSWORD",
    "watch": "all",            // "all" 或配合 ms_id 只盯一篇
    "ms_id": null
  },
  "notify": {
    "toast": true,             // Windows 弹窗（非 Windows 自动忽略）
    "email": {                 // 整段删掉或留空 smtp_auth_code = 不发邮件
      "smtp_host": "smtp.qq.com",
      "smtp_port": 465,
      "smtp_user": "you@example.com",
      "smtp_auth_code": "****",
      "to": "you@example.com"
    }
  },
  "fail_alert_after": 6
}
```

### QQ 邮箱授权码获取方式

QQ 邮箱网页版 → 设置 → 账号（账号与安全）→ 开启 **IMAP/SMTP 服务** → 按提示短信验证 → 生成 16 位授权码（**不是 QQ 密码**）。163 邮箱类似（开启 SMTP 得"客户端授权密码"）。

### 环境变量覆盖（可选）

| 变量 | 作用 |
|---|---|
| `SNM_SN_PASSWORD` | 覆盖 SN 平台密码 |
| `SNM_MTS_PASSWORD` | 覆盖 MTS 密码 |
| `SNM_SMTP_AUTH_CODE` | 覆盖 SMTP 授权码 |

## 使用

```bash
python monitor.py --once        # 检查一次（首次运行只建基线，不发通知）
python monitor.py               # 同 --once
python monitor.py --loop 15     # 常驻运行，每 15 分钟检查一次（Ctrl+C 退出）
python monitor.py --show        # 查看当前各稿件状态（读缓存）
python monitor.py --test-email  # 验证邮件通道
python monitor.py --reset       # 清空基线重新建立
python monitor.py --no-toast    # 本次运行不弹窗
```

状态有任何变化时，你会收到（示例）：

```
主题：【投稿状态更新】Communications Earth & Environment: Peer review 20 October 2026

◆ Communications Earth & Environment
  标题: Toward ...
  版本: v.1.0
  旧状态: Technical check 16 October 2026
  新状态: Peer review 20 October 2026
  详情:
    Your submission is now under peer review ...
    Submission received - done
    Technical check - done
    ...
```

## 定时运行

### Windows（推荐，附一键脚本）

双击 `install_task.bat`（创建每 15 分钟运行的计划任务，pythonw 静默无窗口），`uninstall_task.bat` 可删除。

### Linux / macOS

cron 示例（每 15 分钟）：

```bash
*/15 * * * * cd /path/to/sn-submission-monitor && /usr/bin/python3 monitor.py --once >> monitor.log 2>&1
```

或直接用 `--loop` 模式配合 systemd / supervisor 常驻。

## 注意事项

1. **config.json 含明文密码，已在 .gitignore 中**，请勿提交或外发；介意的话用环境变量传敏感字段
2. 默认 15 分钟一次已足够及时，**不建议**把频率调到 5 分钟以下——对期刊服务器友好一点，也降低账号触发风控的概率
3. 本工具仅模拟正常浏览器行为查询自己的投稿状态，请仅用于本人账号
4. 期刊系统改版可能导致解析失败，届时会收到失败告警邮件，欢迎提 issue
5. Windows 计划任务默认"仅登录时运行"：电脑关机期间的变化会在下次开机检查时补发通知

## 工作原理

- **SN 平台**：Playwright 无头 Chromium 走 Springer Nature 统一登录（idp.springernature.com），登录态缓存于 `sn_storage.json`；之后抓取 "Your submissions" 列表页，解析每篇稿件的状态卡片；检测到变化时再抓对应详情页，把 CURRENT STATUS 全文和进度写进通知
- **MTS**：requests 直接 POST 登录，遍历稿件文件夹（Live / Post Decision 等）读取每篇的 Current Stage
- 状态快照存于 `state.json`，每次比对签名（状态文本 + 版本号），变化即通知并更新快照

## License

MIT
