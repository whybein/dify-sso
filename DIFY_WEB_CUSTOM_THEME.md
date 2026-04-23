# Dify Web 커스텀 테마 적용 가이드

Dify 임베드 챗봇 UI의 기본 파란색을 검정(#000000)으로 바꾸고 커스텀 Docker 이미지를 빌드하는 작업 가이드. 회사 환경에서 별도 dify-web 레포를 만들어 동일 수정을 반복 적용할 때 사용.

---

## 작업용 프롬프트 (Claude Code에 복사해서 사용)

```
Dify 웹앱(langgenius/dify-web:1.13.x)의 임베드 챗봇 UI에서 파란색을 모두 검정(#000000)으로 바꾸고, 커스텀 Docker 이미지를 빌드하려고 해.

## 준비
1. Dify 소스 클론 (아직 없으면):
   git clone https://github.com/langgenius/dify.git
   cd dify
   git checkout 1.13.3   # 또는 사용 중인 버전 태그
2. web/ 디렉토리에서만 작업.

## 수정 1: 테마 기본값 (파란 → 검정)

파일: web/app/components/base/chat/embedded-chatbot/theme/theme-context.ts

-  public primaryColor = '#1C64F2'
-  public backgroundHeaderColorStyle = 'backgroundImage: linear-gradient(to right, #2563eb, #0ea5e9)'
+  public primaryColor = '#000000'
+  public backgroundHeaderColorStyle = 'backgroundColor: #000000'
   public headerBorderBottomStyle = ''
   public colorFontOnHeaderStyle = 'color: white'
   public colorPathOnHeader = 'text-text-primary-on-surface'
-  public backgroundButtonDefaultColorStyle = 'backgroundColor: #1C64F2'
-  public roundedBackgroundColorStyle = 'backgroundColor: rgb(245 248 255)'
+  public backgroundButtonDefaultColorStyle = 'backgroundColor: #000000'
+  public roundedBackgroundColorStyle = 'backgroundColor: rgb(245 245 245)'
   public chatBubbleColorStyle = ''

configCustomColor() 안의 fallback도:
-      this.primaryColor = this.chatColorTheme ?? '#1C64F2'
+      this.primaryColor = this.chatColorTheme ?? '#000000'

## 수정 2: 로딩 애니메이션 색

파일: web/app/components/base/chat/chat/loading-anim/style.module.css

#155EEF 등장하는 곳(keyframe + .avatar background/color) 전부를 #000000으로,
rgba(21, 94, 239, 0.3)을 rgba(0, 0, 0, 0.3)으로 변경.

## 수정 3: 플로팅 임베드 버튼

파일: web/public/embed.js + web/public/embed.min.js

양쪽 모두 아래 라인을 교체:
-background-color: var(--${containerDiv.id}-bg-color, #155EEF);
+background-color: var(--${containerDiv.id}-bg-color, #000000);

## 수정 4: 테스트 파일 (CI 돌리면 필수)

파일: web/app/components/base/chat/embedded-chatbot/theme/__tests__/theme-context.spec.ts

- #1C64F2 → #000000 (전체 치환)
- 'backgroundImage: linear-gradient(to right, #2563eb, #0ea5e9)' → 'backgroundColor: #000000'

## 검증

grep -rn "1C64F2\|2563eb\|0ea5e9\|155EEF" web/app/components/base/chat/ web/public/embed*.js

결과가 비어있어야 함.

## Docker 이미지 빌드

cd web
docker build -t dify-web:custom-black .

## 배포 반영

docker/docker-compose.yaml의 web 서비스 이미지를 dify-web:custom-black으로 변경 후:
cd docker
docker compose up -d web

쿠버네티스 환경이면 해당 이미지를 private registry에 푸시하고 Deployment의 image 필드만 교체.

## 요구사항
- 기존 앱의 chat_color_theme DB 값은 건드리지 말 것. 이 수정은 "기본값"만 바꿈.
- Dify 버전 업그레이드 시 같은 수정을 다시 적용해야 하므로 변경사항은 별도 브랜치(custom/black-theme)로 유지할 것.
- 변경 후 web/.env.example 이나 다른 파일은 건드리지 말 것.

위 수정을 한 번에 적용하고, 마지막에 grep 검증 결과까지 보고해줘.
```

---

## 수정 대상 파일 요약

| 파일 | 목적 |
|---|---|
| `web/app/components/base/chat/embedded-chatbot/theme/theme-context.ts` | 테마 기본값 (primary color, 헤더 배경, 버튼 등) |
| `web/app/components/base/chat/chat/loading-anim/style.module.css` | 응답 대기 중 점 애니메이션 |
| `web/public/embed.js` | 서드파티 삽입 스크립트 (플로팅 버튼) |
| `web/public/embed.min.js` | 위 파일의 압축 버전 |
| `web/app/components/base/chat/embedded-chatbot/theme/__tests__/theme-context.spec.ts` | 기본값 assert 테스트 (CI 깨짐 방지) |

## 운영 관리 팁

- **별도 포크 레포 + 브랜치 전략**: `langgenius/dify`를 회사 조직으로 fork 후 `custom/black-theme` 브랜치에서 위 수정 유지. upstream 태그가 올라오면 해당 태그 체크아웃 → 브랜치 rebase.
- **이미지 태그 컨벤션**: `dify-web:{version}-custom-black` (예: `dify-web:1.13.3-custom-black`). 업그레이드 추적 용이.
- **CI**: `pnpm test theme-context`가 그린인지 확인 후 이미지 빌드.
- **롤백**: 기존 `langgenius/dify-web:{version}` 이미지로 되돌리기만 하면 됨 (DB 변경 없음).

## 참고

- 앱별로 Dify 콘솔에서 `chat_color_theme` 값을 지정해두면 해당 앱은 커스텀 색이 우선 적용됨. 이 가이드는 전역 기본값만 바꿈.
- 로컬에서 미리 확인하려면 `web/` 디렉토리에서 `pnpm install && pnpm dev` 후 `http://localhost:3000/chatbot/{token}` 접속.
