# Dify SSO 설정 가이드 (Authelia + LDAP)

Dify 무료 버전에 SSO를 추가하여 Authelia(LDAP) 한 번의 로그인으로 Dify를 사용할 수 있도록 설정하는 가이드입니다.

## 목표

- Dify 접속 시 로그인하지 않은 상태면 **Authelia로 자동 리다이렉트**
- Authelia에서 LDAP 계정으로 로그인하면 **Dify에 자동 로그인/사용자 생성**
- Dify 자체 로그인 화면은 **보이지 않음** (SSO 단일 로그인)

## 전체 흐름

```
사용자 브라우저          Nginx              dify-sso           Authelia           Dify
     │                   │                    │                  │                 │
     │─── /signin ──────>│                    │                  │                 │
     │<── 302 redirect ──│                    │                  │                 │
     │─── /sso/login ───>│───────────────────>│                  │                 │
     │<── 302 redirect ──│<───────────────────│                  │                 │
     │─── authorize ────>│                    │                  │                 │
     │                   │──────────────────────────────────────>│                 │
     │<──── 로그인 화면 ────│                    │                  │                 │
     │──── LDAP 인증 ────>│                    │                  │                 │
     │<── 302 callback ──│<──────────────────────────────────────│                 │
     │─── /callback ────>│───────────────────>│                  │                 │
     │                   │                    │──── 토큰 교환 ────>│                 │
     │                   │                    │<─── 사용자 정보 ────│                 │
     │                   │                    │──────── DB 사용자 생성/조회 ──────────>│
     │<── 302 + Cookie ──│<───────────────────│                  │                 │
     │──── Dify 콘솔 ────>│                    │                  │                 │
```

**사용자가 보는 화면: Authelia 로그인 화면 1개뿐**

---

## 1단계: 사전 준비

### 필요 정보 확인

| 항목 | 설명 | 예시 |
|------|------|------|
| Dify 도메인 | Dify 웹 콘솔 URL | `https://dify.example.com` |
| Authelia 도메인 | Authelia 인증 서버 URL | `https://auth.example.com` |
| Dify SECRET_KEY | Dify `.env`의 SECRET_KEY | `openssl rand -base64 42`로 생성 |
| Dify DB 접속 정보 | PostgreSQL 호스트/포트/계정 | dify-sso가 Dify와 동일 DB 사용 |
| Dify Redis 접속 정보 | Redis 호스트/포트/비밀번호 | dify-sso가 Dify와 동일 Redis 사용 |
| TENANT_ID | Dify 워크스페이스 ID | DB에서 확인 (아래 참조) |

### TENANT_ID 확인

Dify PostgreSQL에서 실행:

```sql
SELECT id FROM tenants LIMIT 1;
```

---

## 2단계: Authelia 설정

Authelia `configuration.yml`에 OIDC 클라이언트를 추가합니다.

```yaml
identity_providers:
  oidc:
    clients:
      - client_id: dify
        client_name: Dify
        client_secret: '<your-secret-hash>'  # 아래 명령으로 생성
        public: false
        authorization_policy: two_factor      # 또는 one_factor
        redirect_uris:
          - https://dify.example.com/console/api/enterprise/sso/oidc/callback
        scopes:
          - openid
          - profile
          - email
        response_types:
          - code
        token_endpoint_auth_method: client_secret_post
        pkce_challenge_method: S256           # PKCE 보안 필수
```

### client_secret 생성

```bash
# 평문 시크릿을 먼저 정하고 (예: my-dify-secret)
# Authelia용 해시 생성:
docker run --rm authelia/authelia:latest \
  authelia crypto hash generate argon2 --password 'my-dify-secret'
```

생성된 해시값을 `client_secret`에 넣고, **평문 값**(`my-dify-secret`)은 dify-sso `.env`에 사용합니다.

> **참고**: Authelia v4.38 이상이 필요합니다 (PKCE S256 지원).

---

## 3단계: dify-sso 환경 설정

### .env 파일 작성

```bash
cp .env.example .env
```

