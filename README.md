# sn-submission-monitor

[English](README.en.md) · [安全说明](SECURITY.md) · [贡献指南](CONTRIBUTING.md) · [更新记录](CHANGELOG.md)

定期检查自己的期刊投稿状态，在发现变化后通过邮件或 Windows 桌面通知提醒。首次成功检查只建立基线，不发送历史状态通知。

**Python 3.10+。非期刊官方工具；不是实时推送服务，也不支持 ScholarOne。** 使用前请确认账号访问权限、期刊网站条款和所在机构的自动化访问政策。

## 支持范围与边界

| 平台配置 | 识别方式 | 实现与依赖 |
| --- | --- | --- |
| `sn` | `submission.springernature.com` | Springer Nature 投稿平台；Playwright Chromium |
| `mts` | 如 `mts-ncomms.nature.com` | eJournalPress MTS；`requests`，无需下载浏览器 |
| `em` | `editorialmanager.com/<期刊代码>` | Editorial Manager；Playwright Chromium；可选 Elsevier SSO |

- **eJournalPress MTS 不等于 ScholarOne。** 不能只凭期刊名称判断兼容性，请检查实际投稿网址。
- 已知实际账号验证范围限于 **SN Communications 系列和 Elsevier EM**；离线样例覆盖不代表已验证所有期刊。并非每个期刊、定制页面、大学统一认证、MFA 或 SSO 都能使用。
- 电脑休眠、关机、离线、用户未登录或登录失效时不能保证检查与通知。恢复后只能读取当时可见的状态，**不保证捕获所有中间状态**。
- 通知为尽力投递：SMTP 接受邮件不代表已到收件箱；桌面通知受 Windows 设置影响。不要依赖本工具处理修回期限等关键事项。

## 快速开始

### Windows

在普通终端中操作，无需管理员权限：

```bat
git clone https://github.com/icebird1998/sn-submission-monitor.git
cd sn-submission-monitor
setup_windows.bat
```

`setup_windows.bat` 只创建或复用仓库内 `.venv`、安装 `requirements.txt` 和可编辑包。不创建配置、不登录、不发送邮件、不安装计划任务，也不自动下载 Chromium。没有 Python 启动器时，可指定 Python 3.10+ 的完整路径：

```bat
setup_windows.bat "C:\Path\Python312\python.exe"
```

然后进行本地配置与检查：

```bat
.venv\Scripts\python.exe -m submission_monitor --init
.venv\Scripts\python.exe -m submission_monitor --check-config
```

`--init` 是交互向导，**不会覆盖已有配置**。仅使用 MTS 可跳过浏览器下载；SN 或 EM 另需：

```bat
.venv\Scripts\python.exe -m playwright install chromium
```

浏览器安装完成后运行本地诊断（仅用 MTS 不需要浏览器）。诊断通过并确认权限后，才主动执行一次在线检查：

```bat
.venv\Scripts\python.exe -m submission_monitor --doctor
.venv\Scripts\python.exe -m submission_monitor --once
.venv\Scripts\python.exe -m submission_monitor --show
```

### Linux / macOS，或手动安装

```bash
git clone https://github.com/icebird1998/sn-submission-monitor.git
cd sn-submission-monitor
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
# 可选：安装包及 sn-submission-monitor 命令
python -m pip install -e . --no-deps
# 仅 SN / EM 需要浏览器；MTS-only 可跳过
python -m playwright install chromium
python -m submission_monitor --init
python -m submission_monitor --check-config
python -m submission_monitor --doctor
```

也可用 `python -m pip install .` 安装包及其 `requests`、`playwright` 依赖；这不会下载浏览器。Linux 的 Chromium 可能还需要系统库，请按 Playwright 提示处理。仓库入口 `python monitor.py`、模块入口 `python -m submission_monitor` 使用相同 CLI；安装包后 `sn-submission-monitor` 也是同一入口。

## 配置与私有数据

