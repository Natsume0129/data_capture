# 情景表情数据采集软件

[English](README.md) | 中文 | [日本語](README.ja.md)

## 使用项目

### 浏览器视频标注工具

在项目目录执行 `./run_annotation.ps1`，自动打开本地浏览器界面。保持 PowerShell 窗口打开；不需要 API 或新增依赖。若找不到 FFmpeg，可用 `./run_annotation.ps1 -FFmpeg "C:\ffmpeg\bin\ffmpeg.exe"` 指定位置。

选择文件夹后点击“打开”，仅扫描当前目录，按文件名自然排序。每个视频选一个连续区间和一个类别：`neutral`、`polite smile`、`bitter smile`。空格播放/暂停，左右键移动 0.1 秒，I/O 设置起止点，1/2/3 选择类别，Enter 保存并下一条，P 返回，L 循环，D 弃用。输入框获得焦点时快捷键暂停，请点击画面外空白处恢复。

裁剪视频以 `原文件名_trimmed.mp4` 直接写入所选目录，不覆盖原视频；`annotations.csv` 只包含导出成功的裁剪文件名、所在目录和类别。`.video_annotations.json` 保存进度，重新打开同一目录即可续标。D 不删除原视频，但会移除该条由本工具生成的旧裁剪视频及 CSV 条目；可返回重新标注。扫描跳过 `_trimmed.mp4` 文件。

首次选择视频会生成本地临时兼容预览，导出使用原视频。请等待导出中数量归零、处理所有失败项后，再在终端按 Ctrl+C 退出。不要同时启动多个标注实例操作同一目录，也不要在标注过程中用 Excel 打开输出 CSV。所有媒体处理都在本机进行。

55场景完整版题库保留为 `question_set/combined_all_smile_scenarios_55_ja.json`，界面名称为 **苦笑い・表情会話シナリオ統合版（55場面）**；以及 `question_set/combined_all_smile_scenarios_55_zh.json`，界面名称为 **苦笑与社交表情情景题库·中文整合版（55个场景）**。
默认题库列表同时显示完整版和分轮版。日语分轮文件为 `question_set/smile_scenarios_round_01_ja.json` 至 `question_set/smile_scenarios_round_05_ja.json`，中文分轮文件为 `question_set/smile_scenarios_round_01_zh.json` 至 `question_set/smile_scenarios_round_05_zh.json`。每种语言的第1～5轮各有11个场景，轮间无重叠，5轮合计覆盖55个场景。
每次实验只选择一种语言：日语题库使用日语TTS（`ja-JP`），中文题库使用中文TTS（`cmn-CN`）。两个语言版本共用图片。
先显示并播报场景说明，按空格开始录像并显示图片与第一句台词，再按空格依次播放后续台词或结束。

### 环境要求

- Windows 10 或 11
- Python 3.13
- FFmpeg：可以加入 `PATH`，也可以在应用中选择程序路径
- 支持 DirectShow 的摄像头

### 安装

打开 PowerShell，执行：

```powershell
Set-Location "D:\datacapture"
py -3.13 -m venv .venv313
.\.venv313\Scripts\python.exe -m pip install --upgrade pip
.\.venv313\Scripts\python.exe -m pip install -r requirements.txt
```

如果虚拟环境已经存在，只需执行最后两条命令进行更新。

录像现在依赖 PyAV（`av>=16.1,<17`）。已有环境请执行 `.\.venv313\Scripts\python.exe -m pip install -r requirements.txt` 更新依赖，然后关闭应用并通过 `.\run.ps1` 重新启动。

### 启动

```powershell
Set-Location "D:\datacapture"
.\run.ps1
```

如果 PowerShell 禁止执行本地脚本：

```powershell
powershell.exe -ExecutionPolicy Bypass -File "D:\datacapture\run.ps1"
```

当前请从源码启动应用，打包后的可执行程序暂不作为正式启动方式。

### 进行实验

1. 输入匿名被试编号。
2. 选择保存路径、随机种子和视频切分方式。按需设置 **场景间休息（秒）**：默认10秒，可设置为4～300秒。
3. 从 `question_set` 中选择日语或中文题库；默认所有题库均未勾选。分轮采集时，每轮只勾选对应语言、对应第1～5轮中的一份，不要同时勾选完整版或其他轮次。目标片段数随题库选择或练习开关自动更新：取消练习时，等于所有选中题库的 `segment_count` 总和；启用练习时，等于该总和减1，最小为0。未选题库时为0，neutral 片段不计入该目标。
4. 选择摄像头，启动预览并确认画面和分辨率。
5. 如需语音，按照下方说明启用 Google Cloud TTS。
6. 确认 **开始前进行练习** 后开始实验。此项默认勾选，应用会记住开关设置。勾选时，随机抽取的第一个完整场景用于练习，不计入正式 `manifest.csv`；取消勾选时，直接进入正式场景综述，所选题库中的所有场景都可用于正式采集。
7. 阅读场景综述。此时摄像头已打开，但尚未开始录像。
8. 按空格开始场景。应用为整个场景连续保存一段原始视频，同一场景始终使用同一张配图。
9. 按顺序完成场景内的所有台词。每句台词与场景图片一起显示并播放；自然作出反应，表情恢复自然后按空格继续或结束。
10. 最后一项完成后停止场景录像。FFmpeg 根据时间戳生成正式片段。
11. 如果还有下一个正式场景，自动录像采集一段 neutral 休息。正对摄像头看十字，放松面部；休息期间没有图片、台词或语音，不能按空格跳过。休息结束后自动进入下一个正式场景综述。仅两个正式场景之间有休息，首个正式场景前、最后一个正式场景后，以及练习到正式的转换都不加休息。因此，实际采集一轮全部11个正式场景时，会产生10段 neutral 录像。

