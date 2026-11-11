# 已采用版本到 CapCut 可编辑工程

用户要求“选好版本后自动剪辑，再到 CapCut 网页微调”时使用。KK 管版本选择与剪辑记录，官方 CapCut 插件管上传、素材和云草稿；已有安装、登录继续复用。先读官方 `video-editing`、`capcut-upload`、`capcut-stdraft-builder` 和 `web-collaborative-editing` 的相关部分。脚本编译不能代替正常速度审看。

## 入口与版本锁定

浏览器制作面板的 **自动剪辑** 页展示当前集、制作版本、每镜正式采用候选和缺项。用户比较并正式采用各镜后，点击 **自动剪辑到 CapCut**，本地服务锁定本轮选择和文件 SHA-256，启动一个本机 `codex exec` 编辑任务。不是后台付费生成。一个本机同时执行一个剪辑任务，进度和网页入口留在同页；退出聊天后服务和电脑仍须运行。

预览、用户偏好、剪辑曾使用、生成成功不等于正式采用。用户明确要求 AI 自审并先剪给他看时，可建立标为「审片候选」的独立选择快照和云草稿；此操作不替用户写入正式采用状态。剪辑单只能引用本轮锁定的候选；同一候选可拆成多个画面/声音区间。同一集再次启动另建草稿，保留已有人工微调版。版本和源文件在等待中改变时停止核对，不偷换输入。

同镜多版本逐段比较：先看画面中的人物数量、身份与服装，道具状态、机位轴线、动作阶段和接镜位置，再听原句、声音衔接与有无烧入字幕。可把旧版的建立镜、另一版的动作近景、返修版的反应镜分别裁入；不要为了「两个版本都用」重复同一剧情动作或跨轴跳位。画面与声音可以来自不同版本，但须分别锁定源入出点、记录替换理由，逐镜核对口型及原声连续。每个可用片段标明来源版本、源时间、成片时间和用途；无回执的新版本只列待回片，不当作已采用源。

从当前制作上下文收集剧本、镜头设计、采用清单、已有剪辑单和校对字幕。浏览器任务目录 `input.json` 给出唯一选择、文件、指纹和用户要求；制作上下文可以通过本地 `capcut_jobs.py register-context` 预先登记。现有剪辑单与本轮选择一致时复用；不一致处重新设计和记录，不能直接照搬旧成片。

没有剧本时保留原对白，用 ASR 定位并明确核字限制；不要编造对白或把自动识别当作者原文。字幕识别/应用遵循官方 automatic-caption 工作流，不能因为识别失败转用 AI Lab。需要新的创意粗剪可调用官方 AI Lab，但仍必须拿到分片的时间线结果；只有一条合成视频的结果不满足交付。

## 画面、声音、字幕

- 每个采用的**源片**上传一次，保留 VID、文件指纹和 ffprobe 规格。整集 MP4 可以留作对照，不作为可编辑工程的唯一素材。 独立声音文件在输入快照写 `media_role: audio`，默认视频为 `video`；音频参与采用指纹与源长度验证，视觉切口扫描仅扫描实际画面源。
- 主画面保留多个独立 VIDEO，源入出点与项目时间都记录，画面自身音量为零。声音使用独立 AUDIO，保留对白完整性和 J/L-cut。只有原片混合音轨时，标记 mixed；有独立人声、环境/动作声、音乐才分别建轨，不宣称无损拆出了不存在的声源。
- 字幕使用独立 TEXT + caption_timeline，按已校对台词对齐声音。默认白字、无黑色描边（`border_width=0, border_alpha=0`），无黑色阴影。字体必须是有效 vimo URI；默认字体是兼容回退，可按项目选中文字体并实际检查字形。
- 已烧在源片中的字幕不会因为创建文字轨自动消失。优先采用干净视频；没有干净来源时明确列为待修，避免双重字幕。

## 转场与特效

可以调用 `search_capcut_material` 取得转场、视频特效、滤镜、音效、字体与文字效果。视频特效、音乐、音效按 query 搜索；转场、滤镜、字体、文字模板等目前返回固定精选目录，不能把换关键词说成搜索了完整库。使用返回的完整 `stdraft_uri`，不能猜 ID。

按切口叙事需要选择，已有生成效果不重复加。`apply_transition` 作用于同层相邻连续 VIDEO；时长须装得下两侧片段。`apply_video_effect` 和 `apply_filter` 建可删除、可调时段的独立效果段。保存成功不证明效果画面一致，检查代表切口并记录云端转换限制。付费、会员、积分确认仍按官方工作流等待用户，不代点确认。

## 固定编译器

`scripts/capcut_edit.py` 使用官方原子函数建立工程，支持独立声画区间、字幕、转场、视频特效和滤镜。传入实际安装的 `capcut-stdraft-builder` Skill 目录，不固定插件版本或本机绝对目录。

