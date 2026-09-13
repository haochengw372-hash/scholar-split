# Agent 安装契约

本文件供本地编程 Agent 执行 ScholarSplit 安装任务。

## 允许的动作

1. 克隆本仓库并阅读 `README.md`、`SECURITY.md` 与本文件。
2. 运行 `npm test` 和 `npm run check`。
3. 运行 `./scripts/install.sh`，将扩展复制到稳定目录。
4. 只读检查 `http://127.0.0.1:8890/health`。
5. 若已存在用户自管的兼容 Flask 翻译服务，将 `integrations/server/` 作为
   `scholarsplit` 包安装到服务目录，并按该目录 README 注册工作台；不得擅自下载
   或替换后端。
6. 若 Zotero 插件源代码已在本机且许可证兼容，可按
   `integrations/zotero/README.md` 集成桥接并运行契约测试；不得写
   `zotero.sqlite`。
7. 告诉用户在 Chrome 中加载脚本输出的扩展目录；安装 Chrome/Zotero 扩展时
   保留由用户完成的最终确认。

## 安全边界

- 不得要求用户把 API Key 粘贴进聊天。
- 不得读取、打印、提交或上传本机已有 API Key。
- 不得把 PDF、导读、浏览历史或本地任务记录上传到仓库。
- 不得静默修改 Chrome 企业策略或绕过 Chrome 的扩展安装确认。
- 若兼容本地服务缺失，停止在扩展准备完成的状态，并向用户说明依赖；不要未经确认自动安装第三方后端。
- 不移动或复制 Zotero PDF；只记录原路径、哈希、大小和修改时间。

## 完成标准

- 测试通过。
- 安装目录中存在 `manifest.json`，版本与仓库一致。
- 本地服务健康检查结果明确标记为“兼容”“不兼容”或“未运行”。
- 已安装工作台时，`/workspace` 与 `/api/v1/summary` 可访问，`/classic` 不应作为入口出现。
- 用户只需完成 Chrome 的“加载已解压的扩展程序”安全确认。
