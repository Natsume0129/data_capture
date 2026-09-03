# 情景表情数据采集软件

Windows 桌面应用，用多个 CSV 问题集驱动情景式表情视频采集。软件在场景综述阶段仅开启摄像头，不录像；进入第一个片段时开始场景级录像，实验人员手动切换片段并记录时间戳，随后通过 FFmpeg 将原始视频切分为片段级 MP4。

## 已实现功能

- PySide6 图形界面。
- 中文、English、日本語三种界面语言；不翻译题库正文。
- 自动枚举和选择摄像头。
- 优先选择 1920×1080、最高 30 FPS 的摄像头格式；实际格式不符时要求实验人员确认。
- 正式实验期间隐藏自拍预览。
- 同时选择多个问题集。
- 场景不重复，场景内片段严格按 CSV 顺序完整执行。
- 各问题集按已采集片段数轮流均衡抽取，并优先降低目的类别数量差异。
- 固定随机种子，可复现实验抽样顺序。
- 手动点击按钮或按空格切换片段。
- 每个场景保存一份无麦克风音轨的原始 MP4。
- 增量写入时间戳事件日志和 manifest.csv。
- 支持实验中后台切分，或实验结束后统一切分。
- 原始视频永久保留；切分失败会写入 manifest，不会删除原始数据。
- Google Cloud Text-to-Speech 实验前预生成 LINEAR16 WAV，实验中只播放本地缓存。
- TTS 失败时由实验人员明确选择重试、无语音继续或取消，不静默降级。
- 每个场景支持一张可选配图。

## 环境

- Windows 10/11
- Python 3.13
- FFmpeg（开发机当前验证版本：8.0.1）
- PySide6
- google-cloud-texttospeech

安装：

    cd D:\data\_capture
    py -3.13 -m venv .venv313
    .\.venv313\Scripts\python.exe -m pip install -r requirements.txt

启动：

    .\run.ps1

## 问题集格式

最低要求为 UTF-8 CSV，包含“情景、问题、目的”三列。同一个“情景”连续出现的多行会被归为一个场景；“问题”行顺序就是实验中的片段顺序。程序不会修改三列文字。

可以增加“配图”列。“配图”可以是绝对路径，也可以是相对于 CSV 文件的路径。同一场景最多使用一个不同的非空图片路径；可以只在该场景第一行填写。

当前题库位于：

    D:\data\_capture\question_set\笑容情景模拟实验_刺激问题库_v2_真实细化.csv

## 实验流程

1. 输入匿名被试编号。
2. 选择保存路径、目标片段数、随机种子和切分方式。
3. 勾选一个或多个问题集。
4. 选择摄像头并检查预览。
5. 如需语音，选择 Google Cloud 服务账号 JSON，设置文本语言代码、语音名称和语速。
6. 点击“开始实验”；软件先生成本次计划需要且缓存中不存在的 WAV。
7. 查看场景综述。此时摄像头已开启，但没有录像。
8. 点击“开始本场景”或按空格，开始录像并显示片段1。
9. 每次点击或按空格进入下一片段，同时记录前一片段结束时间和下一片段开始时间。
10. 最后一个片段完成后停止场景录像。已经开始的场景永远完整执行。
11. 达到目标片段数后不再抽取新场景；最终数量可以超过目标。
12. 所有切分结束后显示完成提示并返回设置页。

## Google Cloud TTS

先在 Google Cloud 项目中启用 Text-to-Speech API，并准备具有调用权限的服务账号 JSON。凭据文件不会复制到项目、会话或缓存目录；应用只通过 Windows 本地设置保存其路径。

当前中文题库默认语言代码为 cmn-CN。语音名称可留空，让 API 使用该语言的默认语音。

WAV 缓存位于：

    D:\data\_capture\tts_cache

缓存键包含原文、语言代码、语音名称、语速和编码，因此配置改变后不会错误复用旧语音。

## 输出

    数据保存路径/
    └─ 被试编号/
       └─ YYYYMMDD_HHMMSS_随机后缀/
          ├─ raw/
          ├─ clips/
          ├─ logs/
          │  └─ events.jsonl
          ├─ manifest.csv
          └─ session.json

manifest.csv 每行对应一个片段，记录被试和会话编号、问题集/场景/片段 ID、未修改的三列文字、原始视频和切分视频路径、起止时间与时长、切分状态和错误信息。

events.jsonl 在每次场景展示、录像开始、片段切换和录像停止时立即写盘，用于故障审计和恢复。

## 测试

    .\.venv313\Scripts\python.exe -m pytest -q

自动测试覆盖题库解析、当前正式题库规模、配图约束、多题库均衡抽样、完整场景超额停止、无重复场景、会话日志、manifest、TTS 缓存键、三语键一致性，以及真实 FFmpeg 视频切分。

摄像头驱动、Google Cloud 凭据和扬声器属于硬件或外部服务集成，必须在正式采集电脑上完成一次人工试录。

## 打包

当前应使用 run.ps1 从源码运行。Python 3.13 + PySide6 6.11.2 的 PyInstaller 发行包在本机启动时出现 QtCore DLL 加载错误，因此现有 EXE 不作为可用交付物。根据当前决定，先绕过该打包问题；摄像头、题库和采集流程的源码开发与测试不受影响。

后续重新处理 Qt 运行库打包并完成硬件试录后，再运行：

    .\build_exe.ps1

输出目录：

    D:\data\_capture\dist\ScenarioCapture

脚本使用 PyInstaller onedir 模式，并将 ffmpeg.exe 和 question_set 复制到可执行文件旁。分发时必须复制整个 ScenarioCapture 文件夹，不能只复制单个 EXE。

打包后的依赖自检：

    $env:SCENARIO_CAPTURE_SELF_CHECK = "D:\data\_capture\self_check.json"
    .\dist\ScenarioCapture\ScenarioCapture.exe
    Remove-Item Env:SCENARIO_CAPTURE_SELF_CHECK
