<p align="center">
  <img src="icons/icon-128.png" width="128" alt="ScholarSplit 译字标志">
</p>

<h1 align="center">ScholarSplit · 论文双读</h1>

<p align="center">在 Chrome 中并排阅读论文译文与结构化中文导读。</p>

ScholarSplit 是一个本地优先的 Chrome 扩展。打开在线或本地 PDF 后，点击扩展图标即可提交翻译与导读任务；处理完成后，译文 PDF 显示在左侧，研究问题、理论、方法、统计、发现与阅读路径显示在右侧。

> 当前仓库只包含 Chrome 扩展，不包含翻译引擎、模型服务或 API Key。它需要一项运行在 `127.0.0.1:8890` 的兼容本地服务，协议见 [本地服务接口](docs/LOCAL_SERVICE_PROTOCOL.md)。

## 主要能力

- 从 Chrome 当前打开的 HTTP、HTTPS 或本地 PDF 发起任务。
- 翻译与结构化导读并行处理。
- 独立双栏阅读页，译文和导读相互对照。
- 关闭侧栏后保留任务编号，重新打开可恢复进度。
- 校验 PDF 文件头、结尾和大小，避免把登录页当作论文。
- API Key 只保存在本地服务中，不进入扩展存储或仓库。
- 按当前网站按需申请读取权限，不默认读取全部网页。

## 复制给 Agent 一键准备

把下面这一整行复制给 Codex、Claude Code 或其他本地编程 Agent：

```text
请安装 ScholarSplit：https://github.com/haochengw372-hash/scholar-split；先阅读 AGENT_INSTALL.md，再克隆仓库并运行 ./scripts/install.sh，执行测试并验证 127.0.0.1:8890 的兼容服务；不要索取、读取、打印或上传任何 API Key；最后只在 Chrome 必须由我确认“加载已解压的扩展程序”时提醒我操作。
```

Agent 或终端也可以直接运行：

```bash
git clone https://github.com/haochengw372-hash/scholar-split.git && cd scholar-split && ./scripts/install.sh
```

Chrome 出于安全原因不允许普通脚本静默安装开发者扩展。脚本会把扩展准备到稳定目录并打开该目录，最后仍需在 `chrome://extensions` 中点击一次“加载已解压的扩展程序”。

## 手动安装

1. 确认兼容本地服务已启动：访问 `http://127.0.0.1:8890/health`。
2. 下载或克隆本仓库。
3. 在 Chrome 打开 `chrome://extensions`。
4. 开启“开发者模式”，点击“加载已解压的扩展程序”。
5. 选择仓库根目录，或选择安装脚本输出的 `extension` 目录。
6. 打开论文 PDF，点击工具栏中的“ScholarSplit 论文双读”。

本地 `file://` PDF 还需在扩展详情中开启“允许访问文件网址”。遇到出版社登录页、一次性下载链接或验证页面时，先正常下载 PDF，再使用侧栏中的“选择已下载的 PDF”。

## 本地开发

项目不需要 npm 依赖：

```bash
npm test
npm run check
```

生成各尺寸扩展图标需要 Python 和 Pillow：

```bash
python3 tools/generate_icons.py
```

## 隐私与安全

- 扩展只连接当前 PDF 来源和 `http://127.0.0.1:8890`。
- PDF 内容发送给用户自行运行的本地服务；本仓库没有遥测或分析代码。
- 浏览器本地存储只保存任务编号、文件名、处理状态和导读结果，不保存 API Key。
- 发布前请阅读 [隐私说明](PRIVACY.md) 和 [安全策略](SECURITY.md)。

## 许可证与第三方项目

本仓库自行编写的 Chrome 扩展代码采用 [MIT License](LICENSE)。兼容的翻译后端可能使用不同许可证；当前验证过的相关项目采用 AGPL-3.0，并不属于本仓库的 MIT 授权范围。详见 [第三方声明](THIRD_PARTY_NOTICES.md)。

## 项目状态

当前为早期公开版本。Chrome 扩展的安装、在线 PDF 权限和不同出版社的下载方式仍可能因浏览器或网站策略而变化，欢迎提交可复现的问题报告。