从仓库运行或以可编辑方式安装时，默认优先使用仓库的 **`.local/config.json`**；该文件不存在时，兼容已有仓库根目录 `config.json`。非可编辑的 `pip install .` 安装后，控制台命令默认使用**当前工作目录的 `.local/config.json`**，而不是 `site-packages`。为避免启动目录差异，建议始终用 `--config` 指定绝对路径：

```bash
python -m submission_monitor --config /absolute/private/path/config.json --init
python -m submission_monitor --config /absolute/private/path/config.json --check-config
```

运行时状态、待重试通知、会话、锁和日志放在**配置所在目录**，或顶层 `runtime_dir` 指定的目录。建议使用绝对路径的私有目录，或已忽略的 `.local/`，不要放进版本控制。若继续使用旧的根目录配置，请迁移到私有目录并同步更新计划任务路径；脚本不会擅自迁移或删除旧数据。

`config.example.json` 仅含占位内容。手动复制后必须替换；示例占位密码会被本地校验拒绝。以下是 SN 的配置形状，空密码也必须由向导或环境变量补全：

```json
{
  "platform": "sn",
  "sn": {
    "email": "you@example.com",
    "password": "",
    "watch": "all"
  },
  "notify": {
    "toast": true
  },
  "fail_alert_after": 6,
  "interval_minutes": 15,
  "network": {
    "attempts": 3,
    "timeout_seconds": 45
  }
}
```

### 平台和筛选

`platform` 接受 `"sn"`、`"mts"`、`"em"`、`"both"`（兼容旧配置：SN + MTS）、`"all"`（三个平台），或数组如 `["sn", "em"]`。只启用需要的平台，并配置相应账号。

- **SN**：`email`、`password`；`watch: "all"`，或稿件 UUID / 完整稿件 URL 的数组。
- **MTS**：`base`、`email`、`password`；全部稿件用 `watch: "all"`，单篇用 `watch: "single"` + 数字 `ms_id`。以下 ID 仅为示例：

  ```json
  {
    "platform": "mts",
    "mts": {
      "base": "https://mts-ncomms.nature.com",
      "email": "you@example.com",
      "password": "",
      "watch": "single",
      "ms_id": 123456
    }
  }
  ```

- **EM**：`base` 为期刊入口，如 `https://www.editorialmanager.com/JOURNAL_CODE`，请替换期刊代码；另有 `email`、`password` 和可选通知显示名 `label`。只有实际使用 Elsevier 登录流程时才加 `"sso": "elsevier"`，不是通用大学 SSO 支持。`watch` 任选一种：

  ```json
  "all"
  ```

  ```json
  ["EM-DEMO-26-00001"]
  ```

  ```json
  [{"title": "Example manuscript title", "submitted": "Nov 1, 2026"}]
  ```

  后一种适合尚无稿件编号的条目；标题和提交日期应与页面显示一致。按基础稿件编号筛选会跟随其 `R1` / `R2` 等修订版；显式指定带 `R1` 后缀的编号只匹配该修订版。以上是 `em.watch` 的值，不是独立配置文件。

### 通知

`notify.toast` 为布尔值；Windows 桌面通知可用 `--no-toast` 临时关闭，其他系统不依赖此通道。邮件可省略整个 `notify.email`，需要时在 `notify` 内加入：

```json
{
  "toast": true,
  "email": {
    "smtp_host": "smtp.example.com",
    "smtp_port": 465,
    "smtp_user": "you@example.com",
    "smtp_auth_code": "",
    "to": ["you@example.com"],
    "smtp_security": "ssl"
  }
}
```

这仍是示意，必须使用自己的 SMTP 服务设置。`to` 可为单个地址字符串或地址数组；`smtp_port` 默认 `465`；`smtp_security` 为 `ssl` 或 `starttls`（后者通常需要显式配置端口 `587`，以服务商要求为准）。`enabled` 可显式设为 `false` 禁用邮件；省略时按是否配置有效授权码启用。显式启用则必须补齐所需设置。遵循邮箱服务商指南使用专用 SMTP 授权码，不要把授权码贴进 issue。

