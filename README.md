# 소피컷 (SophiCut)

**말한 대로 쓰고, 쓴 대로 자릅니다.** 영상을 넣으면 AI가 원고를 받아 적고, 원고에서 지운 말은 영상에서도 잘려 나가는 영상 편집기입니다. 받아쓰기(faster-whisper)를 포함한 모든 처리가 내 컴퓨터 안에서 이루어지므로 영상이 외부로 전송되지 않습니다.

## 설치 (일반 사용자)

1. [Releases](https://github.com/philosophyAIEDU/261115cut/releases/latest)에서 **`SophiCut-Setup.exe`** 를 내려받아 실행합니다.
2. 「Windows의 PC 보호」 창이 뜨면 **추가 정보 → 실행**을 누릅니다.
3. 설치가 끝나면 바탕화면의 **소피컷**을 실행하세요. 브라우저에 편집 화면이 열립니다.

- Python, ffmpeg 등을 따로 설치할 필요가 없습니다(설치 파일에 들어 있음).
- NVIDIA 그래픽카드가 있으면 첫 화면의 **GPU 가속 받기**로 받아쓰기를 빠르게 할 수 있습니다.
- 새 버전이 나오면 첫 화면에 알림이 뜹니다. 새 설치 파일을 실행하면 프로젝트를 유지한 채 업데이트됩니다.
- 프로젝트·자막 스타일은 `%LOCALAPPDATA%\SophiCut\data` 에 저장됩니다.

## 화면 구성

왼쪽 **도구 레일**(다듬기 · 자막 · 이미지 · 속도 · 숏폼) → **도구 서랍** → 가운데 **원고** → 오른쪽 **영상**. 도구 아이콘을 한 번 더 누르면 서랍이 접힙니다.

| 도구 | 하는 일 |
|---|---|
| 다듬기 | 단어를 끌어 고르고 `Delete`로 자르기/되살리기, 문단 나누기·붙이기, **빈틈 정리**(말 없는 구간), **추임새 지우기**(음·어·으), 원고 찾기 |
| 자막 | 굵기·크기·색·자간, 테두리·배경·형광펜·그림자, 한 줄 글자수·높이, 준비된 스타일 8종과 내 스타일 저장 |
| 이미지 | 체크한 문단(또는 지금 문단) 동안 이미지 표시, 영상 위에서 끌어 옮기기·크기 조절·투명도 |
| 속도 | 0.5~4배, 원고 전체/고른 문단/지금 문단에 적용 (목소리 높이 유지) |
| 숏폼 | 긴 영상을 **주제가 바뀌는 곳에서 나눠** 세로(9:16) 숏폼으로 만들기. 1분 이하/3분 이하, 제목·구간 고치기, 꽉 채우기/흐린 배경, 자막. 숏폼마다 MP4 + 제목·해시태그·설명을 모은 `업로드_정보.txt` |

**완성본 만들기**: 영상 + 자막(MP4), 영상만(MP4), 자막 파일(SRT), 원고(TXT).

단축키: `Space` 재생/정지 · `Ctrl+Z` 되돌리기 · `Ctrl+Shift+Z` 다시 실행 · `←/→` 단어 이동 · 더블클릭·`F2` 단어 고치기(`Tab` 다음 단어, `Enter` 저장, `Esc` 취소) · `Enter` 문단 나누기 · `Esc` 선택 해제

## 개발자용

### 소스에서 실행

`소피컷 실행.bat`을 더블클릭하면 처음에 `.venv`를 만들고 패키지를 설치합니다(Python·ffmpeg가 없으면 winget으로 설치). 직접 실행하려면:

```bash
pip install -r requirements.txt      # GPU 가속: pip install -r requirements-gpu.txt
python -m sophicut                   # http://127.0.0.1:7860
```

소스 실행 시 데이터는 저장소의 `workspace/`에 저장됩니다.

### 새 버전 배포하기

1. `sophicut/__init__.py`의 `__version__`을 올립니다 (예: `1.0.1`).
2. 커밋하고 같은 버전의 태그를 푸시합니다.
   ```bash
   git commit -am "v1.0.1"
   git tag v1.0.1
   git push && git push --tags
   ```
3. GitHub Actions(`.github/workflows/release.yml`)가 `SophiCut-Setup.exe`를 빌드해 Release에 올립니다. 설치된 앱과 홈페이지가 자동으로 새 버전을 안내합니다.

로컬에서 설치 파일 만들기: `powershell -ExecutionPolicy Bypass -File packaging\build.ps1` (Inno Setup 6 필요) → `build\installer\SophiCut-Setup.exe`

### 구조

| 경로 | 역할 |
|---|---|
| `sophicut/transcriber.py` | faster-whisper 단어 단위 받아쓰기 (GPU 실패 시 CPU), GPU 라이브러리 내려받기 |
| `sophicut/editing.py` | 단어·빈틈 토큰 타임라인, 남길 구간 계산, 자막 줄 나누기, SRT/TXT |
| `sophicut/shorts.py` | 주제 경계 찾기(단어 겹침·쉼·"다음으로" 같은 말) → 길이 조건에 맞춰 숏폼 구간 나누기, 제목·해시태그 |
| `sophicut/subrender.py` | Pretendard로 자막을 투명 PNG로 렌더링 |
| `sophicut/media.py` | ffprobe, 미리보기 변환, 구간별 인코딩·연결, 자막·이미지 합성, 세로 숏폼 화면 (NVENC 자동, 실패 시 CPU) |
| `sophicut/app.py` | 로컬 웹 서버(127.0.0.1 전용), 업데이트 확인, 자동 종료 |
| `sophicut/static/` | 화면(UI) |
| `packaging/` | 설치 파일 빌드 (PyInstaller + Inno Setup) |
| `site/` | 홈페이지 (Netlify, `netlify.toml`) |

### 라이선스 고지

- 자막 글꼴: Pretendard — SIL Open Font License (`sophicut/fonts/OFL-LICENSE.txt`)
- 설치 파일에 포함된 FFmpeg: GPL 빌드 (BtbN/FFmpeg-Builds), 라이선스는 설치 폴더 `ffmpeg\FFMPEG-LICENSE.txt`
