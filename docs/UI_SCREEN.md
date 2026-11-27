# 이용자 안내 화면·출력 연결 규격 (UI_SCREEN)

- 작성: 2026-11-26 · 규격 버전 **v1** (초안)
- 대상: 터치스크린 화면(JS) 담당자, 판정 Pi(C)·출력 Pi(D) 담당자
- 관련: [시스템 구조](ARCHITECTURE.md) §5(출력)·§7(코드 구조), [DECISIONS.md](../DECISIONS.md) DEC-006(UI)·DEC-007(상태 표시)·DEC-008(SOS)·DEC-011(RGB Pi 생존 감시)

> **한 줄 요약**
> 판정 Pi(C)의 Python이 보여 줄 내용(화면·음성·신호등)을 정해서 JSON으로 보내고, 출력 Pi(D)의 화면(JS)과 출력 프로그램(Python)은 **받은 대로 출력하기만** 합니다. 연결이 끊기면 D가 스스로 빨간 "판단 불가"로 바꿉니다.

---

## 1. 구성

```
 [Pi C] 판정 Pi
 ┌──────────────────────────────────────────────────────┐
 │  hub (판정) ──▶ ui: 출력 상태 서버 (Python, :8080)       │
 │                   GET  /            → web/screen/ 파일 │
 │                   GET  /api/state   → 현재 상태 JSON    │
 │                   GET  /api/events  → 상태 스트림 (SSE) │
 │                   POST /api/sos     → 관리자 호출 (예정) │
 └───────────────────────────┬──────────────────────────┘
                             │ HTTP (내부 네트워크)
 [Pi D] 출력 Pi               ▼
 ┌──────────────────────────────────────────────────────┐
 │  Chromium (키오스크) ── web/screen/ 의 HTML·JS·CSS ─▶ 터치스크린 │
 │  출력 프로그램 (Python, ui) ──┬─▶ 스피커 (음성 안내)        │
 │                             └─▶ ESP32 ─▶ 신호등        │
 └──────────────────────────────────────────────────────┘
```

- 상태 서버는 **판정 Pi(C)**에서 돌고, 출력 Pi(D)의 Chromium이 `http://<C 주소>:8080/`에 접속합니다. 화면 파일(`web/screen/`)도 C가 제공하므로, 화면을 고치면 C만 업데이트하면 됩니다.
- D의 **화면(JS)과 출력 프로그램(Python)은 같은 `/api/events`를 받습니다.** 그래서 화면·음성·신호등이 서로 다른 내용을 보여 줄 수 없습니다.
- 서버는 프로젝트 **내부 네트워크 주소에서만** 엽니다(인터넷에 노출하지 않음). 지금은 인증이 없으므로 같은 네트워크의 기기는 상태를 읽을 수 있습니다. 상태 읽기와 SOS(막는 쪽)만 있고 운행을 허가하는 요청은 없으므로, 받아들일 수 있는 범위로 봅니다.
- 관리자 알림(HTTP, ARCHITECTURE §5)은 이 경로와 **별도**입니다.
- 상태 서버는 Python 표준 라이브러리로 만들 수 있어 추가 설치가 필요 없습니다.

### 역할 분담

| 누가 | 하는 일 | 위치 |
|---|---|---|
| Python (판정 Pi C, 판정 담당) | 판정 → 출력 상태(`display`) 변환, 상태 서버, 이 규격 유지 | `src/psafe/ui/` |
| Python (출력 Pi D, 판정 담당) | 상태를 받아 음성 재생, 신호등(ESP32) 출력, 연결 끊김 처리 | `src/psafe/ui/` |
| JS (출력 Pi D, 화면 담당) | 화면 그리기, 연결 끊김 처리, 버튼(SOS 등) | `web/screen/` |

**무엇을 보여 주고 들려줄지(색, 제목, 안내 문구, 음성)는 판정 Pi의 Python이 정합니다.** JS가 판정 종류(`kind`)를 보고 색이나 문구를 다시 계산하지 않습니다. 그래야 판정 규칙이 바뀌어도 화면 코드를 고치지 않고, 판정과 화면이 어긋나지 않습니다.

### 폴더

```
web/screen/
├─ index.html
├─ app.js
├─ style.css
├─ assets/          아이콘, 글꼴
└─ samples/         아래 §3.3 예시 JSON (PC에서 화면만 개발할 때 사용)
```

---

## 2. 통신 방법

