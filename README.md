# Scenario Facial-Expression Capture

English | [中文](README.zh-CN.md) | [日本語](README.ja.md)

## Use the project

### Browser video annotation

Run `./run_annotation.ps1` from the project folder; keep the terminal open. The local browser tool needs no API or new dependencies. Specify FFmpeg with `./run_annotation.ps1 -FFmpeg "C:\ffmpeg\bin\ffmpeg.exe"` if needed. Browse to a folder and click Open: only direct video files are listed, in natural filename order.

Keep one interval and one label per video: neutral, polite smile, or bitter smile. Shortcuts: Space play/pause; arrows ±0.1 seconds; I/O start/end; 1/2/3 labels; Enter save/next; P previous; L loop; D discard. Shortcuts are inactive inside text/number inputs. Output is `original-filename_trimmed.mp4` beside the untouched original. `annotations.csv` contains only successful output filenames, output directories, and labels. `.video_annotations.json` enables resume. Discard never deletes originals, but removes that item's previously generated clip and CSV entry. Generated `_trimmed.mp4` files are excluded from scanning.

First playback builds a temporary local browser-compatible preview; exports use the original. Wait for background exports and resolve errors before exiting with Ctrl+C. Do not run multiple annotators against the same folder or open the CSV in Excel during annotation. Media stays local.

The full 55-scene question sets remain available as `question_set/combined_all_smile_scenarios_55_ja.json`, shown as **苦笑い・表情会話シナリオ統合版（55場面）**, and `question_set/combined_all_smile_scenarios_55_zh.json`, shown as **苦笑与社交表情情景题库·中文整合版（55个场景）**.
The default question-set list includes both the full sets and the round sets. The Japanese round files are `question_set/smile_scenarios_round_01_ja.json` through `question_set/smile_scenarios_round_05_ja.json`; the Chinese round files are `question_set/smile_scenarios_round_01_zh.json` through `question_set/smile_scenarios_round_05_zh.json`. For each language, rounds 1–5 contain 11 scenes each, with no overlap between rounds, and together cover all 55 scenes.
Select only one language for each experiment: use Japanese TTS (`ja-JP`) for the Japanese set or Chinese TTS (`cmn-CN`) for the Chinese set. Both language versions share the same images.
Show and play the scene description first; press Space to start recording and display the image with the first spoken line, then use Space to advance through the lines in order or finish.

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

Recording now requires PyAV (`av>=16.1,<17`). Update an existing environment with `.\.venv313\Scripts\python.exe -m pip install -r requirements.txt`, then close the application and restart it with `.\run.ps1`.

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
2. Select the save folder, random seed, and video-splitting mode. Set **Rest between scenes (seconds)** as needed: the default is 10 seconds, with a range of 4–300 seconds.
3. Select a Japanese or Chinese question set from `question_set`; all sets are unchecked by default. For round-based collection, check only the one set for the chosen language and round 1–5; do not also select a full set or another round. The target clip count updates automatically when the selection or practice setting changes. With practice disabled, it is the sum of `segment_count` across the selected sets; with practice enabled, it is that sum minus 1, with a minimum of 0. With no set selected, it is 0. Neutral clips do not count toward this target.
4. Select a camera, start the preview, and confirm framing and resolution.
5. Optionally enable Google Cloud TTS as described below.
6. Review the **Run a practice scene first** setting, then start the experiment. This option is checked by default, and the application remembers the setting. When enabled, the entire first sampled scene is practice and is excluded from the formal `manifest.csv`. When unchecked, the experiment goes directly to the formal scene overview, and every scene in the selected set is available for formal collection.
7. Read the scene overview. The camera is open, but recording has not started.
8. Press Space to start the scene. The application records one continuous raw video for the complete scene and keeps the same image throughout that scene.
9. Follow the spoken lines in order. Each line is displayed and played with the scene image; respond naturally, return to a neutral expression, and press Space to advance or finish.
10. After the last item, the scene recording stops. FFmpeg extracts formal clips from the recorded timestamps.
11. If another formal scene remains, a neutral rest period is recorded automatically. Face the camera, look at the cross, and relax your facial muscles. No image, spoken line, or audio is presented, and Space cannot skip the rest. When it ends, the application automatically opens the next formal scene overview. Rest occurs only between formal scenes—not before the first, after the last, or between practice and formal collection. Collecting all 11 formal scenes in a round therefore produces 10 neutral recordings.

The selected save folder contains raw scene and rest videos, split clips, `manifest.csv`, `stimuli.json`, `session.json`, and timestamp logs, plus a practice video when practice is enabled. The videos contain no microphone audio. Files are created with the current user's permissions and can be deleted without administrator access.

