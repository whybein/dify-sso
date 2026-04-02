# Dify SSO (Authelia + LDAP)

> [lework/dify-sso](https://github.com/lework/dify-sso)를 Fork하여 **Authelia(LDAP) 환경에 맞게 수정**한 버전입니다.

Dify 무료 버전에서 SSO를 사용할 수 있도록 해주는 Flask 기반 백엔드 서비스입니다.
Dify 접속 시 **Authelia 로그인 화면 하나만** 거치면 Dify에 자동 로그인됩니다.

## 원본 대비 변경사항

### 보안 강화
- **OAuth State**: 하드코딩(`'random_state'`) → 매 요청 랜덤 생성 + Redis 검증 (CSRF 방어)
- **PKCE (S256)**: 인가 코드 탈취 방지
- **Nonce**: ID 토큰 재전송 공격 방지
- **토큰 URL 노출 제거**: URL 쿼리 파라미터 → short-lived code 교환 방식
- **Refresh Token 해시**: Redis에 SHA-256 해시로 저장
- **CORS 제한**: `*` → `CONSOLE_WEB_URL` 도메인만 허용
- **입력 검증**: 이메일 형식/길이, 이름 길이 제한
- **에러 은닉**: 내부 에러 메시지가 클라이언트에 노출되지 않음

### 환경 설정
- Authelia 경로 기반 설정 (`/auth` subpath) 지원
- 중국 미러(알리윈) 제거, 타임존 `Asia/Seoul`
- 모든 중국어 주석/로그 → 영어 변환

## 동작 방식

```
사용자 → Nginx(/signin) → dify-sso → Authelia(/auth) → LDAP 인증
                                ↓
                        Dify DB 사용자 생성/조회
                                ↓
                        JWT 토큰 발급 → Dify 콘솔
```

dify-sso는 Dify와 **동일한 DB, Redis, SECRET_KEY**를 공유하여 토큰 호환성을 유지합니다.

## 빠른 시작

### 1. 설정

```bash
cp .env.example .env
# .env 편집 — 상세 설정은 SETUP_GUIDE.md 참조
```

### 2. 빌드 및 실행

```bash
# Docker 빌드
docker build -t dify-sso-authelia:1.0.0 .

# 실행
docker run -p 8000:8000 --env-file .env dify-sso-authelia:1.0.0
```

### 3. 헬스체크

```bash
curl http://localhost:8000/health?detail=true
# {"status": "healthy", "database": true, "redis": true}
```

## 문서

| 문서 | 설명 |
|------|------|
| [SETUP_GUIDE.md](./SETUP_GUIDE.md) | 전체 설정 가이드 (Authelia, Nginx, .env, 트러블슈팅) |
| [examples/](./examples/) | Authelia 설정 및 Nginx 설정 샘플 파일 |
| [.env.example](./.env.example) | 환경 변수 템플릿 |

## 프로젝트 구조

```
dify-sso/
├── app/
│   ├── api/dify/          # API 엔드포인트 (SSO, 권한, 워크스페이스)
│   ├── configs/           # Pydantic 기반 설정
│   ├── extensions/        # Flask 확장 (DB, Redis, OIDC, CORS)
│   ├── models/            # SQLAlchemy 모델 (Account, Tenant)
│   ├── services/          # 비즈니스 로직 (OIDC, 토큰, 패스포트)
│   └── main.py            # 앱 진입점
├── examples/              # Authelia, Nginx 설정 샘플
├── SETUP_GUIDE.md         # 전체 설정 가이드
├── Dockerfile
├── requirements.txt
└── .env.example
```

## 기술 스택

- Python 3.11 / Flask / Gunicorn
- SQLAlchemy (PostgreSQL)
- Redis
- PyJWT (HS256)
- OIDC (Authorization Code Flow + PKCE)

## 라이선스

MIT — [LICENSE](LICENSE) 참조

## 참고

- 원본 프로젝트: [lework/dify-sso](https://github.com/lework/dify-sso)
- [OpenID Connect 사양](https://openid.net/connect/)
- [Authelia OIDC 문서](https://www.authelia.com/configuration/identity-providers/openid-connect/provider/)

> **안내**: 이 프로젝트는 Dify 소스 코드를 수정하지 않는 독립 외부 연동입니다. Dify 공식 엔터프라이즈 SSO와는 무관하며, 내부 인프라에서 통합 인증이 필요한 경우를 위한 것입니다.
