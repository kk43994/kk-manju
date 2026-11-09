# 脚本与数据约定

Python 3.10+；队列文件锁使用 macOS/Linux 的 fcntl。媒体检查另需本机 ffmpeg、ffprobe。脚本不调用外部生成接口，执行器用当前连接器或插件按领取结果执行。

## 1. 素材发现

```sh
python3 scripts/asset_inventory.py /绝对路径/项目资产 --output /绝对路径/盘点-r1.json
```

扫描用户指定根目录中的图片、音频和视频，输出内容指纹、大小、重复组。已有输出文件不覆盖。身份与采用状态仍需看素材和当前选择。越出指定根目录的符号链接不会被纳入。

## 2. 计划

新叙事视频计划先读[pipeline-standard.md](pipeline-standard.md)的director_contract约定。镜头正文与首尾接口在领取时统一展开，最终完整正文才交给长度检查；asset-only计划和旧任务恢复沿原格式，不要求改写历史。

提示词任务可填`prompt_limits`：`max_chars`、`max_utf16_units`、`max_utf8_bytes`、`max_tokens`及实际`source`；token限制另需正确tokenizer给出的`token_count`。领取前自动检查；MJ当前900字符仅为内部预算。详见[提示词长度检查](prompt-length.md)。

从 assets/production-plan.template.json 建立本项目计划，避免覆盖旧版本。

顶层：
- schema_version=1；project_id、revision 为本项目稳定标识。
- authorized_kinds：本轮允许执行的 image/video/audio/edit/sample；未授权则不进入队列。
- limits.total：总并发；limits.by_kind：分类并发，未单设的种类受总并发约束。
- source_file：可选，当前剧本文件。
- dialogue_source：当前计划范围内的原文台词，源 ID → 完整文本。无对白可为空。
- jobs：按剧情和生产顺序列任务；priority 仅改变调度优先级，不改变剧情顺序。

每个任务：
- id、kind、depends_on。
- prompt_file：图/视频/音频任务的已完成正文；shot_design_file 可记录导演稿。
- parameters：当前执行端实际参数，脚本原样返回，不猜模型语法。
- references：每项 role，加 path/node_id/url/source_job 四选一。source_job 必须出现在依赖中；多输出时显式 output_index。
- review_required：该任务生成成功后是否须通过审查才解锁后继。
- delivery_channels：本任务需实际交付的 chat、canvas。
- video 的 duration_seconds、shots；每镜 id 与 duration_seconds，handles_seconds 为额外剪辑余量。
- 每镜 events 可写 id、kind、start、end，时刻相对该镜。能重叠的事件重叠排，先后依赖用 after。对白加 source_id、text；按镜序拼接须与 dialogue_source 完全一致。
- start_state/end_state：连续性关键事实的扁平字典，键名由项目定义，例如 actor_a.right_hand。
- continuity：from_job、mode=cut/continuation/time_jump、match_keys；有意变化须写 intentional_changes 的理由。
- continuation 必须依赖并引用前段真实输出。require_actual_state=true 时，派发前还需已记录 observed_end_state 与对应键一致。可独立拍的普通切镜用计划状态先行，产物出来后再查切口。

示例（仅展示任务结构，不是已生成资产）：

```json
{
  "id": "video-b",
  "kind": "video",
  "depends_on": ["character-base", "scene-base"],
  "prompt_file": "prompts/video-b.md",
  "parameters": {},
  "references": [
    {"role": "identity", "source_job": "character-base", "output_index": 0},
    {"role": "space", "source_job": "scene-base", "output_index": 0}
  ],
  "duration_seconds": 9,
  "shots": [
    {"id": "S03", "duration_seconds": 4, "events": []},
    {"id": "S04", "duration_seconds": 5, "events": []}
  ],
  "review_required": true,
  "delivery_channels": ["chat", "canvas"]
}
```

实际镜头、时长和引用都按项目填；示例 9 秒不构成默认值。脚本只校验可计算条件，分镜正文仍由两套规范审查。

## 3. 校验、领取与恢复

需要先审完整提示词、文字站位图和转场动画计划时，运行`python3 scripts/production_contract.py compile /项目/plan-r1.json --output /项目/compiled-r1`。该命令不创建队列状态、不派发生成，按同一计划输出prompts、调度与转场.md、manifest。之后audit/init/claim沿用原流程；claim封存同一展开正文和用到的blocking_states。

