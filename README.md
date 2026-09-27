# Windows 微信自动回复

面向个人微信 Windows 客户端的本地程序。第一版只处理配置中启用的**私聊文字消息**：从本地聊天数据库读取新消息，按关键词规则调用兼容 OpenAI Chat Completions 的大模型 API，默认只保存拟回复；开启 `auto_send` 后通过微信页面发送，并从数据库回读确认。

当前适配目标是微信 **4.1.13.65**。项目使用固定提交的 [wechatauto-replica](https://github.com/fanyuantaier/wechatauto-replica) 读取数据库和激活 UI Automation。微信更新后，版本检查会阻止继续自动发送，需先重新验证。

## 安装

在 Windows PowerShell、Python 3.12 环境下：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m wxreply init
```

`init` 创建本地 `config.json`。先运行以下命令验证数据库，并查找联系人的内部 ID：

```powershell
.\.venv\Scripts\python.exe -m wxreply probe-db
.\.venv\Scripts\python.exe -m wxreply list-chats
.\.venv\Scripts\python.exe -m wxreply find-contact --name "联系人备注"
```

`list-chats` 只在本机终端显示会话 ID 与名称。需要测试页面发送时，显式运行 `probe-send`；它会向自己的**文件传输助手**发送一条带随机后缀的测试消息，并核对数据库记录。

## 配置与运行

编辑 `config.json`，填写 `model`，并在 `chats` 中加入需要监听的私聊。例如：

```json
{
  "username": "wxid_example",
  "display_name": "唯一的微信备注名",
  "chat_type": "private",
  "enabled": true,
  "keywords": ["测试自动回复"]
}
```

该示例应作为 `chats` 数组中的一个对象。`display_name` 必须能唯一对应目标联系人；重复备注会被拒绝。只有消息包含至少一个 `keywords` 时才生成回复；空数组不会触发回复。群聊字段已保留，但第一版禁止启用群聊自动回复。

在启动程序的同一个 PowerShell 会话中设置 API 密钥，并运行：

```powershell
$env:WXREPLY_API_KEY = "你的 API 密钥"
.\.venv\Scripts\python.exe -m wxreply run
```

`api_base_url` 默认是 `https://api.openai.com/v1`，可以改为其他兼容 Chat Completions 的服务地址。密钥只从环境变量读取。程序在系统托盘显示状态，可暂停、恢复、查看最近消息并退出。修改配置后需要重启程序。

首次运行时，程序将各会话现有最新消息设为基线，**不会回复旧消息**。`auto_send` 默认是 `false`，生成结果会保存在本地数据库中。确认只生成模式的识别结果后，将其改为 `true` 并重启，才会实际自动发送。

## 发送与故障处理

发送前，程序重新核对目标会话、最新消息和输入框文字；只执行一次回车发送。随后回读数据库寻找新的、自己发出的完全相同文本。若结果不确定，任务标记为 `unknown` 并暂停，重启也不会自动重发。托盘“最近消息”可查看该任务；在微信里核实后再手动恢复。

程序要求微信保持登录，Windows 保持未锁屏桌面。它会检查微信进程版本和桌面状态；不可操作时暂停。暂停期间收到的消息会记录为忽略，不会在恢复后补发。

程序状态保存在 `%LOCALAPPDATA%\wxreply\state.db`。上游库的数据库密钥和解密缓存可能存放在 `%LOCALAPPDATA%\wxreply\upstream`；个别多账号恢复路径会使用系统临时目录。请把这些目录视作敏感聊天数据，不要同步到公开网盘或提交到 Git。

## 验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

测试覆盖重复文本消息、重启去重、发送中断、暂停与只生成模式。当前机器上的微信 4.1.13.65 已通过本地数据库读取及文件传输助手发送回读验证；**指定联系人持续运行与大模型调用仍需填入配置后验证**。
