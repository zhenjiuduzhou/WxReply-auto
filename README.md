# WxReply

**Windows 微信 4.x 私聊 AI 回复助手。**指定联系人发来新文字消息后，WxReply 从本机微信数据库读取消息，调用兼容 OpenAI Chat Completions 的模型生成回复。默认只生成草稿；确认效果后，才可开启自动发送。

> 当前适配并验证的微信版本：**4.1.13.65**。这是个人微信 Windows 客户端的本地工具，不支持网页版、群聊自动回复或锁屏运行。微信升级后需重新验证适配情况。

## 下载

前往 [GitHub Releases](https://github.com/zhenjiuduzhou/WxReply-auto/releases/latest) 下载 Windows 版：下载 `.exe` 后双击即可启动；`.zip` 内含同一程序和本 README。首次启动后，在窗口中配置模型 API 和监听联系人。源码仓库不直接保存 EXE 构建产物。

## 功能与边界

| 功能 | 当前行为 |
| --- | --- |
| 监听范围 | 只处理已启用联系人的新私聊文字消息；启动时跳过旧消息 |
| AI 模型 | 使用自备密钥和兼容 Chat Completions 的 API，可自定义提示词 |
| 发送模式 | 默认 `auto_send: false`，只保存拟回复；手动开启后才发送 |
| 桌面操作 | 图形界面配置联系人、测试 API、查看任务；关闭窗口后可从系统托盘恢复 |
| 发送核对 | 发送前核对会话与消息，发送后回读微信数据库；结果不确定时暂停，不自动重发 |

项目基于 [wechatauto-replica](https://github.com/fanyuantaier/wechatauto-replica) 读取微信本地数据库和操作 Windows 界面。当前固定其依赖提交；微信进程版本不匹配时程序会暂停。

## 运行条件

- Windows 10/11、已登录的微信 Windows 客户端 **4.1.13.65**。
- 从源码运行或构建 EXE：Python **3.12**、可安装 Git 依赖的网络环境。
- 一个可访问的 OpenAI Chat Completions 兼容 API、模型名称和 API 密钥。调用模型可能产生服务费用。
- 自动发送期间保持微信登录、Windows 桌面解锁；发送会操作微信界面。

## 快速开始：桌面界面

仓库不包含预构建 EXE。克隆或下载源码后，在项目根目录打开 PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m wxreply.desktop
```

如果已经按[下文](#构建独立-exe)构建 EXE，也可以双击 `dist\WxReply.exe`；该程序不要求目标电脑另装 Python。

首次使用按以下顺序操作：

1. 打开微信并登录。在 **「监听联系人」** 页点「添加联系人」，按备注或昵称搜索，选择要测试的私聊联系人并启用。
2. 在 **「模型与发送」** 页填写 API 地址、模型名称、密钥。地址填到 `/v1` 等 API 根路径，程序会请求其 `/chat/completions`。点「测试 API 连接」；测试只发送固定提示词，但可能产生少量 API 用量。
3. 保持 **「允许无人值守自动发送」关闭**，点「保存配置」，再点「启动监听」。让测试联系人发送一条**新的文字消息**；首次启动会以现有最新消息为基线，不处理旧消息。
4. 在 **「运行与任务」** 页查看收到的消息、拟回复和处理说明。确认联系人匹配与回复内容都正确。
5. 如需自动发送，先点「停止」，勾选自动发送并保存，重新启动监听，再用测试联系人验证实际发送。

关闭主窗口会缩到托盘；托盘菜单可重新显示窗口、暂停或恢复、退出。配置改动需要停止监听、保存并重新启动。

## 命令行使用

适合排查数据库、查找联系人或不使用主窗口运行。安装步骤同上，然后在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe -m wxreply init
.\.venv\Scripts\python.exe -m wxreply probe-db
.\.venv\Scripts\python.exe -m wxreply find-contact --name "联系人备注"
```

`init` 创建根目录的 `config.json`；已有文件时不会覆盖。`probe-db` 检查本地数据库是否可读。`find-contact` 输出匹配联系人的内部 ID 与名称；也可用 `list-chats` 查看最近会话。编辑 `config.json`，填入模型并在 `chats` 数组中添加联系人，例如：

```json
{
  "username": "wxid_example",
  "display_name": "微信中唯一的备注名",
  "chat_type": "private",
  "enabled": true
}
```

上述对象应放在 `chats` 数组中。`display_name` 需准确且唯一；当前只支持私聊，启用的联系人发来任意新文字消息都会触发生成，无需关键词。然后在**同一个 PowerShell 会话**中设置密钥并运行：

```powershell
$env:WXREPLY_API_KEY = "你的 API 密钥"
.\.venv\Scripts\python.exe -m wxreply run
```

命令行从环境变量读取密钥，运行后在系统托盘显示状态。`auto_send` 默认是 `false`；先查看托盘「最近消息」中的拟回复，再按需改为 `true` 并重启。需要单独验证微信发送通道时，运行 `.\.venv\Scripts\python.exe -m wxreply probe-send`：它会向**自己的文件传输助手**发送一条测试消息并回读确认；执行前先手动给文件传输助手发一条消息，供程序建立核对基线。

## 配置速查

完整模板见 [`config.example.json`](config.example.json)。桌面界面维护 `data\config.json`，命令行默认读取根目录的 `config.json`；首次打开桌面界面时，若工作目录存在 `config.json`，会复制到 `data\`。两种入口使用的配置文件位置不同，请按实际入口修改。

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `expected_wechat_version` | `4.1.13.65` | 允许操作的微信版本 |
| `api_base_url` | `https://api.openai.com/v1` | Chat Completions 兼容 API 根地址 |
| `model` | 空 | 提供商给出的模型 ID，使用前必填 |
| `system_prompt` | 内置中文提示词 | 回复风格与约束 |
| `chats` | 空 | 要监听的私聊联系人；须设置 `enabled: true` |
| `auto_send` | `false` | `false` 只生成，`true` 允许自动发送 |
| `poll_interval_seconds` | `2` | 数据库轮询间隔，至少 0.5 秒 |
| `max_parallel_api_requests` | `3` | 不同联系人的并行模型请求数，范围 1–8；界面发送仍串行 |
| `history_retention_days` | `180` | 已完成、已忽略及失败任务的保留天数，范围 7–3650 |

同一联系人连续发来多条消息时，旧的待处理任务会让位于最新消息；生成时会参考近期文字上下文。积压消息会分批读取。任务记录保留期仅影响程序的 `state.db`，不会删除微信原始聊天记录。

## 构建独立 EXE

先按快速开始安装依赖，再在项目根目录运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

产物为 `dist\WxReply.exe`。构建需在 Windows 上进行。将 EXE 单独移走时，数据保存在 **EXE 所在目录**的 `data\`；放在仓库的 `dist\` 中时，数据保存在**项目根目录**的 `data\`。参见 [PyInstaller 打包说明](https://pyinstaller.org/en/stable/operating-mode.html)。

## 数据与故障排查

- `data\config.json`：桌面版配置；`data\api-key.dpapi`：使用当前 Windows 用户的 DPAPI 加密的桌面版密钥。
- `data\state.db`：读取进度、任务和拟回复；`data\app.log`：运行日志。上游库的数据库密钥或解密缓存可能位于 `data\upstream`，个别多账号恢复路径会使用系统临时目录。
- **启动后没有回复**：确认微信版本、登录及桌面解锁状态，联系人已启用，模型名称与密钥已填写；用新消息测试，旧消息会被跳过。查看「运行与任务」或 `data\app.log`。
- **生成了但没有发出**：检查 `auto_send` 是否仍为 `false`；这是默认模式。若任务显示 `unknown`，先在微信中核实是否已经发送，再手动恢复，程序不会自动重发。
- **暂停期间的消息**：会记录为忽略，恢复后不补发。微信升级后若版本检查失败，需重新验证兼容性。

`data\` 包含敏感聊天内容。分发 EXE 时不要附带自己的 `data\`，不要将该目录同步到公开网盘或提交到 Git。

## 开发验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

测试覆盖消息去重、重启恢复、发送中断、暂停及只生成模式。微信 **4.1.13.65** 的本地数据库读取和文件传输助手发送回读已在开发机验证；指定联系人持续运行与实际模型调用仍需在你的环境中验证。