以下环境变量覆盖配置中的对应秘密值：

| 环境变量 | 覆盖字段 |
| --- | --- |
| `SNM_SN_PASSWORD` | SN 密码 |
| `SNM_MTS_PASSWORD` | MTS 密码 |
| `SNM_EM_PASSWORD` | EM 密码 |
| `SNM_SMTP_AUTH_CODE` | SMTP 授权码 |

环境变量并不加密本地数据；不要把值放进 Git、命令行参数、截图或工单。计划任务必须能在其运行用户的环境中读取这些变量，当前终端临时设置的变量不一定可用。

## 命令参考

以下命令中的 `python` 指项目虚拟环境解释器，均可加 `--config PATH`：

| 命令 | 行为 |
| --- | --- |
| `python -m submission_monitor --init` | 交互配置向导；拒绝覆盖已有配置 |
| `python -m submission_monitor --check-config` | 仅本地验证配置；不登录、不发邮件 |
| `python -m submission_monitor --doctor` | 本地配置、运行目录和依赖健康检查；不登录、不输出凭据或原始日志；错误配置、缺少必需依赖或所选平台所需浏览器返回非零 |
| `python -m submission_monitor --once` | 在线检查一次；也是不带模式参数时的默认行为；出错返回非零 |
| `python -m submission_monitor --loop 15` | 每 15 分钟在线检查；最小 5 分钟，`Ctrl+C` 退出 |
| `python -m submission_monitor --show` | 只读本地缓存状态和待重试通知，不在线查询 |
| `python -m submission_monitor --list` | 在线列出配置选中范围内的稿件（`watch: "all"` 才列全部）；不改基线、不发送通知，但可能登录/刷新本地会话 |
| `python -m submission_monitor --test-email` | 明确标记为测试的邮件；需要主动执行，不改变监控状态；成功仅表示 SMTP 接受 |
| `python -m submission_monitor --once --no-toast` | 本次检查不发桌面通知 |
| `python -m submission_monitor --once --headed` | 在支持的浏览器平台显示窗口；不绕过认证限制 |
| `python -m submission_monitor --reset --yes` | 清除监控基线；必须显式确认；**不删除凭据或待重试通知** |

### 状态、重试与告警

- 首次成功检查只建基线，不给已有稿件发历史通知；基线建立后发现的**新稿件也会通知**。
- 状态原子写入，并使用跨进程锁避免同时修改同一运行目录。不要让旧版本与新版本同时写入同一批数据。
- 每个通知通道有持久化待重试队列；一个通道失败不会把另一个通道当成失败。投递语义为**至少一次**：发送成功与落盘之间若发生崩溃，重试可能产生少量重复通知；多收件人 SMTP 只接受部分地址时，重试也可能让已接受的收件人收到重复邮件。
- 暂时设 `notify.email.enabled: false` 会暂停已有邮件队列而不是丢弃；待发送项保留，运行以非零状态（退出码 `3`）提示未完成，重新启用后再重试。为了隐私，更换账号或收件人会取消旧队列，避免把旧账号的稿件发往新目标。
- 缩小 `watch` 范围不会把仍保留的稿件重新当成首次运行；完全属于剩余范围的已排队事件继续保留。旧事件若同时包含已移除的稿件，将作为整体取消，而不是把混合事件继续发送。
- 各平台分别累计失败次数，默认连续 `6` 次后告警，恢复后发送恢复通知。若全部通知通道也不可用，告警本身无法保证到达，请定期使用 `--doctor` / `--show` 检查。
- 默认检查间隔 `15` 分钟、网络尝试 `3` 次、超时 `45` 秒。不要低于 5 分钟轮询，也不要用本工具绕过验证码、MFA、访问限制或网站条款。
- `--reset --yes` 不撤销队列中已记录的通知；后续检查仍可能投递它们。卸载计划任务同样不删除这些数据。