```sh
python3 scripts/pipeline.py audit /项目/plan-r1.json
python3 scripts/pipeline.py init /项目/plan-r1.json --state /项目/state-r1.json
python3 scripts/pipeline.py status /项目/plan-r1.json --state /项目/state-r1.json
python3 scripts/pipeline.py claim /项目/plan-r1.json --state /项目/state-r1.json --limit 2
```

--state 不指定时为计划同目录 production-state.json；多版本建议始终显式命名。claim 在文件锁内重新算容量并原子写入，避免重复领取；ready 只供预览，不预留名额。

返回 claimed 中的 request_key、request_file、prompt_file、parameters、duration_seconds 和 references。request_file 封存领取时的完整提示词和执行输入，执行与恢复优先读取它，不能用后来编辑过的正文替换原请求。source_job 引用在领取时解析为已登记实际输出；本地输出附内容指纹，文件被替换后不继续引用。执行器核对当前服务的真实项目、参数、引用角色及限额，再调用工具。独立调用可用 Promise.allSettled；逐条处理所有结果。

请求键绑定计划和输入指纹，重复 init 不重置状态。修改计划、提示词或已引用文件后，旧状态拒绝继续派发；保留旧回执，建立新修订，明确复用哪些已有输出。已经发出的原任务仍可按封存计划登记结果、审查和交付，输入变更不能丢掉原任务回执。

## 4. 记录原始执行结果

将真实工具结果规范化为本地 JSON；不得猜测 task_id 或成功输出。保留原始回执文件以便恢复：

```json
{
  "task_id": "真实平台任务ID或local:本地任务ID",
  "request_key": "领取时返回的请求键",
  "outputs": [
    {"node_id": "实际返回节点ID", "url": "实际返回可访问地址"}
  ]
}
```

提交或运行中可暂时无 outputs；成功必须有真实输出。路径相对计划目录或使用绝对路径。回执文件不要含凭据。

```sh
python3 scripts/pipeline.py record /项目/plan-r1.json --state /项目/state-r1.json --job video-b --status submitted --receipt /项目/receipts/video-b-submit.json
python3 scripts/pipeline.py record /项目/plan-r1.json --state /项目/state-r1.json --job video-b --status succeeded --receipt /项目/receipts/video-b-result.json
```

状态支持 submitted/running/succeeded/failed/unknown。unknown 占位且不会自动重发，继续查原任务后再记录。相同任务不能换成另一 task_id，成功结果不能被覆盖成另一个输出。

确有证据证明仅本地领取、尚未发外部请求时：

```sh
python3 scripts/pipeline.py release /项目/plan-r1.json --state /项目/state-r1.json --job video-b --not-submitted-evidence /项目/notes/not-submitted.md
```

## 5. 媒体检查、审查和交付

```sh
python3 scripts/media_probe.py /项目/video-b.mp4
python3 scripts/media_probe.py /项目/video-b.mp4 --frames /项目/review/video-b-r1
python3 scripts/media_probe.py /项目/video-b.mp4 --frames /项目/review/video-b-action --times 0,1.4,3.1
```

默认抽取开头、中间、接近结尾的真实帧，并记录规格。时间须在实际片长内；帧输出目录应为空或尚不存在。标注帧用于自审，不自动绑定成新参考。

### 成片完整解码与交付比对

`export_verify.py`读取文件，枚举真实解码帧、检查PTS，并完整解码首路视频与全部音轨；它不改媒体、不补帧。参数从本版剪辑单取得。下面的帧数/帧率只是一次测试例，不作为项目默认值：

```sh
python3 scripts/export_verify.py /项目/成片.mp4 \
  --expected-frames 3916 --expected-fps 24 --expected-duration 163.166667 \
  --report /项目/review/导出核验-r1.json
python3 scripts/export_verify.py /项目/官方原文件下载.mp4 \
  --expected-frames 3916 --expected-fps 24 --match-file /项目/已审成片.mp4 \
  --report /项目/review/交付核验-r1.json
```

只在项目要求CFR时传`--expected-fps`（支持`24000/1001`等分数）；未指定时不把可变帧间隔判成丢帧。预期时长按视频可见范围而非AAC填充后的容器时长，检查容差为预期半帧，未给帧率时为1毫秒。`--match-file`只验证原文件字节相同，平台转码交付另记派生关系，不要求哈希相同。

