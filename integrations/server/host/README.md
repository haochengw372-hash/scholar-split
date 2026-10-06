# 本地翻译主机（AGPL-3.0-or-later）

本目录包含从 zotero-pdf2zh 衍生的完整 Flask 翻译主机源码，而非只包含工作台插件。
默认安装 PDF2zh-next 2.9.0 + BabelDOC 0.6.2；`--legacy` 另外安装 PDF2zh 1.9.11。
两种引擎必须放在独立环境，避免同名 `pdf2zh` 命令和依赖冲突。

请从仓库根目录运行 `./scripts/install.sh`。安装器将本目录、
`integrations/server/*.py` 和 `dashboard/` 组合到安装目录；保留原配置和文献。
根目录锁定直接依赖版本，不宣称跨平台所有传递依赖均固定。
首篇翻译时引擎从官方资源源获取字体、版面分析模型和 tokenizer，安装不等于完全离线。

配置模型：在安装目录运行 `server/.venv/bin/python server/configure.py`。
密钥以无回显输入保存到本机 `server/config/config.toml`（0600），不应上传。
研究模型首次从当前翻译配置迁移，随后可在工作台独立配置非敏感模型参数。

本发行关闭上游通知拉取和源码自动覆盖更新；更新请使用 ScholarSplit Release 安装包。
`server.py` 的翻译、裁剪、导读、任务与文件接口保持旧插件协议兼容。

扫描 PDF 的导读需要 OCR 文本。macOS 可在安装目录的 `server/` 中运行
`xcrun swiftc -O tools/ocr_pdf_text.swift -o tools/ocr_pdf_text` 构建本机 Vision 辅助工具
（需要 Xcode Command Line Tools）；其他系统请先为原文生成可搜索文字层。
PDF2zh-next 的翻译 OCR 与此导读文字提取是不同流程。
