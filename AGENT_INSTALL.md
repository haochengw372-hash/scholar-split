# Agent 安装契约

本文件供本地编程 Agent 执行 ScholarSplit 安装任务。

## 允许的动作

1. 克隆本仓库并阅读 `README.md`、`SECURITY.md` 与本文件。
2. 运行 `npm test` 和 `npm run check`。
3. 优先下载最新 Release 的完整 ZIP、XPI 和 `SHA256SUMS`，先校验再解压。运行 `./scripts/install.sh`，安装稳定目录中的 Chrome 扩展、完整服务、PDF2zh-next 与 Zotero XPI。
4. 只读检查 `http://127.0.0.1:8890/health`。
5. 需要 uv 与 Python 3.12。缺少运行时先说明官方安装方法；未经授权不执行远程 shell 安装器。安装本仓库公开依赖是本安装任务的正常步骤，旧 PDF2zh 引擎仅在用户需要时加 `--legacy`。
6. 源码安装先进入 `integrations/zotero/plugin` 运行 `npm ci --ignore-scripts --no-audit --no-fund`、`npm test` 与 `npm run build`，再运行根目录安装器；Node.js 22+。不得直接写 `zotero.sqlite`。
7. 告诉用户在 Chrome 中加载脚本输出的扩展目录；安装 Chrome/Zotero 扩展时
   保留由用户完成的最终确认。

## 安全边界

- 不得要求用户把 API Key 粘贴进聊天。
- 不得读取、打印、提交或上传本机已有 API Key。
- 不得把 PDF、导读、浏览历史或本地任务记录上传到仓库。
- 不得静默修改 Chrome 企业策略或绕过 Chrome 的扩展安装确认。
- 服务缺失时安装并启动本发行的完整后端。8890 被占用时不终止现有服务；先报告所有者与兼容性，用另一个端口验证新安装。需要替换现有服务时保留旧数据并确认迁移范围。
- 运行时配置、数据库、配对令牌和论文保留原位；不把既有服务目录递归复制到仓库。
- 默认安装根目录为 macOS 的 `~/Library/Application Support/ScholarSplit` 或 Linux 的 `~/.local/share/ScholarSplit`。自定义用 `SCHOLAR_SPLIT_INSTALL_ROOT`；相同变量用于安装目录中的 `scripts/start.sh`、`stop.sh` 与 `status.sh`。
- 用户在安装目录执行 `server/.venv/bin/python server/configure.py`，无回显输入模型密钥；不要让密钥进入聊天或命令行参数。
- 自定义根目录时在 Zotero 配置编辑器设置 `extensions.zotero.pdf2zh.scholarSplitTokenPath` 为令牌文件路径，不是令牌值；服务地址用插件现有设置。
- 不移动或复制 Zotero PDF；只记录原路径、哈希、大小和修改时间。

## 完成标准

- 测试通过。
- 安装目录中存在 `manifest.json`，版本与仓库一致。
- 本地服务健康检查结果明确标记为“兼容”“不兼容”或“未运行”。
- 已安装工作台时，`/workspace` 与 `/api/v1/summary` 可访问，`/classic` 不应作为入口出现。
- 安装目录有完整主机、独立 next 引擎、Chrome 扩展和 Zotero XPI；健康接口 `engines.pdf2zh_next` 为 true，`workspaceReady` 为 true。仅能启动网页但未安装引擎不算完成。
- 告知用户完成 Chrome 加载扩展及 Zotero 从文件安装的最终确认；插件会替换旧 PDF2zh 导读版，保留偏好与文献。
- 首次翻译需下载引擎官方版面模型/字体资源；不能声称安装包完全离线。

## 更新已有安装

对本安装器管理的服务，先运行安装目录 `scripts/stop.sh`，再安装新发行包并启动。
安装器保留配置、数据库和文件，但不会自动终止已运行进程；不停止则进程仍运行旧源码。
其他程序管理的服务（如旧 LaunchAgent）需要单独决定迁移，不得直接杀掉占用 8890 的进程。