JSON区分声明帧数、解码帧数、时间戳、完整解码和字节比较；错误诊断返回非零，已有报告不覆盖。默认解码线程为2，可按机器负载调整。PTS与帧数通过仍可能存在重复画面、源动作僵硬或声画不同步；字幕/姓名牌、嘴型、音色、转场与审美不在该脚本的自动判定范围，按[成片规范](post-production-standard.md)播放检查。

### 登记审查与交付

```sh
python3 scripts/pipeline.py review /项目/plan-r1.json --state /项目/state-r1.json --job video-b --decision passed --by assistant --note-file /项目/review/video-b.md --observed-state /项目/review/video-b-end.json
python3 scripts/pipeline.py deliver /项目/plan-r1.json --state /项目/state-r1.json --job video-b --channel chat --evidence-file /项目/delivery/video-b-chat.md
python3 scripts/pipeline.py deliver /项目/plan-r1.json --state /项目/state-r1.json --job video-b --channel canvas --evidence-file /项目/delivery/video-b-canvas.md
```

--by=user 只能记录用户真实决定，不能替代助手自审。审查失败保留成功生成回执，后继仍阻塞。chat/canvas 必须在实际展示/同步后登记；下载不等于发送。

status 返回 active、pending_review、pending_delivery 和每个未就绪任务的具体原因，供控制循环继续处理。脚本不会自动发送聊天消息、同步画布、付费生成或判定艺术质量。

## 6. 图片观察对比

需要Pillow；脚本按原文件字节计算SHA-256并完整解码，处理EXIF显示方向，等宽缩放后排版。原文件不修改；输出目录必须是新目录，避免覆盖历史。它不做降噪、调色、AI重绘或艺术验收。

```sh
uv run --no-project --with pillow python scripts/image_compare.py \
  --image '原图' /项目/原图.png \
  --image '新版源图' /项目/新版源图.png \
  --image '处理结果' /项目/处理结果.png \
  --output /项目/review/对比-r2
```

输出comparison.jpg、逐图preview-XX.jpg、manifest.json（来源、标签、字节数、指纹、原始/显示/预览尺寸）。预览保持比例；透明图在预览中合成到白底，原图不变。可用--columns、--width、--font调整排版。同显示宽度不保证同主体大小；比较皮肤与发丝前仍需人工核对景别、光照、干湿和压缩。提交原图应使用原文件，不把带标签的对比图自动挂成下一轮参考。

## 7. 独立声画剪辑与字幕

`render_split_edit.py`用于原速剪辑，输入`fps`、`size`、`sources`（path/sha256）、`picture`和`audio`。坐标为本版fps下的帧数，出点为不含端点；真实素材须同尺寸、帧率。每个画面项写id/source/in_frame/out_frame，另可写overlap_frames与明确transition；可选crop为宽、高、x、y。每个声音项独立写id/source/in_frame/out_frame/at_frame，可设fade_in_seconds、fade_out_seconds、gain_db。speech_guards是源音轨秒数区间，禁止边缘裁切或淡化穿过该区间；不自动判断台词、音色或混音合适。black_tail_frames仅用于明确设计的片尾黑场，不补冻结画面。

```sh
python3 scripts/render_split_edit.py /项目/edit-r3.json /项目/无字-r3.mp4
python3 scripts/subtitle_layer.py /项目/无字-r3.mp4 /项目/cues-r3.json /项目/style-r3.json /项目/字幕-r3.mp4
```

字幕脚本需Pillow；cues是按顺序排列的start/end/text数组，时刻为最终成片秒数。style可直接包含或在subtitle对象内配置font_file、font_size_at_480p、bottom_margin_at_480p、punctuation。字体须真实存在；当前为单行白字、轻描边/阴影，超宽或重叠时间轴会停止，不静默缩小或丢字。无libass时以透明RGBA条带合成；保留SRT、逐句图层和音轨包指纹。正式源台词先校正，输出显示文本与SRT应一致。新输出另存，最后运行export_verify并检查实际字幕与声画接缝。

## 8. 验证边界

`delivery_save.py`接受平台正式交付清单并原子保存、核对文件；见[pipeline-standard.md](pipeline-standard.md#6-固定命令与职责边界)。图片完整解码需Pillow；用`uv run --no-project --with pillow python`运行。不再为每批图片另写下载循环。

本包本地测试覆盖依赖循环、并发抢占、unknown 保留、审查依赖、输入变更、对白/时间检查和独立交付状态。ffmpeg 的合成素材测试只验证抽帧工具能工作，不代表任何用户剧情视频通过审查。