| 경로 | 방식 | 용도 |
|---|---|---|
| `GET /` | 정적 파일 | `web/screen/index.html`과 같은 폴더의 파일 |
| `GET /api/state` | JSON 한 번 | 처음 켤 때, 재연결 직후 현재 상태 |
| `GET /api/events` | **SSE** (Server-Sent Events) | 상태를 계속 받기. JS는 `EventSource`로 연결 |
| `POST /api/sos` | JSON | 관리자 호출 (예정, §5) |

### SSE를 쓰는 이유
- 서버 → 화면 **한 방향**만 필요합니다. 브라우저의 `EventSource`는 **끊기면 자동으로 다시 연결**합니다.
- WebSocket보다 단순하고, Python 쪽에 별도 라이브러리가 필요 없습니다.

### 보내는 주기
- 판정 Pi는 **하트비트마다(200 ms) 상태를 보냅니다. 바뀌지 않아도 보냅니다.** 이 메시지가 "살아 있음" 신호입니다.
- 이벤트 이름은 `state`이고, `data`는 §3의 JSON 한 줄입니다.

```
event: state
data: {"v":1,"seq":1234,...}

```

---

## 3. 상태 JSON (v1)

### 3.1 형식

```json
{
  "v": 1,
  "seq": 1234,
  "sent_at": "2026-12-03T14:03:21.120+09:00",
  "state": "CHECKING",
  "display": {
    "level": "WARNING",
    "color": "yellow",
    "title": "확인 중입니다",
    "message": "잠시만 기다려 주세요.",
    "action": null
  },
  "voice": {
    "name": "checking",
    "seq": 17
  },
  "signal": {
    "light": "yellow",
    "permit": false
  },
  "verdict": {
    "kind": "CHECKING",
    "reason": "A_verifying"
  },
  "nodes": [
    { "name": "A",   "role": "interior", "alive": true, "state": "VERIFYING", "count": 0, "reason": "clear", "evidence_age_ms": 210 },
    { "name": "B",   "role": "interior", "alive": true, "state": "CLEAR",     "count": 0, "reason": "clear", "evidence_age_ms": 190 },
    { "name": "RGB", "role": "exterior", "alive": true, "state": "CLEAR",     "count": 1, "reason": "clear", "evidence_age_ms": 340 }
  ],
  "estimate": {
    "interior": 0,
    "exterior": 1
  }
}
```

| 필드 | 값 | 설명 |
|---|---|---|
| `v` | `1` | 규격 버전. 다르면 JS는 "판단 불가" 화면 + 버전 불일치 표시 |
| `seq` | 정수, 계속 증가 | 메시지 번호. **같은 연결 안에서** 이전보다 작거나 같으면 무시 (오래된 메시지). C가 재시작하면 0부터 다시 시작하므로, 다시 연결한 뒤 첫 메시지는 받아들임 |
| `sent_at` | 시각 문자열 (ISO 8601) | 판정 Pi가 보낸 시각. "마지막 갱신" 표시용 |
| `state` | 출력 FSM 상태 이름 (§3.2) | 디버그·로그용. **출력을 정하는 데 쓰지 않음** |
| `display.level` | `IDLE` / `WARNING` / `BLOCKED` | DEC-007 표시 상태 |
| `display.color` | `green` / `yellow` / `red` | 화면 색. `level`과 짝이 정해져 있음 (IDLE=green, WARNING=yellow, BLOCKED=red) |
| `display.title` | 문자열 | 큰 글씨 제목 |
| `display.message` | 문자열 | 안내 문구 |
| `display.action` | 아래 표 또는 `null` | 이용자에게 요청하는 행동 (아이콘·버튼 표시용) |
| `voice.name` | 음성 이름 또는 `null` | D의 출력 프로그램이 재생할 녹음 파일 이름 (`<name>.wav`). 이름 목록은 §3.2, 녹음 문구는 DEC-006 |
| `voice.seq` | 정수 | **이 값이 바뀔 때만 한 번 재생**합니다. 언제 말할지(상태가 바뀔 때, 반복할 때)는 C가 정해 이 값을 올립니다 |
| `signal.light` | `green` / `yellow` / `red` | 신호등 색. 항상 `display.color`와 같음 |
| `signal.permit` | `true` / `false` | 운행 허가 (모의 운행 제한 신호). **판정이 `EMPTY`이고 초록일 때만 `true`** |
| `verdict.kind` | `EMPTY` / `OCCUPIED` / `CHECKING` / `UNKNOWN` | 판정 종류. **화면 색·문구를 정하는 데 쓰지 않음** (디버그·로그용) |
| `verdict.reason` | 문자열 | 판정 사유 (디버그용) |
| `nodes[]` | 카메라 Pi별 상태 (A·B·RGB) | 관리·디버그 화면용. 이용자 화면에서는 선택. **개수와 순서를 고정으로 가정하지 않습니다** (카메라가 늘거나 빠질 수 있음) |
| `nodes[].name` | 문자열 | 카메라 Pi 이름 (`A`, `B`, `RGB`) |
| `nodes[].role` | `interior` / `exterior` | `interior` = 차량 내부 인원(NoIR, A·B), `exterior` = 외부 인원(RGB) |
| `nodes[].alive` | `true` / `false` | 판정 Pi가 그 카메라의 보고를 제때 받고 있는지 |
| `nodes[].count` | 정수 | 그 카메라가 본 사람 수. **카메라끼리 더하지 않습니다** (겹치는 구역에서 두 번 세어질 수 있음) |
| `estimate.interior` | 정수 또는 `null` | 차량 내부 인원 추정 = `interior` 카메라 `count` 중 **가장 큰 값**. 살아 있는 `interior` 카메라가 하나도 없으면 `null` |
| `estimate.exterior` | 정수 또는 `null` | 외부 인원 추정 = `exterior` 카메라의 `count`. 살아 있지 않으면 `null` |