```env
# ── 서비스 설정 ──
CONSOLE_WEB_URL=https://dify.example.com
SECRET_KEY=<Dify의 SECRET_KEY와 반드시 동일>
TENANT_ID=<1단계에서 확인한 TENANT_ID>
EDITION=SELF_HOSTED
ACCOUNT_DEFAULT_ROLE=editor

# ── OIDC 설정 (Authelia) ──
OIDC_CLIENT_ID=dify
OIDC_CLIENT_SECRET=my-dify-secret            # 평문 시크릿 (Authelia에는 해시값 입력)
OIDC_DISCOVERY_URL=https://auth.example.com/.well-known/openid-configuration
OIDC_REDIRECT_URI=https://dify.example.com/console/api/enterprise/sso/oidc/callback
OIDC_SCOPE=openid profile email
OIDC_RESPONSE_TYPE=code

# ── PostgreSQL (Dify와 동일) ──
DB_HOST=<dify-db-host>
DB_PORT=5432
DB_DATABASE=dify
DB_USERNAME=<dify-db-user>
DB_PASSWORD=<dify-db-password>

# ── Redis (Dify와 동일) ──
REDIS_HOST=<dify-redis-host>
REDIS_PORT=6379
REDIS_PASSWORD=<dify-redis-password>
REDIS_DB=0

# ── 토큰 설정 (선택) ──
ACCESS_TOKEN_EXPIRE_MINUTES=900
REFRESH_TOKEN_EXPIRE_DAYS=30
```

> **중요**: `SECRET_KEY`와 DB/Redis는 Dify와 동일해야 합니다. 같은 JWT 토큰과 사용자 DB를 공유합니다.

---

## 4단계: dify-sso 배포

### Docker Compose (권장)

```yaml
# docker-compose.yml
version: '3.8'
services:
  dify-sso:
    build: .
    # 또는 이미지 사용: image: your-registry/dify-sso:latest
    ports:
      - "8000:8000"
    env_file:
      - .env
    restart: unless-stopped
    networks:
      - dify-network  # Dify의 DB, Redis와 같은 네트워크

networks:
  dify-network:
    external: true  # Dify의 기존 네트워크에 연결
```

```bash
docker compose up -d
```

### 기존 Dify docker-compose에 추가하는 경우

Dify의 `docker-compose.yml`에 서비스를 추가:

```yaml
  dify-sso:
    build: /path/to/dify-sso
    ports:
      - "8000:8000"
    env_file:
      - /path/to/dify-sso/.env
    restart: unless-stopped
    depends_on:
      - db
      - redis
```

### 헬스체크 확인

```bash
curl http://localhost:8000/health
# 응답: {"status": "healthy"}

# 상세 확인 (DB, Redis 연결 확인)
curl http://localhost:8000/health?detail=true
# 응답: {"status": "healthy", "database": true, "redis": true}
```

---

## 5단계: Nginx 프록시 설정

Dify의 Nginx 설정에 아래 내용을 추가합니다. SSO 관련 요청만 dify-sso로 라우팅합니다.

```nginx
server {
    listen 443 ssl;
    server_name dify.example.com;

    # ... 기존 SSL 및 Dify 설정 ...

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # SSO 설정 (기존 Dify location 블록보다 위에 배치)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    # [1] 로그인 화면 바이패스 — Dify 로그인 페이지 대신 바로 SSO로 이동
    location = /signin {
        return 302 /console/api/enterprise/sso/oidc/login?is_login=true;
    }

    # [2] SSO 인증 엔드포인트 → dify-sso
    location ~ ^/console/api/enterprise/sso/ {
        proxy_pass http://dify-sso:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # [3] 시스템 기능 API → dify-sso (SSO 강제 설정 반환)
    location = /console/api/system-features {
        proxy_pass http://dify-sso:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # [4] Features API → dify-sso
    location = /console/api/features {
        proxy_pass http://dify-sso:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # [5] Enterprise info → dify-sso
    location = /info {
        proxy_pass http://dify-sso:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # [6] WebApp SSO 관련 → dify-sso
    location ~ ^/api/enterprise/sso/ {
        proxy_pass http://dify-sso:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # [7] WebApp 접근 제어 → dify-sso
    location ~ ^/(webapp/|app-sso-setting|sso/|check-credential-policy-compliance) {
        proxy_pass http://dify-sso:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # [8] Workspace API → dify-sso
    location ~ ^/console/api/enterprise/workspace/ {
        proxy_pass http://dify-sso:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # [9] WebApp permission/access-mode → dify-sso
    location ~ ^/console/api/enterprise/webapp/ {
        proxy_pass http://dify-sso:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # ... 기존 Dify 프록시 설정 (아래에 위치) ...
    # location / {
    #     proxy_pass http://dify-web:3000;
    #     ...
    # }
    # location /console/api {
    #     proxy_pass http://dify-api:5001;
    #     ...
    # }
}
```

> **중요**: SSO location 블록은 기존 Dify의 `/console/api` 블록보다 **위에** 배치해야 합니다. Nginx는 위에서부터 매칭하므로, SSO 요청이 Dify API로 가지 않도록 먼저 잡아야 합니다.

### Nginx 설정 테스트 및 적용

```bash
nginx -t && nginx -s reload
```

---

