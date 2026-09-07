# 情景表情数据采集软件

[English](README.md) | 中文 | [日本語](README.ja.md)

## 使用项目

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
2. 选择保存路径、目标片段数、随机种子和视频切分方式。
3. 从 `question_set` 中选择一个或多个 CSV 问题集。
4. 选择摄像头，启动预览并确认画面和分辨率。
5. 如需语音，按照下方说明启用 Google Cloud TTS。
6. 开始实验。随机抽取的第一个场景是练习场景，不计入正式 `manifest.csv` 和目标片段数。
7. 阅读场景综述。此时摄像头已打开，但尚未开始录像。
8. 按空格开始场景。应用为整个场景连续保存一段原始视频，同一场景始终使用同一张配图。
9. 按顺序完成场景内的所有项目：
   - 旧题库（`情景,问题,目的,配图`）：阅读或聆听片段，自然作出反应；表情恢复自然后按空格继续。
   - 说明发言题库（`情景,片段序号,说明,发言,目的,配图`）：先显示并播放说明。按第一次空格后单独显示并播放发言，同时记录片段开始时间；表情反应结束后再次按空格，记录结束时间并进入下一条说明。
10. 最后一项完成后停止场景录像。FFmpeg 根据时间戳生成正式片段。说明发言题库的最终片段只包含“发言开始至第二次按下空格”的画面。

所选保存路径中会生成练习视频、场景原始视频、切分片段、`manifest.csv`、`stimuli.json`、`session.json` 和时间戳日志。视频不包含麦克风音轨。文件使用当前用户权限创建，无需管理员权限即可删除。

启用 TTS 时，不要在同一次实验中混选正文语言不同的题库，因为整场实验只使用一套语言和语音配置。

## 配置 Google Cloud TTS API

应用通过 Google Cloud Text-to-Speech Python 客户端生成 LINEAR16 WAV。语音在实验开始前生成，采集过程中只播放本地缓存。

1. 创建或选择 Google Cloud 项目，关联结算账号，并启用 Cloud Text-to-Speech API。参见 Google 的[入门文档](https://docs.cloud.google.com/text-to-speech/docs/get-started)。
2. 创建允许调用该 API 的服务账号。参见 Google 的[创建服务账号文档](https://docs.cloud.google.com/iam/docs/service-accounts-create)。
3. 为服务账号创建并下载 JSON 密钥。当前应用通过该 JSON 文件认证，不使用 Application Default Credentials。参见 Google 的[服务账号密钥文档](https://docs.cloud.google.com/iam/docs/keys-create-delete)。
4. 把 JSON 保存在项目目录和被试数据目录之外。不要将其提交到 Git，也不要随采集数据共享。
5. 在应用中启用 **Google Cloud TTS**，通过 **服务账号 JSON** 选择密钥文件。
6. 根据所选题库设置 **文本语言代码**：
   - 中文：`cmn-CN`
   - 英语：`en-US`
   - 日语：`ja-JP`
7. **语音名称**可以留空，让 Google 选择默认语音；也可以填写与语言代码兼容的 Google Cloud 语音名称。根据需要设置语速。
8. 开始实验。缺少的语音会在会话开始前生成并写入 `D:\datacapture\tts_cache`。新题库中的说明和发言会分别生成独立的 WAV 文件。

应用不会把凭据文件复制到缓存或实验输出目录。语音生成失败时，实验人员可以选择重试、本次不使用语音，或取消实验。
