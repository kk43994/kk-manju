# KK 漫剧制作管线标准

版本2，2026-11-07。管线协调两套独立规范，不将导演决策和模型提示词混为一套。用于整集、多段叙事视频制作；单张资产、讨论和旧任务恢复不强制补全整集计划。

新增执行要求见[文字站位与转场动画规范](blocking-transition-standard.md)：新叙事视频启用blocking_maps与transition_animation；每镜起终点对应文字俯视状态图，每个跨视频切口具有成对的实际动画设计。动画的运动载体、交界画面、方向、时长与执行归属不能只写“自然转场”。旧回执按原快照恢复。

## 1. 流程与单一来源

| 阶段 | 主要产物／工作 | 进入下一阶段前确认什么 |
|---|---|---|
| 剧本与导演设计 | 整集拍摄剧本、逐镜动作与摄影、对白和心声、切口、跨镜状态 | 观众能看懂因果，镜头能执行；按用户当前的审阅权限处理采用 |
| 资产盘点与补缺 | 角色身份、衣装、空间多视图、道具形制/尺度、色卡、必要界面 | 真实图片已看过、用途明确；生成成功与视觉采用分开 |
| 镜头结构化 | plan.json的shots、events、start_state/end_state | 稳定镜号、逐镜职责与动作；不能只有台词 |
| 提示词编译 | STYLE LOCK、有序参考、逐镜正文、声音、首尾承接 | 保留导演稿；最终展开正文通过当前长度计量 |
| 执行与并发 | pipeline.py领取，服务工具提交，原Task回读 | 已授权种类、真实参考、当前模型能力；未知请求不重发 |
| 产物审查与返修 | 播放/抽帧、听音、相邻切口、问题定位 | 剧情、表演、物理、地理、声音、节奏逐项成立 |
| 剪辑与交付 | 选择可用入出点、混音、统一字幕、正式下载与发送 | 实际解码与原速观看，聊天和原画布分别有交付证据 |

剧本、镜头、生成段落是三个不同单位。先设计整集，再按真实模型能力与戏剧切点装入生成任务；不把整集先均分成15秒/30秒再填戏。需要变更摄影/台词时回到来源修订，同步受影响镜头、资产状态与相邻接口，保留旧版本。

## 2. 什么代码化，什么仍需创作判断

代码负责：输入指纹、镜号覆盖、原句拼接、说话人合法值、时间容量、参考存在、任务依赖、成对首尾状态、并发占位、幂等键、回执恢复、长度拦截、下载及基础媒体校验。

导演负责：这镜想表达什么、观众先看哪里、人物如何反应、采用什么机位和运动、为何切、切后增加什么信息。观看负责：实际动作是否自然、演技和情绪是否成立、是否好看。结构检查通过不等于后两者通过。禁止用脚本自动给艺术质量打“已通过”。

## 3. 新叙事视频计划的director_contract

沿用现有plan.json与pipeline.py，不再建一套调度器。图像任务仍沿用现有格式；新叙事视频任务增加以下顶层字段：

```json
{
  "director_contract": {
    "version": 1,
    "inputs": {
      "script": {"file": "导演稿.md", "sha256": "真实文件字节SHA256"},
      "design": {"file": "导演稿.md", "sha256": "真实文件字节SHA256"}
    },
    "cast": {"lead": "项目角色规范名"},
    "forbidden_terms": [],
    "video_order": ["V01", "V02"],
    "shot_order": ["SH001", "SH002"],
    "transitions": []
  }
}
```

此处是字段说明，示例哈希不是可执行值。用户要求整合拍摄剧本时，script与design可以指向同一文件；两套专业规范仍独立。已有独立剧本和分镜文件则分别引用。使用真实当前文件计算哈希；文件变化必须开修订，不能仅把哈希改成新值就假装下游设计已经复审。

`cast`是角色ID到规范中文名；对白事件必须带`speaker`及`voice_mode`（dialogue/inner/voice_over）。如填`speaker_name`，须与规范名一致。已确认的常见错名可以放`forbidden_terms`，检查对白和最终提示词，不扫描审查报告里的错误引用。此项不能判断实际生成音频的发音；仍要听成片。

每镜在已有id、duration_seconds、events之外，至少提供：

- `purpose`：本镜改变什么。
- `camera.framing`、`camera.movement`、`camera.motivation`：景别/观看位置、完整运动或固定方案、选择理由。
- `action`：人物起点→刺激/行为→终点，含真实持物与接触关系。
- `cut`：切点、后镜增加的信息、承接元素。

以上文字须由导演设计后填写，脚本不会发明运镜。`shot_order`必须与`video_order`逐段串接的镜号完全一致，发现遗漏/重排会阻止领取。现有dialogue_source与events继续核对原句拼接，切镜跨句时按实际发声分段，不能把整句在前后都写一遍。

## 4. 每条视频的尾与下一条的头成对设计

每个相邻段落共用一个transition记录；前条与后条不得各写一套互不对应的“自然转场”。末条写本集退出状态，下一集确定后再建立跨集接口。