## 6단계: 동작 확인

### 1. 서비스 상태 확인

```bash
# dify-sso 헬스체크
curl https://dify.example.com/health?detail=true

# OIDC 설정 로드 확인
curl https://dify.example.com/console/api/system-features
# sso_enforced_for_signin: true 확인
```

### 2. 로그인 흐름 테스트

1. 브라우저에서 `https://dify.example.com` 접속
2. **Dify 로그인 화면이 보이지 않고** 바로 Authelia 로그인 화면으로 이동되는지 확인
3. LDAP 계정으로 로그인
4. Dify 콘솔로 자동 리다이렉트되는지 확인

### 3. 사용자 생성 확인

```sql
-- Dify DB에서 SSO로 생성된 사용자 확인
SELECT id, name, email, status, last_login_at
FROM accounts
ORDER BY created_at DESC
LIMIT 5;
```

---

## 문제 해결 (Troubleshooting)

### "Failed to load OIDC configuration"

- `OIDC_DISCOVERY_URL`이 올바른지 확인
- dify-sso 컨테이너에서 Authelia에 네트워크 접근 가능한지 확인

```bash
# 컨테이너 안에서 테스트
docker exec dify-sso curl -s https://auth.example.com/.well-known/openid-configuration
```

### "Invalid or expired OAuth state"

- dify-sso와 Dify가 같은 Redis를 사용하는지 확인
- 인증 요청 후 5분 이내에 콜백이 오는지 확인 (state TTL: 5분)

### "Authentication failed" (콜백 에러)

- Authelia의 `redirect_uris`에 콜백 URL이 정확히 등록되어 있는지 확인
- `OIDC_CLIENT_SECRET`이 평문값인지 확인 (해시값이 아님)
- Authelia 로그에서 상세 에러 확인

### 로그인 화면이 여전히 보이는 경우

- Nginx 설정에서 `location = /signin` 블록이 Dify 프록시 블록보다 위에 있는지 확인
- Nginx 캐시 정리: `nginx -s reload`
- 브라우저 캐시/쿠키 삭제 후 재시도

### 사용자가 생성되지 않는 경우

- dify-sso 로그 확인: `docker logs dify-sso`
- `TENANT_ID`가 올바른지 확인
- `SECRET_KEY`가 Dify와 동일한지 확인

---

## 보안 참고사항

이 프로젝트에는 다음 보안 기능이 적용되어 있습니다:

| 기능 | 설명 |
|------|------|
| **CSRF 방어 (OAuth State)** | 매 인증 요청마다 암호학적 랜덤 state 생성, Redis 저장, 콜백에서 검증 |
| **PKCE (S256)** | 인가 코드 탈취 방지, code_verifier/code_challenge 사용 |
| **Nonce** | ID 토큰 재전송 공격 방지 |
| **토큰 보호** | 토큰이 URL에 노출되지 않음 (short-lived code 교환 방식) |
| **Refresh Token 해시** | Redis에 SHA-256 해시로 저장 (Redis 침해 시 토큰 직접 노출 방지) |
| **CORS 제한** | `CONSOLE_WEB_URL` 도메인만 허용 |
| **입력 검증** | 이메일 형식/길이 검증, 이름 길이 제한 |
| **에러 은닉** | 인증 실패 시 내부 에러 메시지가 클라이언트에 노출되지 않음 |
| **쿠키 보안** | HttpOnly, Secure, SameSite=Lax, __Host- prefix (HTTPS) |

---

## 역할(Role) 매핑

Authelia에서 사용자에게 roles claim을 설정하면 Dify 역할과 자동 매핑됩니다.

| Authelia Role | Dify Role | 설명 |
|---------------|-----------|------|
| `admin` | Admin | 전체 관리자 |
| `editor` | Editor | 앱 편집 가능 |
| `normal` | Normal | 읽기 전용 |
| (없음) | `ACCOUNT_DEFAULT_ROLE` | .env에서 설정한 기본 역할 |

Authelia에서 roles claim을 설정하지 않으면 `.env`의 `ACCOUNT_DEFAULT_ROLE` (기본값: `editor`)이 적용됩니다.

---

## 참고: WebApp SSO

Dify WebApp(외부 공개 앱)에도 SSO를 적용하려면, Dify 관리 콘솔에서 앱별 접근 모드를 설정할 수 있습니다.

- `public`: 누구나 접근 가능
- `sso_verified`: SSO 인증된 사용자만 접근 가능
- `private_all`: SSO 인증 + 특정 사용자만 접근 가능

WebApp SSO는 콘솔 SSO와 동일한 Authelia 인증을 사용하며, 별도의 설정은 필요하지 않습니다.
