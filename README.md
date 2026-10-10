# 도로시 (Dorothy) — 나만의 AI 비서

나를 알고, 놓친 것을 챙겨 주는 개인 비서 모바일 앱입니다. Expo(React Native)로 만들었고, 두뇌는 Claude가 맡습니다.

## 할 수 있는 일

| 탭 | 기능 |
|---|---|
| ☀️ 오늘 | 기한 지난 일·오늘 할 일 요약, 할 일·기억·날씨를 모은 **AI 브리핑** |
| 💬 도로시 | 대화형 비서. 말만 하면 알아서 **기억하고**, **할 일과 알림을 등록하고**, **웹에서 최신 정보를 찾아** 답합니다 |
| ✅ 할 일 | 할 일 목록, 정해진 시각에 휴대폰 알림 |
| 🧠 기억 | 도로시가 알고 있는 나에 대한 사실. 직접 보고, 추가하고, 지울 수 있습니다 |
| ⚙️ 설정 | 내 소개, API 키, Google 연결, 데이터 가져오기, 매일 아침 브리핑 알림 |

**Google 캘린더 · Gmail 연동** (연결했을 때)
- 오늘 화면에 오늘 일정이 보이고, 브리핑에 앞으로 3일 일정과 안 읽은 중요 메일이 들어갑니다.
- "내일 오후 3시 김포 미팅 잡아줘" → 캘린더에 일정 생성
- "이번 주 일정 알려줘", "KF 견적 메일 찾아서 요약해줘" → 캘린더·메일을 직접 확인
- "그 메일에 답장 써줘" → **Gmail 임시보관함에 초안만 저장**합니다. 도로시는 메일을 직접 보내지 않으니, Gmail에서 확인하고 보내세요.

예시:
- "나 다음 달부터 판교로 출근해. 커피는 디카페인만 마셔" → 기억 2개 저장
- "금요일 오후 3시 치과 예약 알려줘" → 할 일 등록 + 알림 예약
- "이번 주에 놓친 거 있어?" → 기한 지난 일, 다가오는 기념일 등을 짚어 줌

## 실행 방법

```bash
npm install
npx expo start
```

휴대폰에 **Expo Go** 앱을 설치하고 터미널에 뜨는 QR 코드를 찍으면 바로 실행됩니다.
처음 실행하면 **설정** 탭에서 다음 두 가지를 해 주세요.

1. [Claude API 키](https://platform.claude.com/settings/keys)를 입력하기
2. "나에 대해"에 하는 일, 사는 곳, 가족, 관심사를 적기 (도로시가 처음부터 나를 알게 됩니다)

### Google 연결 설정 (처음 한 번)

Google 로그인은 Expo Go에서는 동작하지 않고 **개발 빌드**가 필요합니다.

1. [Google Cloud Console](https://console.cloud.google.com/)에서 프로젝트를 만들고 **Google Calendar API**와 **Gmail API**를 사용 설정합니다.
2. **OAuth 동의 화면**을 "외부 / 테스트"로 만들고, 테스트 사용자에 내 Gmail 주소를 추가합니다.
3. **사용자 인증 정보 → OAuth 클라이언트 ID**를 만듭니다.
   - iOS: 유형 "iOS", 번들 ID `com.dorothybrew.app`
   - Android: 유형 "Android", 패키지 이름 `com.dorothybrew.app`, 개발 빌드의 SHA-1 지문 입력. 만든 뒤 **고급 설정 → 맞춤 URI 스킴 사용**을 켭니다.
4. `.env.example`을 `.env.local`로 복사하고 Client ID를 넣습니다.
5. `npx expo run:android` 또는 `npx expo run:ios`로 다시 빌드합니다 (또는 `npx eas-cli@latest build --profile development`).
6. 앱의 **설정 → Google 계정 연결**을 누릅니다.

### 데이터 가져오기

설정 → **데이터 가져오기**에 아래 형식의 JSON을 붙여 넣으면 프로필·기억·할 일이 한 번에 들어갑니다. 이미 있는 기억과 할 일은 건너뜁니다.

```json
{
  "profile": { "name": "호칭", "about": "자기소개" },
  "memories": [{ "category": "일", "content": "기억할 사실" }],
  "tasks": [{ "title": "할 일", "dueAt": "2026-10-13T09:00:00+09:00", "notes": "메모" }]
}
```

> 알림 예약은 Expo Go에서 제한될 수 있습니다. 알림까지 제대로 쓰려면 `npx expo run:android` / `npx expo run:ios`로 개발 빌드를 만들거나 EAS Build로 설치하세요.

## 구조

```
src/
  app/            화면 (Expo Router, 파일 하나가 탭 하나)
  lib/assistant.ts  Claude 호출, 도구(remember / forget / add_task / complete_task / web_search)
  lib/store.tsx     기억·할 일·대화 저장 (AsyncStorage, API 키는 SecureStore)
  lib/notifications.ts  할 일 알림, 아침 브리핑 알림
  lib/google.ts     Google 로그인(PKCE), 캘린더·Gmail API
```

## 개인정보

- 기억, 할 일, 대화는 모두 **이 휴대폰에만** 저장됩니다. 대화하거나 브리핑을 받을 때만 Claude API로 전송됩니다.
- Google 로그인 토큰도 기기의 보안 저장소에만 저장되고, 설정에서 연결 해제하면 토큰이 폐기됩니다.
- API 키는 기기의 보안 저장소(Keychain/Keystore)에 보관됩니다. 이 앱은 혼자 쓰는 용도로 설계되어 휴대폰에서 바로 API를 호출합니다. 다른 사람에게 배포하려면 키를 서버 뒤로 옮겨야 합니다.

## 다음에 붙이면 좋을 것

- 음성으로 말 걸기 ("헤이 도로시")
- 위치 기반 알림 ("마트 근처에 가면 우유 사라고 알려줘")
- 여러 기기 동기화를 위한 백엔드
