# Scenario Facial-Expression Capture

English | [中文](README.zh-CN.md) | [日本語](README.ja.md)

## Use the project

### Requirements

- Windows 10 or 11
- Python 3.13
- FFmpeg, either available on `PATH` or selected in the application
- A DirectShow-compatible camera

### Install

Open PowerShell and run:

```powershell
Set-Location "D:\datacapture"
py -3.13 -m venv .venv313
.\.venv313\Scripts\python.exe -m pip install --upgrade pip
.\.venv313\Scripts\python.exe -m pip install -r requirements.txt
```

If the virtual environment already exists, only run the final two commands to update it.

### Start

```powershell
Set-Location "D:\datacapture"
.\run.ps1
```

If PowerShell blocks local scripts:

```powershell
powershell.exe -ExecutionPolicy Bypass -File "D:\datacapture\run.ps1"
```

Run the application from source for now. The packaged executable is not yet the supported launch method.

### Run an experiment

1. Enter an anonymous participant ID.
2. Select the save folder, target number of clips, random seed, and video-splitting mode.
3. Select one or more CSV question sets from `question_set`.
4. Select a camera, start the preview, and confirm framing and resolution.
5. Optionally enable Google Cloud TTS as described below.
6. Start the experiment. The first sampled scene is practice and is excluded from the formal manifest and target count.
7. Read the scene overview. The camera is open, but recording has not started.
8. Press Space to start the scene. The application records one continuous raw video for the complete scene and keeps the same image throughout that scene.
9. Complete every item in order:
   - Legacy sets (`情景,问题,目的,配图`): read or listen to the segment, respond naturally, return to a neutral expression, and press Space to continue.
   - Instruction-and-utterance sets (`情景,片段序号,说明,发言,目的,配图`): the instruction is shown and spoken first. Press Space to show and play the utterance and record the clip start timestamp. Press Space again when the facial response ends; this records the end timestamp and opens the next instruction.
10. After the last item, the scene recording stops. FFmpeg extracts formal clips from the recorded timestamps. For instruction-and-utterance sets, the final clip contains only the interval from utterance start to the second Space press.

The selected save folder contains the practice video, raw scene videos, split clips, `manifest.csv`, `stimuli.json`, `session.json`, and timestamp logs. The videos contain no microphone audio. Files are created with the current user's permissions and can be deleted without administrator access.

Do not select question sets in different text languages in the same TTS-enabled experiment. A single language and voice configuration applies to the entire run.

## Configure the Google Cloud TTS API

The application uses the Google Cloud Text-to-Speech Python client, generates LINEAR16 WAV files before the experiment, and plays only the local cache during collection.

1. Create or select a Google Cloud project, attach billing, and enable the Cloud Text-to-Speech API. See Google's [Cloud Text-to-Speech setup guide](https://docs.cloud.google.com/text-to-speech/docs/get-started).
2. Create a service account that is allowed to call the enabled API. See Google's [service-account creation guide](https://docs.cloud.google.com/iam/docs/service-accounts-create).
3. Create and download a JSON key for that service account. The current application authenticates with this JSON file rather than Application Default Credentials. See Google's [service-account key guide](https://docs.cloud.google.com/iam/docs/keys-create-delete).
4. Store the JSON outside the repository and participant-data folders. Never commit it to Git or share it with collected data.
5. In the application, enable **Google Cloud TTS** and select the JSON file with **Service-account JSON**.
6. Set **Text language code** to match the selected question set:
   - Chinese: `cmn-CN`
   - English: `en-US`
   - Japanese: `ja-JP`
7. Leave **Voice name** empty to let Google choose a default voice, or enter a compatible Google Cloud voice name. Set the speaking rate as required.
8. Start the experiment. Missing audio is generated before the session begins and saved under `D:\datacapture\tts_cache`. In the new question-set format, both the instruction and the utterance receive their own cached WAV file.

The credentials file itself is not copied into the cache or experiment output. If generation fails, the application lets the operator retry, continue without speech, or cancel the experiment.
