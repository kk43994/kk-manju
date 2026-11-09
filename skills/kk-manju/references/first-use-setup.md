# 首次安装与连接

发布入口：[KK 漫剧 GitHub 仓库](https://github.com/kk43994/kk-manju)与[版本下载页](https://github.com/kk43994/kk-manju/releases)。当前完整包为 [Skill 1.5.1（内含浏览器扩展）](https://github.com/kk43994/kk-manju/releases/download/v1.5.1/KK-Manju-Skill-1.5.1.zip)，另提供[浏览器扩展 0.6.0 单独下载](https://github.com/kk43994/kk-manju/releases/download/v1.5.1/KK-Manju-Browser-0.6.0.zip)。单独的浏览器扩展仍需完整 Skill 的本机服务。

首次调用 KK 漫剧 Skill 时先完成本页引导。已安装者检查当前状态，只补缺项；更新 Skill 后不要求重复登录和配对。安装命令由助手在已获授权的环境执行，浏览器加载扩展和账户授权需要使用者在界面完成时，提供准确步骤。Skill 文件没有安装钩子，不能声称“下载后已自动装好插件”。

## 需要安装的两项

| 组件 | 安装到哪里 | 用途 |
| --- | --- | --- |
| KK 漫剧浏览器扩展 | Chrome；其他 Chromium 浏览器须单独验证 | LibTV 网页里的制作面板、看片比较、问题与本地采用记录，以及当前画布上下文 |
| LibTV 官方插件 `libtv@libtv` | Codex | 使用本人 LibTV OAuth 授权读取项目和资产，提供当前平台允许的制作工具 |

当前随包浏览器扩展及本机服务为 `0.6.0`。检查包内 `manifest.json` 和实际连接状态，不把文档版本当作已经加载的版本。浏览器扩展不是 LibTV 官方插件；安装任意一项均不代表另一项就绪。

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

以下命令在实际安装的 `kk-manju` Skill 目录执行；自定义安装位置以真实位置为准：

```sh
python3 scripts/extension_release.py --source assets/companion/extension
python3 scripts/companion.py --skill-root "$PWD" start
```

第一条将完整扩展发布到本机桌面的 `KK漫剧-LibTV扩展` 固定目录，并登记文件指纹；后续继续使用同一加载目录。第二条启动 `127.0.0.1:18765` 本机服务，复用同版本同实例服务，不覆盖其他实例。运行环境按现有脚本要求准备 Python 3.10+；媒体检查/预览另需 ffmpeg、ffprobe。当前本机服务采用 macOS/Linux 的文件锁，Windows 原生运行尚未验证，不承诺通用安装成功。

在 Chrome 打开 `chrome://extensions/`，启用“开发者模式”，选择“加载已解压的扩展程序”，选择上述固定目录。它直接包含 `manifest.json`，不要选择 ZIP 或上层目录。当前使用本机开发者模式加载，GitHub 下载不等于 Chrome 扩展商店安装。

按启动命令返回的 URL 打开本机面板，在“浏览器扩展配对”复制本机配对码，填入扩展“连接设置”。配对码只在本机使用，不写入公开日志、GitHub 或聊天。回到已有 LibTV 画布，检查右下角 KK 入口；保留未保存内容，必要的页面刷新由使用者在保存后处理。

## 3. 核对连接，再进入制作

分别核对三路真实证据：

- **浏览器**：扩展已启用，当前 LibTV 画布有近期心跳，报告实际扩展版本及准确项目 ID。
- **Codex**：本地 Codex 已登录，伴随服务连接检查成功；仅终端存在 `codex` 命令不足以证明登录。
- **LibTV**：官方 `doctor` 返回当前授权状态；平台可执行能力以当次 `executionAdmission` 为准。用户已选项目时再只读回读该项目，不能以工具注册或缓存目录代替授权。

需要在面板同步时，使用“同步 LibTV”并检查真实返回。连接检查只读，不创建测试节点、不试投生成、不用付费任务探测能力。未连接项显示具体原因，修复该项后再检查，不重复重装全部组件。已有未知任务沿原任务身份恢复。

两款插件与所需连接就绪后，恢复当前项目的剧本、素材与回执，按制作流程推进。文字设计可在连接补齐前继续；依赖浏览器或 LibTV 的制作步骤等待对应连接。安装完成、可读取项目、允许生成、实际生成成功和用户采用分别记录。
