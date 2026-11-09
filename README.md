# KK 漫剧

KK 漫剧 Skill 与 LibTV 浏览器制作面板。按剧本组织导演设计、图片资产、视频提示词、依赖调度、检查返修与剪辑交付，保留真实任务回执、素材版本和人工采用。

本次版本：**Skill 1.5.1 / 浏览器扩展与伴随面板 0.6.0**。

[完整 Skill 安装包（内含扩展）](https://github.com/kk43994/kk-manju/releases/download/v1.5.1/KK-Manju-Skill-1.5.1.zip) · [浏览器扩展单独下载](https://github.com/kk43994/kk-manju/releases/download/v1.5.1/KK-Manju-Browser-0.6.0.zip) · [所有发布版本](https://github.com/kk43994/kk-manju/releases) · [SHA-256 校验清单](https://github.com/kk43994/kk-manju/releases/download/v1.5.1/SHA256SUMS.txt)

## 首次安装

需要安装两项：**KK 漫剧浏览器扩展**与 **LibTV 官方 Codex 插件**。安装 Skill 后第一次调用时，助手先检查当前安装与连接，只补缺项，再进入制作。复制 Skill 不会自动执行命令。

1. 下载完整 Skill 安装包，解压后将 `kk-manju` 目录安装到 Codex 的 Skill 目录，默认是 `~/.codex/skills/kk-manju`；从源码安装时使用 `skills/kk-manju`。已有同名目录时先保留旧版并对照合并，保留项目资料、草稿和任务记录。
2. 安装 LibTV 官方插件，在 Codex 中完成本人 LibTV 账户的 OAuth 授权：

   ```sh
   codex plugin marketplace add https://github.com/liblib-ai/marketplace.git --ref main
   codex plugin add libtv@libtv
   ```

   命令见 [LibTV 官方说明](https://github.com/liblib-ai/marketplace/blob/main/README.md)。装完重新加载插件或开启新会话。

3. 在已安装的 Skill 目录发布浏览器扩展，并启动本机服务：

   ```sh
   cd ~/.codex/skills/kk-manju
   python3 scripts/extension_release.py --source assets/companion/extension
   python3 scripts/companion.py --skill-root "$PWD" start
   ```

4. Chrome 打开 `chrome://extensions/`，启用开发者模式，选择“加载已解压的扩展程序”，选择本机桌面的 `KK漫剧-LibTV扩展` 目录。在本机面板复制配对码，填入扩展的连接设置。
5. 在 LibTV 画布检查 KK 入口及浏览器 / Codex / LibTV 三路状态，再同步所选项目。首次检查只读，不试投付费生成。

完整步骤、更新命令与故障定位见[首次安装与连接](skills/kk-manju/references/first-use-setup.md)。浏览器源文件在[随包扩展目录](skills/kk-manju/assets/companion/extension/)，制作规范入口为 [SKILL.md](skills/kk-manju/SKILL.md)。

## 当前能力与边界

- 浏览器面板支持选集、分镜视频预览、候选比较、问题记录和明确的本地采用；项目、画布与个人资产通过本地 Codex 的官方 LibTV 读取工具同步。
- 原生整理与分组保留精确项目和节点身份，持久化回读后才登记完成。同步和本地采用不会自动替换平台成片。
- 实际生成由当前 Codex 工作流与获授权的 LibTV 工具执行，扩展不会在后台自行启动模型创作。安装、连接、平台受理、媒体完成、审查通过及人工采用分别记录。
- 当前为 Chrome 开发者模式扩展，未发布 Chrome 扩展商店。Python 3.10+、已登录的 Codex 和已授权的 LibTV 插件用于本机服务；媒体检查另需 ffmpeg、ffprobe。当前本机服务采用 macOS/Linux 文件锁，Windows 原生运行尚未验证。

## 验证与打包

测试使用合成数据或临时目录，不要求真实账户、画布或付费生成：

```sh
python3 -W error::ResourceWarning -m unittest discover -s skills/kk-manju/tests -v
node --test skills/kk-manju/tests/test_adapter.cjs skills/kk-manju/tests/test_updater.cjs
python3 scripts/build_release.py
```

部分媒体测试需要 ffmpeg、ffprobe 与 Pillow。ASR 是可选制作步骤，另需在本机准备 faster-whisper、NumPy 与对应模型缓存；安装引导和面板启动不需要加载 ASR 模型。

打包脚本产出完整 Skill ZIP、浏览器扩展 ZIP 及 SHA-256 清单。Skill ZIP 已包含扩展；浏览器扩展 ZIP 便于单独查看和安装，仍依赖完整 Skill 的本机服务。打包完成与浏览器实际加载分别验证。