`edit-plan.json` 的最小结构（时间均为整数毫秒）：

```json
{
  "schema": 1,
  "selection_digest": "input.json 的原值",
  "width": 854, "height": 480, "duration_ms": 4000,
  "picture": [
    {"id": "P001", "candidate": "已采用候选ID", "start_ms": 0, "duration_ms": 4000, "source_start_ms": 500}
  ],
  "audio": [
    {"id": "A001", "candidate": "已采用候选ID", "role": "mixed", "start_ms": 0, "duration_ms": 4000, "source_start_ms": 500, "volume": 1}
  ],
  "captions": [
    {"audio_id": "A001", "text": "校对后的台词", "start_ms": 500, "end_ms": 2000}
  ],
  "effects": []
}
```

画幅与切点由实际项目决定，上述数字只是 schema 示例。每条字幕绑定一个明确 audio_id，必须完整落入该声音片段；跨片段语句先设计能承载完整发声的声音区间。`media-map.json` 用候选 ID 做键，每个值包含 `source: vid://...`、`duration_ms`、`width`、`height` 和 `sha256`。

效果行分别为：

```json
{"type":"transition","source":"工具返回的vimo URI","from_id":"P001","to_id":"P002","duration_ms":150}
{"type":"video_effect","source":"工具返回的vimo URI","start_ms":1000,"duration_ms":300}
{"type":"filter","source":"工具返回的vimo URI","start_ms":0,"duration_ms":4000}
```

```sh
python3 scripts/capcut_edit.py build --plan edit-plan.json --media media-map.json --builder-root /实际安装的/capcut-stdraft-builder --out draft-bundle.json
python3 scripts/cut_audit.py --plan edit-plan.json --input input.json --out cut-audit.json
python3 scripts/capcut_edit.py publish-new --bundle draft-bundle.json --receipt cloud-receipt.json
```

编译器验证连续主轨、源长度、画面静音、独立声音及字幕绑定，再只对**新建文档**赋按时间排序的稳定 ID。云导入按 ID 顺序插入主轨，随机 ID 曾导致镜序重排，仅排序 JSON 数组无效；不要为此改用户既有云工程的片段 ID。

`publish-new` 通过本机已认证 Codex App Server 调用固定官方 CapCut 草稿工具，不读取 Cookie 或导出账户凭据。先把 session_id 与 push_pending 记录到回执，再保存和回读；结果未知时恢复原会话，不重复推送或另建。它不用于覆盖已有网页工程。修改旧工程必须另按官方协作 Skill 执行 check → 最新基线 → 原子修改 → 带当前版本保存；发生冲突按该 Skill 交还人工版本并询问是否重新应用。

## 素材内部切镜与闪帧检查

外部片段顺序正确仍可能夹进一帧残片：生成源视频在 8.958 秒换构图，而出点是 9.000 秒，就会只保留下一个镜头的一帧。逐帧检查源内部的切点与最终入出点，不能只看每段开头、中间和结尾抽样。毫秒四舍五入靠近内部切点时也要留意实际源帧。

`cut_audit.py` 对锁定源片进行 scene 定位，并报告实际采用区间中不超过 0.5 秒的头、尾及内部镜头片段。这个数是定位范围，不是自动删短镜的规则。快运动、放大和明暗变化也会触发检测；查看切点前、当帧、后帧后，将每项填写 `review`（intentional / false_positive / repaired）及具体 `note`。改剪辑单后重新运行；`publish-new` 和自动任务的最终验收均拒绝旧报告或未审项目。

有意的短回忆可保留，偶然的一两帧新构图应从画面入出点去掉。声轨和字幕分别从完整原句保留；移动画面边界后重新核对原声对齐，必要时调整心声 J/L-cut，不能为修一帧剪掉字音或制造全局失步。

## 交付与恢复

核对云端 VIDEO/AUDIO/TEXT 数量、镜序、媒体 VID、源入点、项目时间、音量、字幕内容和描边；转场、裁切、关键帧等还要检查回读与实际画面。云转换无法表达的属性列为限制，不能标记为完整复现。任务成功状态只表示结构交付通过，创意审核和用户采用仍独立。

按官方协作 Skill 打开/复用本会话的 embed_editor_url，让用户点回执里的 **Open in CapCut**（实际标签用服务原文）微调。结束回复保留 web_editor_url 的直接链接。只有链接、退出码 0 或本地 build 成功都不是云工程交付。自动任务遇到登录、付费、保存冲突或不确定结果时在目录写 `blocker.json`（含 message，保留原 session、任务和回执），由当前 Codex 会话核对后继续，不能循环重跑。
