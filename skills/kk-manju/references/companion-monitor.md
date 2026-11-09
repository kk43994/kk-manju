# 制作伴随面板与固定执行入口

首次使用先完成[两款插件的安装与连接](first-use-setup.md)。本地面板展示真实状态、脚本、日志、问题与媒体，并保存使用者明确执行的本地采用记录；LibTV 保留素材、连线、生成和剪辑画布。浏览器配套扩展读取当前项目和选择项、提供看片比较、记录问题、调用已启用的原生整理按钮。它不是官方 LibTV 插件，也不借用平台 Cookie 或复制 CLI 凭据。

已登录并授权时，面板通过本地 Codex App Server 的官方 LibTV 读取工具同步项目、当前画布和个人资产。活跃面板可后台增量核对，具体结果以真实同步回执为准；不启动模型回合、创作任务或付费生成。同步失败保留旧快照，账号与项目分别隔离，本地采用及问题不被同步改写。

## 启动与执行

实际制作或管线维护开始时，先执行 `python3 scripts/companion.py start`。按返回 URL 打开本地状态页；已有同版本、同数据目录服务则复用。讨论或单纯写提示词不用启动。服务仅监听 `127.0.0.1:18765`，数据在 `~/.local/share/kk-manju-companion/`。无需浏览器也能记录脚本；没有通过统一入口的进程不会自动出现在面板。

登记真实项目：

```sh
python3 scripts/companion.py project --id PROJECT_ID --name '项目名称'
```

固定脚本使用同一执行入口；参数全部位于 `--` 后，传参列表不能拼成 shell 命令。举例：

```sh
python3 scripts/companion.py run --project PROJECT_ID --script media_probe --label '检查选定视频' -- /absolute/input.mp4 --frames /absolute/review --times 1.2,2.4
python3 scripts/companion.py run --project PROJECT_ID --script subtitle_layer --label '字幕层导出' --artifact /absolute/output.mp4 -- /absolute/clean.mp4 /absolute/reviewed-cues.json assets/presets/dsx-r3-yahei.json /absolute/output.mp4
```

脚本白名单、版本指纹、执行 ID、心跳、退出码、产物指纹统一记录。缺 Pillow 时入口用现有 uv 加载依赖。服务重启不影响独立的脚本进程；心跳过期只显示状态未知，不假装结束或自动重跑。必要时沿原脚本和原外部任务身份核对。

`completed` 只表示脚本零退出；产物默认待审。图像语义、动作、剪接、口型、声音须依原规范检查。不得用测试日志冒充本集制作进度。独立任务可以并发执行，但监看器本身不调度生产队列、不替代 `pipeline.py claim`；重型本地渲染按机器负载控并发。

## 浏览器请求

扩展安装并配对后同步项目名、选择项和可见视频时间。获取不到时间时留空，不猜测。切项目清除未提交的表单，所有请求保留 projectId、节点和稳定请求 ID。界面打开在另一个项目时，不把旧请求用到当前项目。

```sh
python3 scripts/companion.py pending --project PROJECT_ID
```

- `sync_snapshot`：助手通过当前授权的 LibTV 插件回读画布，保存真实统计、采集时间和 revision。此请求与面板已有的只读增量同步分别登记，不把排队请求视为已完成。
- `organize_selection`：按请求中的 `batch` 名称创建或绑定原生组，严格限定原始所选节点。无批次名、空选择、切项目或选择变化则拒绝。
- `organize_batches`：按批次与用途分类。人物 R2 与视频 R2 不混组；同批次参考、结果、返修相邻；正式采用资产保留独立区域；不确定归属先保留原位。
- `native_group_layout`：打开已选原生组的排列菜单，选择宫格、水平或垂直；没有原生可用控件则禁用。
- `native_arrange`：浏览器已请求网页原生整理；DOM 观察只作中间证据，不能替代平台保存回读。请求记录并不保证按钮已成功响应。

保存回读证据后用 `receipt --id REQUEST_ID /absolute/receipt.json` 记录已核对。回执包含 `project`、`tool`、`readback` 和 `evidence`。浏览器 HTTP 接口不能写“已验收”，也不能启动任意命令。助手未运行时，请求留在等待状态，不能宣传为自动唤醒 Codex。

## 批次整理固定流程

1. 插件 `read_project(view="nodes")` 保存 `before.json`，记录准确项目、节点、内容、连线及 revision。
2. 保存 `plan.json`：`projectId` 与 `groups[]`，每组包含稳定 `key`、可读 `name`、精确 `nodes[]`、可选 `existing` 组 ID。不要从 R2 等单个短词推断全部归属，不以分组行为改变人工采用状态。
3. 统一入口运行 `canvas_batches.py preflight --before before.json --plan plan.json --output preflight.json`。同一节点只能属于一个组。新建组用 LibTV `manage_group(create)`，已有组用 `bind`；稳定幂等键与回执落盘，禁止 `run`。
4. 按网页原生侧栏定位组；组名可双击编辑。组内先“宫格排列”，然后全局“整理画布”，检查预览再“保留”。正在等待“保留/还原”时不能重复点击整理。
5. 回读保存 `after.json`。回执清单为 `[{"key":"...","groupId":"g-..."}]`。执行 `canvas_batches.py verify --before before.json --plan plan.json --after after.json --receipts receipts.json --output verification.json`，非零退出必须处理差异，不能标记完成。
6. 核对原节点仍在、素材与提示词内容不变、连线完整、分组名称与成员正确；再通过 `companion.py project --id PROJECT_ID --name NAME --snapshot verification.json` 同步目录。浏览器批次目录用于定位原生组，高亮为短暂视觉提示，不修改素材。

`canvas_batches.py` 只校验，不调用生成或改变采用状态。CLI 成功与实际可见排版分别检查；截图、平台回读与分组回执一并留存。

## 字幕预设

`assets/presets/dsx-r3-yahei.json` 固化《大师兄》R3 用户认可的字幕外观：微软雅黑、480p 基准 27px、距底部 30px、白色单行、居中、去标点。使用既有 `subtitle_layer.py`，保留无字母版与 SRT，校验音频包不变。它是可选项目预设，不强制覆盖其他题材字幕。实际 font_file 必须存在；不偷偷替换字体。

当前预设验证基准为 854×480；提高分辨率或换成双行字幕时重新检查图层安全区，不能仅改字号就称效果一致。

## 运行边界

- 本地页面无生成、重试、删除、终止按钮；创作操作从画布与既定工具进行。
- 扩展调用原生整理时如按钮禁用，保持禁用并提示现状，不绕过网页限制。
- 外部生成的 Task/Resource 回执继续保存在制作队列；本地运行 ID 不是平台任务 ID。
- 问题报告保留为待复核；已发现、已修复、已复看、用户采用不能合并。
- 配对码只用于本机扩展。禁止上传或把配对码、平台凭据写进日志与仓库。