**출력마다 자기 블록만 봅니다.** 화면 JS는 `display`, 음성은 `voice`, 신호등은 `signal`만 씁니다.

**받는 쪽도 안전 규칙을 검사합니다.** 아래가 어긋난 메시지는 받지 못한 것과 같이 처리(빨간 표시)합니다. Python은 `src/psafe/ui/contract.py`에서 검사합니다.
- `display.level`과 `display.color`가 짝이 맞음, `signal.light` = `display.color`
- 초록은 `verdict.kind`가 `EMPTY`일 때만, `signal.permit: true`는 `EMPTY`이고 초록일 때만

`estimate`는 **표시 전용**입니다. 판정(`verdict`)은 이 숫자로 정하지 않고, 화면도 이 숫자로 색·문구를 바꾸지 않습니다(§4-1). 숫자를 보여 줄 때 `null`은 "—"(알 수 없음)으로 표시하고 `0`으로 바꾸지 않습니다.

`display.action` 값

| 값 | 뜻 |
|---|---|
| `check_inside` | 차량 내부를 확인해 주세요 |
| `open_window` | 유리창을 내려 주세요 |
| `call_admin` | 관리자를 호출했습니다 / 관리자를 기다려 주세요 |
| `null` | 요청 없음 |

### 3.2 출력 FSM: 판정 → 화면·음성·신호등 (Python이 정함)

판정 Pi(C)의 출력 FSM(`src/psafe/ui/output_fsm.py`)이 판정을 받아 아래 상태 중 하나로 정하고, 그 상태의 내용을 보냅니다. 문구는 DEC-006에서 확정되면 Python 쪽만 고칩니다.

| `state` | 들어가는 조건 | `level` / `color` | `title` (초안) | `action` | `voice.name` | `permit` |
|---|---|---|---|---|---|---|
| `STARTING` | 첫 판정 전 | `BLOCKED` / `red` | 확인할 수 없습니다 | `null` | — | `false` |
| `IDLE` | 판정 `EMPTY` | `IDLE` / `green` | 대기 중입니다 | `null` | `idle` | **`true` (유일)** |
| `CHECKING` | 판정 `CHECKING` | `WARNING` / `yellow` | 확인 중입니다 | `null` | `checking` | `false` |
| `OCCUPIED` | 판정 `OCCUPIED` | `WARNING` / `yellow` | 차량 안에 사람이 있습니다 | `check_inside` | `occupied` | `false` |
| `OCCUPIED_BLOCKED` | `OCCUPIED`가 `occupied_block_s` 이상 계속 | `BLOCKED` / `red` | 운행할 수 없습니다 | `call_admin` | `blocked` | `false` |
| `UNKNOWN` | 판정 `UNKNOWN` | `BLOCKED` / `red` | 확인할 수 없습니다 | `null` (미정) | `unknown` | `false` |
| `SHUTDOWN` | 판정 Pi 종료 | `BLOCKED` / `red` | 확인할 수 없습니다 | `null` | — | `false` |

규칙

- **운행 허가는 판정이 `EMPTY`일 때만** 나갑니다. 표시 설정을 바꿔도 이 조건은 코드로 고정되어 있습니다.
- 더 엄격한 쪽(초록 → 노랑 → 빨강)으로는 즉시 바뀝니다. 풀어 주는 쪽은 판정을 그대로 따릅니다. 판정이 이미 "푸는 건 느리게"를 지킵니다.
- `OCCUPIED_BLOCKED`로 바꾸는 시간은 아직 정하지 않았습니다(DEC-007). 정해지기 전까지는 사용하지 않으므로 `OCCUPIED`는 `WARNING`만 보냅니다.
- `UNKNOWN`의 `action`(유리창 개방 / 관리자 호출)은 정해지기 전까지 비워 둡니다. 실제로 호출하지 않았는데 "관리자를 호출했습니다"를 띄우면 안 되기 때문입니다.
- `IDLE`(초록)은 화면에서 **운행 허가를 뜻하지 않습니다**(DEC-007). 화면 문구에서도 "출발하세요" 같은 표현은 쓰지 않습니다.

