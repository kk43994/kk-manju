# 首次安装与连接

完整 Skill 从 [KK 漫剧版本下载页](https://github.com/kk43994/kk-manju/releases)获取。浏览器扩展与配套服务使用[官方下载页](https://kk666.best/asset-lab/#browser-extension)上的 **0.9.0 自动更新版**：[完整配套包](https://kk666.best/asset-lab/downloads/KK-Manju-Companion-0.9.0.zip)、[独立扩展](https://kk666.best/asset-lab/downloads/KK-Manju-Browser-0.9.0.zip)、[安装说明](https://kk666.best/asset-lab/browser-extension.html)。独立扩展仍需配套本机服务。已安装云端自动更新入口时，检查实际当前版本，不用旧文档中的固定 ZIP 覆盖新程序。

用户交来剧本时先按[剧本接收与决策引导](guided-production.md)读取内容、整理已知选择和制作建议，在进入依赖平台的实际制作前完成本页引导；仅整理剧本或讨论流程不要求先安装。用户专门要求安装时直接处理本页。已安装者检查当前状态，只补缺项；更新 Skill 后不要求重复登录和配对。安装命令由助手在已获授权的环境执行，浏览器加载扩展和账户授权需要使用者在界面完成时，提供准确步骤。Skill 文件没有安装钩子，不能声称“下载后已自动装好插件”。

## 需要安装的两项

| 组件 | 安装到哪里 | 用途 |
| --- | --- | --- |
| KK 漫剧浏览器扩展 | Chrome；其他 Chromium 浏览器须单独验证 | LibTV 网页里的制作面板、看片比较、问题与本地采用记录，以及当前画布上下文 |
| LibTV 官方插件 `libtv@libtv` | Codex | 使用本人 LibTV OAuth 授权读取项目和资产，提供当前平台允许的制作工具 |

当前公开自动更新版为 `0.9.0`。检查包内 `manifest.json`、服务健康状态、`cloudUpdate` 和近期扩展心跳，不把文档版本当作已经加载的版本。浏览器扩展不是 LibTV 官方插件；安装任意一项均不代表另一项就绪。

## 1. 安装并授权 LibTV 官方插件

先检查当前会话是否已有 LibTV 工具和可用授权；已经可用则跳到下一项。未安装时，在安装 Codex CLI 的环境执行官方命令：

```sh
codex plugin marketplace add https://github.com/liblib-ai/marketplace.git --ref main
codex plugin add libtv@libtv
```

命令来源：[LibTV 官方安装说明](https://github.com/liblib-ai/marketplace/blob/main/README.md)。先用 `codex plugin --help` 核对本机是否支持；命令不存在时按官方安装页和当前客户端可用入口处理，不编造 `/plugin install` 命令。

装完重新加载插件或开启新会话，在 Codex 的 MCP 连接管理里为 `libtv` 完成本人 LibTV OAuth 登录。每位使用者独立授权；不索取或复制他人的 Cookie、token、client ID 或回调配置。保留当前聊天与未保存草稿，不为生效安装而刷新 LibTV 画布。

更新已有插件时使用：

```sh
codex plugin marketplace upgrade libtv
codex plugin add libtv@libtv
```

旧包冲突时先核对官方说明和实际配置，不把卸载现有连接作为常规首次安装步骤。

## 2. 安装 KK 漫剧浏览器扩展

完整 KK 漫剧 Skill 内含扩展，目录为 `assets/companion/extension/`。发布包必须含 `manifest.json`、`build-info.js`、脚本、页面及 `release.json`；不能让新用户使用作者电脑的桌面路径。若下载包缺少这些文件，获取包含扩展的完整发布包再继续，不把缺包报告为已安装。

首次启用云端自动更新时，下载并解压上方完整配套包，在该包目录执行一次；自定义安装位置以真实位置为准：

```sh
python3 install_cloud_update.py
```

安装器登记原状态目录和固定扩展加载目录，备份原 Skill 启动入口，并启动 `127.0.0.1:18765` 托管服务。自定义位置用 `--skill-root`、`--state-dir`、`--extension-dir` 指定原目录，不更换扩展路径或重新创建数据库。0.8.1 及更早用户先保存编辑、等待制作与剪辑结束，再核对并停止原本机服务后迁移一次；不删除原扩展或配对。已托管者沿用现有 `scripts/companion.py start` 启动入口，无需重复安装。

托管服务运行时约每 5 分钟从固定 kelao HTTPS 来源检查、下载和校验新版，空闲后自动切换；新程序启动失败退回上一程序版本，不还原用户数据库。浏览器未保存编辑仍暂缓重载。电脑休眠或服务停止期间不检查，恢复运行后继续。Python 3.10+、ffmpeg / ffprobe 等依赖沿用原要求；当前 macOS 链路已验证，Windows 原生运行尚未验证。

在 Chrome 打开 `chrome://extensions/`，启用“开发者模式”，选择“加载已解压的扩展程序”，选择上述固定目录。它直接包含 `manifest.json`，不要选择 ZIP 或上层目录。当前使用本机开发者模式加载，GitHub 下载不等于 Chrome 扩展商店安装。

按启动命令返回的 URL 打开本机面板，在“浏览器扩展配对”复制本机配对码，填入扩展“连接设置”。配对码只在本机使用，不写入公开日志、GitHub 或聊天。回到已有 LibTV 画布，检查右下角 KK 入口；保留未保存内容，必要的页面刷新由使用者在保存后处理。

## 3. 核对连接，再进入制作

分别核对三路真实证据：

- **浏览器**：扩展已启用，当前 LibTV 画布有近期心跳，报告实际扩展版本及准确项目 ID。
- **Codex**：本地 Codex 已登录，伴随服务连接检查成功；仅终端存在 `codex` 命令不足以证明登录。
- **LibTV**：官方 `doctor` 返回当前授权状态；平台可执行能力以当次 `executionAdmission` 为准。用户已选项目时再只读回读该项目，不能以工具注册或缓存目录代替授权。

需要在面板同步时，使用“同步 LibTV”并检查真实返回。连接检查只读，不创建测试节点、不试投生成、不用付费任务探测能力。未连接项显示具体原因，修复该项后再检查，不重复重装全部组件。已有未知任务沿原任务身份恢复。

两款插件与所需连接就绪后，恢复当前项目的剧本、决定、素材与回执，按制作流程推进，不重新询问已确定方向。文字设计可在连接补齐前继续；依赖浏览器或 LibTV 的制作步骤等待对应连接。安装完成、可读取项目、允许生成、实际生成成功和用户采用分别记录。