```json
{
  "id": "T01",
  "from_job": "V01",
  "to_job": "V02",
  "method": "action_match",
  "reason": "保持同一次抬手，省去动作重演",
  "tail_instruction": "角色右手上提到胸前，在动作仍继续时切出。",
  "head_instruction": "从右手胸前的上提中段接入，继续同一次动作。",
  "sound": "衣料声连续，不重复上一条对白。",
  "state_keys": ["lead.right_hand", "prop.owner", "prop.height_cm"],
  "intentional_changes": {},
  "handles": {"out_seconds": 0.4, "in_seconds": 0.4}
}
```

`method`支持action_match、eyeline_match、sound_bridge、occlusion_cut、establishing_cut、time_jump。按叙事选择，不默认加溶解/光闪，不给每段强加黑场、停帧或慢动作。虚构示例的0.4秒不是项目固定值。

`state_keys`从前job.end_state与后job.start_state核对；道具尺度、持物手、位置、支撑、视线、光、情绪/已知信息按该切口选必要项。明确换场可通过`intentional_changes`逐项解释位置等变化；时间跳切不等于道具或身份可以无故变化。

`handles_seconds`是job.duration_seconds中除镜头有效时长以外的生成余量。两侧transition分配的余量总和不得超过它，镜头总时长加余量必须等于生成任务时长。镜头和余量必须符合实际模型时长范围，实时get_model后再分组；不能为了满足档位拖慢台词。余量继续同一运动，供后期择切，不是新增一秒静止。

普通剪接可以共享计划并发拍摄；真正的无缝动作续接若需依赖实际尾帧，使用既有continuation依赖和真实前段产物，等待审查后才能提交。计划匹配不等于实际素材已接得上。

## 5. 固定编译槽位

新增固定审阅入口：`python scripts/production_contract.py compile 项目/plan.json --output 项目/compiled-r1`。导出最终提示词、文字调度图及动画执行单；不提交任务、不渲染视频。claim继续封存同一编译正文和所用站位图，执行器按request_file提交。

提示词模板按既定完整正文版式写STYLE LOCK、参考绑定、全段摄影/光声约束，并在合适位置各放一次：

```text
[STYLE LOCK]
本项目已审阅的风格与成像要求

有序参考职责与真实Node引用

{{TRANSITION_IN}}

{{SHOT_DESIGN}}

{{TRANSITION_OUT}}
```

`production_contract.py`从同一份shots原样编入动作、摄影、切镜与声音时间线；不自动决定镜头。它只把同一个transition的head写进后一条、tail写进前一条。首条无incoming时留空、末条无outgoing时留空。模型引用仍用真实`{{Node nodeId}}`，编译槽位不占用平台的图像标签编号。

pipeline在audit和claim时对**展开后的完整提示词**计量；claim封存最终正文、导演镜头与两侧接口。提交执行器必须读取封存request_file，不能转而读取未展开的模板或临时追加一段未计量的文字。

字段齐全只能证明必要设计存在于输入；不能证明其理由正确，也不能从“同轴、自然”几个字推断执行成立。摄影移动的起点、触发、路径、速度和终点仍按镜头规范由人写全。

## 6. 固定命令与职责边界

继续使用automation.md里的audit→init→claim→record→review→deliver；不复制一份临时调度脚本。脚本不调用付费生成，执行仍通过现有平台工具。对外发送、生成与采用各遵循本轮用户授权。

LibTV正式下载增加固定入口：

```sh
uv run --no-project --with pillow python scripts/delivery_save.py delivery.json --output 本轮交付目录
```

输入是prepare_project_delivery返回的ready/awaiting_host_save清单，不是自己拼的原件链接。脚本拒绝危险文件名、重复名、未就绪清单和覆盖未知旧文件；检查大小、可用的真实contentHash，并完整解码图片。sourceIdentity是来源身份，不能当作字节SHA256。结果保存在delivery-local.json；图片之外的音视频仍使用现有export_verify.py并原速播放。脚本成功不记录聊天已发送。

各文件存项目本轮目录；公共skill只放通用方法，不放角色私密素材、账号、票据、签名链接或项目源剧本。新管线校验不会重写旧任务状态，旧受理任务沿原键和快照恢复。

对话附件需要上传时，使用upload_ticket取得票据后，`python scripts/upload_parts.py ticket.json --file 实际原件 --receipt put.json`固定直传；多分片必须显式传入申请票据时的同一part_size。该脚本清空urllib默认表单Content-Type，避免破坏OSS签名，并记录每片ETag和源字节指纹。它不调用upload_complete或upload_media；PUT后继续用原uploadId/path完成，再以返回的file_ref挂图。票据和含签名链接的清单只放私有本轮目录，不纳入公共Git。

## 7. 本轮收敛的资产派生原则

已定人物转多视图时，默认用简短的“参考原图人物，保持画风/身份/衣装/材质，只改变视角与版式”。补充必要的无脸全身、大头、全脚入画与朝向约束即可。不要一边要求保持风格，一边另加painterly、引擎或摄影风格重新定义媒介。探索新风格与资产派生属于不同任务。

长提示词曾使角色变成平涂，只能说明这次指令与目标矛盾；简短修订仍须看实际结果，不能沉淀为“字越少必然越好”的结论。

2026-11-07的多视图修订中，已核对正确的单张高清源图与图生图参数，短指令仍发生画风/身份漂移，用户退回。此案例记录为失败，不提供成功配方；后续先验证单视角保真再扩展是候选诊断路线。形制、身份、画风和版式分别检查，不能把版式合格当成资产可用。
