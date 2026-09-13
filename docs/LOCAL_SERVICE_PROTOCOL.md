# ScholarSplit 本地服务接口

扩展固定连接 `http://127.0.0.1:8890`。兼容服务需要实现以下接口，并且只绑定回环地址。

## 健康检查

`GET /health`

返回 JSON，`status` 为 `ok`，`capabilities` 同时包含：

- `readingGuideV1`
- `serverDeepSeekProfileV1`

## 翻译任务

`POST /translate`

请求包含 Base64 PDF、文件名、语言、输出模式和 `asyncJob: true`。响应返回 `taskId`。

## 导读任务

`POST /guide`

请求包含 Base64 PDF、文件名、标题和 `asyncJob: true`。响应返回 `taskId`。导读结果应包含研究问题、理论、方法、统计、发现、贡献、局限、概念与阅读路径。

## 任务状态

- `GET /api/tasks`
- `GET /api/history`

扩展按 `taskId` 查找活动任务或历史结果。

## 译文读取

`GET /translatedFile/<filename>?preview=true`

必须返回完整 PDF。扩展会再次校验 `%PDF-` 与 `%%EOF` 后生成浏览器内 Blob URL。

## 安全要求

- 不接受来自远程地址的服务端模型配置调用。
- 不在任何响应、日志或任务记录中返回 API Key。
- 文件名必须去除路径并防止目录穿越。
- 跨域配置不得使用无条件的 `Access-Control-Allow-Origin: *`。