## Windows 计划任务

先手动完成本地检查和一次在线检查，再在普通终端中**主动**安装：

```bat
install_task.bat
```

默认任务名 **`SNSubmissionMonitor`**，每 **15 分钟**调用一次仓库 `.venv\Scripts\pythonw.exe`（没有时用同目录 `python.exe`），不从 PATH / Conda 猜解释器。参数通过批处理传给 PowerShell，也可直接调用：

```powershell
.\scripts\install_task.ps1 -TaskName SNSubmissionMonitor `
  -Config "C:\Users\YOU\AppData\Local\sn-submission-monitor\config.json" `
  -PythonExecutable "C:\path\sn-submission-monitor\.venv\Scripts\pythonw.exe" `
  -IntervalMinutes 15
```

- `TaskName`、`Config`、`PythonExecutable`、`IntervalMinutes` 可配置；间隔为 5–1440 分钟。相对配置/解释器路径按仓库目录解析。
- 只以**当前已登录用户**、非提权方式运行，不保存 Windows 登录密码。采用 `MultipleInstances=IgnoreNew`、`StartWhenAvailable`，不唤醒睡眠电脑；执行时限为 `min(10, 间隔−1)` 分钟。超时会结束当次运行，稿件很多或网站缓慢时需检查任务结果。
- 安装脚本先查询任务并执行本地 `--check-config` / `--doctor`，不主动登录或发测试邮件；注册后的首次计划触发约在一分钟后。
- 已存在的同名任务会显示摘要并报错，**不会静默覆盖**。检查后需明确加 `-Force` 才能替换。`-WhatIf` 可预览注册而不创建任务（仍做本地查询和校验）。
- 若脚本策略阻止执行，先检查脚本并遵循机构策略；不要直接提权或永久放宽机器执行策略。
- 移动仓库/虚拟环境/配置后需要检查并更新任务路径。单独的 pip 安装不会自动创建任务。

卸载会先显示摘要并要求输入完整任务名确认；仅移除任务注册，不删数据：

```bat
uninstall_task.bat
uninstall_task.bat -TaskName MySubmissionMonitor
```

无人值守删除需要显式 `-Force`。**已有本地 `NCommsStatusMonitor` 不会被默认安装/卸载操作修改。** 请在任务计划程序中单独检查旧任务，避免重复调度；若确实要删除它，用 `uninstall_task.bat -TaskName NCommsStatusMonitor` 并确认。不要同时启用多个循环或调度器。

### Linux / macOS 定时运行

可使用 `--loop 15`，或在自行配置的 cron / 服务中使用虚拟环境与配置的绝对路径。例如，先创建并保护好私有数据目录，再设置：

```cron
*/15 * * * * /absolute/path/sn-submission-monitor/.venv/bin/python /absolute/path/sn-submission-monitor/monitor.py --config /home/YOU/.local/share/sn-submission-monitor/config.json --once >> /home/YOU/.local/share/sn-submission-monitor/cron.log 2>&1
```

服务环境和交互终端环境可能不同。此示例不代表所有平台都已完成集成验证；请检查自己的服务权限、依赖和调度结果。

## 排错与参与

先用本地 `--check-config`、`--doctor` 和只读 `--show`，再按需主动执行 `--list` 或 `--test-email`。网页改版、SSO、验证码、网络限制及浏览器依赖都可能导致失败。不要公开配置、状态、会话或原始日志，即使你认为已删掉密码；其中可能有 Cookie、稿件标题、邮箱或链接标识符。

问题报告、离线测试与安全处理见 [CONTRIBUTING.md](CONTRIBUTING.md) 和 [SECURITY.md](SECURITY.md)。MIT 许可见 [LICENSE](LICENSE)；无可用性、投稿结果或通知送达保证。
