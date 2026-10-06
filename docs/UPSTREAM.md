# 上游与下游修改记录

本发行完整提供 AGPL 对应源码：

- 上游：<https://github.com/guaguastandup/zotero-pdf2zh>
- 基线 Git revision：`35af40f5d206c1843e10bae000d9aaabf89096df`
- 衍生目录：`integrations/server/host/`、`integrations/zotero/plugin/`。
- 已保留上游 LICENSE、作者记录、插件 ID 与偏好设置前缀。
- 本地开发修改包含导读、DeepSeek JSON 重试、任务协议、原文附件关联、论文问答、红色写作批注与 Zotero 同步。
- 发行修改包含 ScholarSplit 品牌、完整安装/运行脚本、独立引擎环境、更新地址、本地配对路径和关闭上游覆盖更新。

PDF2zh-next、BabelDOC 与可选 PDF2zh 引擎由安装器从 PyPI 安装，不把用户虚拟环境或模型缓存复制进发行包。请遵守对应包的 AGPL 许可证。

`scripts/vendor-upstream.py` 是维护者的白名单摄取工具，不是用户安装命令。
再次运行会覆盖衍生文件，维护者必须重新应用并审核下游变更；不得复制运行配置、数据库、API Key 或私人论文。

ScholarSplit 独立 Chrome/工作台代码的 MIT 不替代本目录的 AGPL。