所选保存路径中会生成场景和休息原始视频、切分片段、`manifest.csv`、`stimuli.json`、`session.json` 和时间戳日志；启用练习时还会生成练习视频。视频不包含麦克风音轨。文件使用当前用户权限创建，无需管理员权限即可删除。

新录像通过 PyAV 写入可变帧率（VFR）MP4，使用每个真实帧的单调时钟时间戳，并将最后一个数据包的持续时间延伸到实际停止录像的时刻。这修正了按名义帧率写入导致的时长压缩：例如，实际10秒采集到的50帧，旧方式按30 FPS写入后可能只有约1.667秒。不会补入重复帧或黑帧。摄像头枚举和采集仍使用 OpenCV；实际源帧率可能仍偏低，摄像头黑幕也不属于此次修复范围。录像确实为空，或超过2秒没有收到真实帧时，应用会报错停止。Neutral 的前3秒裁剪规则不变。修正仅适用于新录像；旧录像缺少逐帧时间戳历史，无法可靠恢复原始时间基准。

仅摄像头名称包含 `C920`（不区分大小写）时，采集会优先请求 MJPEG（`MJPG`）输入、1920×1080、30 FPS；其他摄像头保持现有采集设置。驱动拒绝请求，或无法读取 MJPG 的首帧时，会回退到默认格式并记录原因。这些是请求值，不保证实际达到30 FPS。MJPEG 输入不改变 MP4/H.264 VFR 输出，也不新增依赖。

预览和实验启动时会将采集诊断写入项目级 `logs/camera_capture.jsonl`（Git 忽略）。实验诊断还会保存到 `session.json` 的 `camera_capture`，以及 `logs/events.jsonl` 的 `camera_capture_config` 事件，包含请求设置、驱动反馈、实际首帧尺寸和 `measured_capture_fps`。后者是摄像头读取及处理链路的实收帧率，不等于驱动的 `reported_fps`。被试实验界面保持不变，不新增摄像头状态。检查更新后的预览前，请先关闭正在运行的程序，再通过 `.\run.ps1` 重新启动。开发机未用实体摄像头实测此改动；请在采集笔记本上选择 C920，先确认预览与诊断再开始正式采集。

Neutral 片段会剪掉休息录像的前3000毫秒；正常10秒录像裁剪后约有7秒 neutral 视频。`raw` 仍保留完整休息录像。Neutral 视频与其他切片一起保存在 `clips` 中，其 `manifest.csv` 行通过 `stimulus_format='neutral'` 和 `purpose='neutral'` 识别；events 日志记录休息起止与 trim 信息。Neutral 视频沿用现有的静默切分或实验结束后切分设置。如果摄像头实际录到的休息视频不超过3秒，裁剪后没有有效 neutral 视频，应用会报错并停止，不会生成空 neutral 片段。

要在5轮中正式采集全部55个场景，建议每轮都取消 **开始前进行练习**；某轮启用练习时，该轮11个场景中有1个用于练习，正式采集最多覆盖10个场景。目标片段数按片段／台词计数，不是场景数，自动计算，无需手工设置。启用练习时减1仅是界面目标的计算规则：练习仍排除含1～4句台词的整个场景，因此实际正式片段数可能小于显示的目标。取消练习后，自动目标可覆盖所选轮次的全部11个场景。

分轮仅用于缩短单次实验，不代表摄像头黑幕故障已修复。建议轮与轮之间停止并重新启动摄像头，确认预览画面有效后再开始下一轮。

不要在同一次实验中同时选择两个语言版本，因为整场实验只使用一套语言和语音配置。

## 配置 Google Cloud TTS API

应用通过 Google Cloud Text-to-Speech Python 客户端生成 LINEAR16 WAV。语音在实验开始前生成，采集过程中只播放本地缓存。

1. 创建或选择 Google Cloud 项目，关联结算账号，并启用 Cloud Text-to-Speech API。参见 Google 的[入门文档](https://docs.cloud.google.com/text-to-speech/docs/get-started)。
2. 创建允许调用该 API 的服务账号。参见 Google 的[创建服务账号文档](https://docs.cloud.google.com/iam/docs/service-accounts-create)。
3. 为服务账号创建并下载 JSON 密钥。当前应用通过该 JSON 文件认证，不使用 Application Default Credentials。参见 Google 的[服务账号密钥文档](https://docs.cloud.google.com/iam/docs/keys-create-delete)。
4. 把 JSON 保存在项目目录和被试数据目录之外。不要将其提交到 Git，也不要随采集数据共享。
5. 在应用中启用 **Google Cloud TTS**，通过 **服务账号 JSON** 选择密钥文件。
6. 根据所选题库设置 **文本语言代码**：
   - 中文：`cmn-CN`
   - 日语：`ja-JP`
7. **语音名称**可以留空，让 Google 选择默认语音；也可以填写与语言代码兼容的 Google Cloud 语音名称。根据需要设置语速。
8. 开始实验。缺少的语音会在会话开始前生成并写入 `D:\datacapture\tts_cache`。场景说明和每句台词会分别生成独立的 WAV 文件。

应用不会把凭据文件复制到缓存或实验输出目录。语音生成失败时，实验人员可以选择重试、本次不使用语音，或取消实验。