음성 (`voice.seq`를 올리는 때)

- 더 엄격한 상태로 바뀌면 **즉시** 말합니다.
- 풀어지거나 같은 수준으로 바뀌면, 마지막 음성에서 `voice_gap_s`(기본 3초)가 지난 뒤 말합니다. 사람 있음 ↔ 확인 중이 빠르게 오갈 때 음성이 겹치지 않게 하기 위해서입니다. 그래서 화면은 초록이 되었는데 "대기 중" 음성은 조금 늦게 나올 수 있습니다.
- 같은 상태가 계속되면 `voice_repeat_s`에 적은 상태만 그 간격으로 반복합니다(기본: 반복 없음).

설정 (`config/hub_c.yaml`의 `state_server` 옵션): `occupied_block_s`, `voice_repeat_s`, `voice_gap_s`, `exterior`(외부 인원 카메라 이름).

### 3.3 예시 (JS 개발용)

`web/screen/samples/`에 아래 파일이 있습니다. 판정 Pi 없이 화면만 개발할 때 이 파일을 읽어 그리면 됩니다. 실제 출력 FSM 코드로 만든 것이라 규격과 어긋나지 않습니다. 규격이 바뀌면 `python -m psafe.ui.samples`로 다시 만들고, 테스트가 최신인지 검사합니다.

| 파일 | 상황 |
|---|---|
| `starting.json` | 판정 Pi가 막 켜짐 (첫 판정 전) |
| `idle.json` | 차가 비었고 대기 중 |
| `checking.json` | 사람이 사라졌고 확인 중 |
| `occupied.json` | B 카메라가 사람 1명을 봄 |
| `unknown.json` | A 카메라 무응답 |
| `rgb_lost.json` | RGB 카메라 무응답 (Wi-Fi 끊김). `estimate.exterior: null`, 판정 결과는 §8 결정에 따름 |

---

## 4. 출력 Pi(D)가 꼭 지켜야 할 규칙

### 화면(JS)이 지킬 규칙

안전과 직결되는 규칙입니다. 화면 디자인은 자유지만, 아래는 지켜 주세요.

1. **판단하지 않습니다.** `display`에 있는 값만 그립니다. `verdict.kind`나 `nodes`를 보고 색·문구를 바꾸지 않습니다.
2. **연결이 끊기면 스스로 빨간 화면으로 바꿉니다.** 마지막 메시지를 받은 뒤 **1초 넘게** 새 메시지가 없으면, 받은 값과 상관없이 `BLOCKED` / `red` / "확인할 수 없습니다 (화면 연결 끊김)"을 표시합니다. 판정 Pi가 멈추거나 C–D 연결이 끊겼을 때 **멈춘 초록 화면이 남지 않게** 하기 위해서입니다(신호등의 ESP32 워치독과 같은 원리).
3. **처음 켜질 때는 빨간 화면으로 시작합니다.** 첫 메시지를 받기 전에는 "확인할 수 없습니다 (연결 중)"을 표시합니다. 초록으로 시작하지 않습니다.
4. **살아 있음을 보여 줍니다.** 메시지마다 바뀌는 작은 표시(예: 점 깜빡임)와 "마지막 갱신 N초 전"을 화면 구석에 둡니다.
5. **색만으로 구분하지 않습니다.** 색과 함께 제목 글자(가능하면 아이콘)를 표시합니다. 고령자·색각 이상 이용자를 위한 것입니다(DEC-006).
6. **오래된 메시지는 버립니다.** 같은 연결 안에서 `seq`가 마지막으로 받은 값보다 작거나 같으면 무시합니다. `EventSource`가 다시 연결되면(`open` 이벤트) 마지막 `seq`를 잊습니다. 판정 Pi가 재시작하면 `seq`가 0부터 다시 시작하기 때문입니다.
7. **규격 버전이 다르면** "확인할 수 없습니다 (화면 버전 불일치)"를 표시합니다.

### 출력 프로그램(D의 Python)이 지킬 규칙

화면 JS와 같은 원칙입니다. 판정 담당이 구현합니다.

