# 第三方声明

ScholarSplit 包含上游衍生源码，不是全仓库 Apache-2.0：

- [zotero-pdf2zh](https://github.com/guaguastandup/zotero-pdf2zh)（guaguastandup）：AGPL-3.0。完整衍生源码和许可证保存在 `integrations/server/host/`、`integrations/zotero/plugin/`。
- [PDFMathTranslate / PDF2zh](https://github.com/PDFMathTranslate/PDFMathTranslate)：AGPL-3.0，通过 `--legacy` 安装独立引擎。
- [PDF2zh-next](https://github.com/PDFMathTranslate/PDFMathTranslate-next) 和 [BabelDOC](https://github.com/funstory-ai/BabelDOC)：AGPL-3.0，由安装器安装并保留包许可证。
- PyMuPDF 使用其 AGPL 开源发行；其余依赖保留各自包许可证。
- Zotero 构建使用 zotero-plugin-toolkit 和 zotero-plugin-scaffold；来源及版本见插件 `package-lock.json`。

`integrations/server/` 与 `integrations/zotero/` 采用 AGPL-3.0-or-later。
独立 Chrome 扩展、工作台前端和根目录工具从 v0.3.1 起采用 Apache-2.0。完整对应源码随仓库及发行包提供，基线和修改说明见 [UPSTREAM.md](docs/UPSTREAM.md)。此前 MIT 发行版及其他依赖保留原许可证。

ScholarSplit 名称与“译”字标志不代表上游项目的官方认可或赞助。