New recordings use PyAV to write variable-frame-rate MP4 with each real frame's monotonic timestamp and a final packet duration extending to the actual recording stop. This corrects nominal-FPS time compression: for example, 50 frames captured over 10 seconds could previously become about 1.667 seconds at 30 FPS. No duplicate or black frames are inserted. OpenCV still enumerates and captures cameras; source FPS may remain low, and this does not fix a black camera feed. Recording fails if it is truly empty or no real frame arrives for more than 2 seconds. The 3-second neutral trim is unchanged. Only new recordings are corrected; old videos' original timing cannot be reliably recovered without their missing per-frame timestamp history.

For cameras whose name contains `C920` (case-insensitive), capture first requests MJPEG (`MJPG`) input at 1920×1080 and 30 FPS. Other cameras keep their existing capture configuration. If the driver rejects the request or the first MJPG frame cannot be read, capture falls back to the default format and logs the reason. These are requested settings, not a guarantee of 30 FPS. MJPEG input does not change the MP4/H.264 VFR output or require additional dependencies.

Preview and experiment startup write capture diagnostics to the project-level `logs/camera_capture.jsonl` (ignored by Git). Experiment diagnostics are also stored in `session.json` under `camera_capture` and in `logs/events.jsonl` as `camera_capture_config` events. They include the requested settings, driver feedback, actual first-frame size, and `measured_capture_fps`: the rate actually received through camera reads and the processing pipeline, not the driver's `reported_fps`. The participant-facing experiment screen remains unchanged, with no additional camera status. Before checking the updated preview, close the running application and restart it with `.\run.ps1`. This change has not been tested with a physical camera on the development machine; on the collection laptop, select the C920 and confirm the preview and diagnostics before formal collection.

Neutral clips exclude the first 3,000 ms of each rest recording; a normal 10-second recording yields about 7 seconds of neutral video. The full rest recording remains in `raw`. Neutral videos are saved alongside the other split videos in `clips`, and their `manifest.csv` rows use `stimulus_format='neutral'` and `purpose='neutral'`. The events log records the rest start, end, and trim. Neutral videos follow the existing silent or end-of-experiment splitting setting. If the actual rest recording is 3 seconds or shorter, there is no usable neutral video after trimming: the application reports an error and stops instead of creating an empty neutral clip.

To collect all 55 scenes formally across five rounds, leave **Run a practice scene first** unchecked in every round. Enabling practice uses one of that round's 11 scenes for practice, leaving at most 10 scenes for formal collection. The target counts clips / spoken lines, not scenes, and is calculated automatically without manual input. Subtracting 1 when practice is enabled is only the displayed target calculation: practice still excludes an entire scene of 1–4 spoken lines, so the actual formal clip count may be lower than the displayed target. With practice disabled, the automatic target covers all 11 scenes in the selected round.

Splitting the sets only shortens each experiment; it does not mean the camera black-screen issue has been fixed. Between rounds, stop and restart the camera and confirm a working preview before starting the next round.

Do not select both language versions in the same experiment. A single language and voice configuration applies to the entire run.

## Configure the Google Cloud TTS API

The application uses the Google Cloud Text-to-Speech Python client, generates LINEAR16 WAV files before the experiment, and plays only the local cache during collection.

1. Create or select a Google Cloud project, attach billing, and enable the Cloud Text-to-Speech API. See Google's [Cloud Text-to-Speech setup guide](https://docs.cloud.google.com/text-to-speech/docs/get-started).
2. Create a service account that is allowed to call the enabled API. See Google's [service-account creation guide](https://docs.cloud.google.com/iam/docs/service-accounts-create).
3. Create and download a JSON key for that service account. The current application authenticates with this JSON file rather than Application Default Credentials. See Google's [service-account key guide](https://docs.cloud.google.com/iam/docs/keys-create-delete).
4. Store the JSON outside the repository and participant-data folders. Never commit it to Git or share it with collected data.
5. In the application, enable **Google Cloud TTS** and select the JSON file with **Service-account JSON**.
6. Set **Text language code** to match the selected question set:
   - Chinese: `cmn-CN`
   - Japanese: `ja-JP`
7. Leave **Voice name** empty to let Google choose a default voice, or enter a compatible Google Cloud voice name. Set the speaking rate as required.
8. Start the experiment. Missing audio is generated before the session begins and saved under `D:\datacapture\tts_cache`. The scene description and each spoken line receive their own cached WAV file.

The credentials file itself is not copied into the cache or experiment output. If generation fails, the application lets the operator retry, continue without speech, or cancel the experiment.