1. **판단하지 않습니다.** 신호등은 `signal`, 음성은 `voice`만 따릅니다. 구현: `src/psafe/ui/client.py`.
2. **1초 넘게 새 메시지가 없거나, 처음 켜져 아직 받지 못했거나, 버전이 다르면** 신호등에 빨강을 보냅니다. 초록을 다시 보내지 않으므로, D 프로그램 자체가 죽어도 ESP32 워치독(500 ms)이 빨간불로 바꿉니다.
3. **음성은 `voice.seq`가 바뀔 때만 한 번 재생합니다.** 연결이 끊기면 `link_lost` 음성을 한 번 재생합니다(시작 중·종료 때는 말하지 않음).
5. **신호등 → ESP32:** 전송 형식(프로토콜 v1, 5바이트)은 그대로 쓰고 색을 상태 코드로 바꿔 보냅니다. 초록+허가 → `CLEAR`+`Y`, 노랑 → `VERIFYING`+`N`, 빨강 → `FAULT`+`N`. 지금 펌웨어는 Y/N만 켜므로 노랑은 빨강처럼 보입니다. 3색은 펌웨어에서 상태 코드로 색을 고르면 되고, 전송 형식은 바뀌지 않습니다.
4. **같은 연결 안에서 `seq`가 이전보다 작거나 같은 메시지는 버립니다.** 다시 연결한 뒤 첫 메시지는 받아들입니다.

---

## 5. SOS 버튼 (예정, DEC-008)

- `POST /api/sos` 본문 `{}` → 응답 `{"accepted": true}`
- 판정 Pi는 관리자 알림을 보내고, 이후 상태 JSON에 `display.action: "call_admin"`으로 반영합니다.
- **SOS는 막는 쪽으로만 동작합니다.** 화면에서 운행을 허가하거나 "비었음"으로 바꾸는 버튼은 두지 않습니다.
- 자세한 동작(취소, 중복 누름, 관리자 응답 표시)은 DEC-008과 함께 정합니다.

---

## 6. 실행

```
# Pi C: 판정 + 출력 상태 서버
python -m psafe --config config/hub_c.yaml

# Pi D: 출력 프로그램 (음성 · 신호등)
python -m psafe --config config/output_d.yaml

# Pi D: Chromium 키오스크 (배포판에 따라 chromium 또는 chromium-browser)
chromium --kiosk --noerrdialogs --disable-infobars http://<C 주소>:8080/
```

PC에서 화면만 개발할 때는 `web/screen/index.html`을 열고 `samples/`의 JSON을 읽어 그립니다. 상태를 바꿔 가며 보내 주는 시험용 서버는 화면 서버를 구현할 때 함께 만듭니다.

---

## 7. 규격 바꾸기

- **필드를 추가**하는 것은 `v`를 유지합니다. JS는 모르는 필드를 무시합니다.
- **필드의 뜻을 바꾸거나 지우는 것**은 `v`를 올리고, 이 문서와 Python·JS를 **같은 PR**에서 함께 고칩니다.

## 8. 아직 정하지 않은 것

| 항목 | 관련 |
|---|---|
| 화면 문구 확정 (제목, 안내 문구) | DEC-006 |
| `OCCUPIED` → `BLOCKED` 전환 조건 | DEC-007 |
| `UNKNOWN` 사유별 `action` (유리창 개방 vs 관리자 호출) | DEC-004, DEC-007 |
| SOS 동작 | DEC-008 |
| 이용자 화면에 카메라별 상태를 보여 줄지 | DEC-006 |
| 이용자 화면에 `estimate`(내부·외부 인원 수)를 보여 줄지 | DEC-006 |
| RGB(외부) 결과를 판정에 넣는 규칙 | ARCHITECTURE §9-1 |
| RGB Pi 보고가 끊길 때 판정·화면 (전체 UNKNOWN vs "외부 확인 불가"만 표시). MCU 생존 감시가 들어오면 "장비 고장 / 통신 불안정"을 나눠 보여 줄지 | ARCHITECTURE §9-2, DEC-011 |
| 음성 안내와 화면 문구를 맞출지, `voice.name` 이름 목록 | DEC-006 |
| 출력 Pi(D) 네트워크 (유선 / Wi-Fi), D 고장을 C·관리자가 알 방법 | ARCHITECTURE §9-12·13 |

이 구조의 알려진 약점(C–D 연결이 끊길 때 허가가 남는 시간, D 고장 감지, 인증, 음성 지연 등)은 [ARCHITECTURE.md §10](ARCHITECTURE.md#10-알려진-약점과-위험-출력-구조-2026-11-27)에 정리되어 있습니다.
