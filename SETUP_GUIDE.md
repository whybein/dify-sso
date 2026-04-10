# Dify SSO 설정 가이드 (Authelia + LDAP)

Dify 무료 버전에 SSO를 추가하여 Authelia(LDAP) 한 번의 로그인으로 Dify를 사용할 수 있도록 설정하는 가이드입니다.

Authelia는 별도 서브도메인 없이 **경로 기반** (`dify.example.com/auth`)으로 구성합니다.

## 목표

- Dify 접속 시 로그인하지 않은 상태면 **Authelia로 자동 리다이렉트**
- Authelia에서 LDAP 계정으로 로그인하면 **Dify에 자동 로그인/사용자 생성**
- Dify 자체 로그인 화면은 **보이지 않음** (SSO 단일 로그인)
- 관리자는 `/admin-login` 경로로 **Dify 기본 로그인**(이메일/비밀번호) 사용 가능
- 모든 서비스가 **하나의 도메인** (`dify.example.com`)에서 동작

## 전체 흐름

### 일반 사용자 (SSO/LDAP)

```
사용자 브라우저          Nginx              dify-sso           Authelia(/auth)    Dify
     │                   │                    │                  │                 │
     │─── /signin ──────>│                    │                  │                 │
     │<── 302 redirect ──│                    │                  │                 │
     │─── /sso/login ───>│───────────────────>│                  │                 │
     │<── 302 redirect ──│<───────────────────│                  │                 │
     │─── /auth/... ────>│                    │                  │                 │
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

### 관리자 (Dify 기본 로그인)

```
관리자 브라우저                dify-sso                 Dify
     │                          │                       │
     │─── /admin-login ────────>│                       │
     │<── 쿠키 설정 + 302 ────────│                       │
     │─── /signin ─────────────>│                       │
     │    (system-features 호출) │                       │
     │<── 이메일/비밀번호 활성화 ───│                       │
     │─── 이메일/비밀번호 입력 ────────────────────────────>│
     │<── JWT 토큰 + 로그인 ──────────────────────────────│
```

**관리자 접속 URL: `https://dify.example.com/admin-login`**

---

## 1단계: 사전 준비

### 필요 정보 확인

| 항목 | 설명 | 예시 |
|------|------|------|
| Dify 도메인 | Dify 웹 콘솔 URL (Authelia도 동일 도메인 사용) | `https://dify.example.com` |
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

### 경로 기반 설정 (`/auth`)

Authelia `configuration.yml`에서 `server.path`를 설정하면 모든 엔드포인트가 `/auth/` 하위로 이동합니다.

```yaml
server:
  address: 'tcp://0.0.0.0:9091/'
  path: 'auth'  # ← 이 설정으로 /auth 프리픽스 적용
```

이 설정에 의해 Authelia 엔드포인트가 다음과 같이 변경됩니다:

| 기존 (서브도메인) | 변경 후 (경로 기반) |
|---|---|
| `https://auth.example.com/.well-known/openid-configuration` | `https://dify.example.com/auth/.well-known/openid-configuration` |
| `https://auth.example.com/api/oidc/authorization` | `https://dify.example.com/auth/api/oidc/authorization` |
| `https://auth.example.com/api/oidc/token` | `https://dify.example.com/auth/api/oidc/token` |

### OIDC 클라이언트 등록

Authelia `configuration.yml`의 `identity_providers` 섹션에 Dify를 OIDC 클라이언트로 등록합니다.

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

평문 시크릿을 먼저 생성하고, argon2id 해시를 만듭니다.

**평문 시크릿 생성:**
```bash
# Linux/macOS
openssl rand -hex 16

# Windows PowerShell
$b = New-Object byte[] 16; [System.Security.Cryptography.RandomNumberGenerator]::Fill($b); ([System.BitConverter]::ToString($b)).Replace('-','').ToLower()
```

**argon2id 해시 생성 (아무 도구나 사용 가능):**

**Python (모든 OS — 가장 간편):**
```bash
pip install argon2-cffi
python -c "from argon2 import PasswordHasher; print(PasswordHasher().hash('<평문 시크릿>'))"
```

**Node.js:**
```bash
npm install argon2
node -e "require('argon2').hash('<평문 시크릿>').then(console.log)"
```

**Linux (argon2 CLI):**
```bash
# argon2-utils 패키지 설치 후
echo -n '<평문 시크릿>' | argon2 "$(openssl rand -base64 16)" -id -e
```

출력 예시: `$argon2id$v=19$m=65536,t=3,p=4$...`

생성된 해시값을 Authelia `client_secret`에 넣고, **평문 값**은 dify-sso `.env`의 `OIDC_CLIENT_SECRET`에 사용합니다.

> **참고**: Authelia v4.38 이상이 필요합니다 (PKCE S256 지원).

---

## 3단계: dify-sso 환경 설정

### .env 파일 작성

```bash
cp .env.example .env
```

```env
# ── 서비스 설정 ──
CONSOLE_WEB_URL=https://dify.example.com    # [필수] Dify 콘솔 URL
SECRET_KEY=<Dify의 SECRET_KEY와 반드시 동일>   # [필수] JWT 서명 공유
TENANT_ID=<1단계에서 확인한 TENANT_ID>         # [필수] 워크스페이스 ID
EDITION=SELF_HOSTED                          # [선택] 기본값: SELF_HOSTED
ACCOUNT_DEFAULT_ROLE=editor                  # [선택] 신규 사용자 기본 역할 (기본값: normal)

# ── OIDC 설정 (Authelia - 경로 기반) ──
OIDC_CLIENT_ID=dify                          # [필수] Authelia에 등록한 client_id
OIDC_CLIENT_SECRET=<평문 시크릿>               # [필수] 평문 시크릿 (Authelia에는 해시값 입력)
OIDC_DISCOVERY_URL=https://dify.example.com/auth/.well-known/openid-configuration  # [필수]
OIDC_REDIRECT_URI=https://dify.example.com/console/api/enterprise/sso/oidc/callback  # [필수]
OIDC_SCOPE=openid profile email              # [선택] 기본값: openid profile email roles
OIDC_RESPONSE_TYPE=code                      # [선택] 기본값: code

# ── PostgreSQL (Dify와 동일) ──
DB_HOST=<dify-db-host>                       # [필수]
DB_PORT=5432                                 # [선택] 기본값: 5432
DB_DATABASE=dify                             # [선택] 기본값: dify
DB_USERNAME=<dify-db-user>                   # [필수]
DB_PASSWORD=<dify-db-password>               # [필수]

# ── Redis (Dify와 동일) ──
REDIS_HOST=<dify-redis-host>                 # [필수]
REDIS_PORT=6379                              # [선택] 기본값: 6379
REDIS_PASSWORD=<dify-redis-password>         # [선택] 비밀번호 없으면 비워둠
REDIS_DB=0                                   # [선택] 기본값: 0

# ── 토큰 설정 (선택) ──
ACCESS_TOKEN_EXPIRE_MINUTES=900              # [선택] 기본값: 900 (15시간)
REFRESH_TOKEN_EXPIRE_DAYS=30                 # [선택] 기본값: 30
```

> **중요**: `SECRET_KEY`와 DB/Redis는 Dify와 동일해야 합니다. 같은 JWT 토큰과 사용자 DB를 공유합니다.

---

## 4단계: dify-sso 배포

### Docker 이미지 빌드

```bash
# amd64 서버 환경용 (Mac에서 빌드 시 필수)
docker build --platform linux/amd64 -t <레지스트리>/dify-sso:1.0.0 .
docker push <레지스트리>/dify-sso:1.0.0
```

### Docker Compose

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

### Kubernetes (아코디언)

Deployment + Service 생성:

| 항목 | 값 |
|------|-----|
| 이미지 | `<레지스트리>/dify-sso:1.0.0` |
| 포트 | 8000 |
| 헬스체크 | `GET /health` |
| 환경변수 | `.env` 내용을 ConfigMap/Secret으로 |

### 헬스체크 확인

```bash
curl http://localhost:8000/health
# 응답: {"status": "healthy"}

# 상세 확인 (DB, Redis 연결 확인)
curl http://localhost:8000/health?detail=true
# 응답: {"status": "healthy", "database": true, "redis": true}
```

---

## 5단계: 프록시 설정

하나의 도메인(`dify.example.com`)에서 Authelia, dify-sso, Dify를 모두 경로로 분기합니다.

### 방법 A: Nginx Ingress Controller (Kubernetes 환경)

`examples/k8s-ingress-nginx.yaml` 참고. 하나의 Ingress로 모든 라우팅을 처리합니다.

주요 라우팅:

| 경로 | 대상 | 설명 |
|------|------|------|
| `/signin` | 302 → SSO 로그인 | server-snippet으로 리다이렉트 |
| `/admin-login` | dify-sso | 관리자 Dify 기본 로그인 |
| `/console/api/system-features` | dify-sso | SSO 강제 설정 반환 |
| `/console/api/enterprise/sso/*` | dify-sso | SSO 인증 엔드포인트 |
| `/auth/*` | Authelia | OIDC 제공자 |
| `/console/api/*` | Dify API | Dify 본체 |
| `/api/*` | Dify API | Dify 본체 |
| `/triggers/*` | Dify API | 웹훅 트리거 |
| `/*` | Dify Web | Dify 프론트엔드 |

매칭 우선순위: Exact > Regex > Prefix (긴 경로 > 짧은 경로)

```bash
kubectl apply -f examples/k8s-ingress-nginx.yaml -n llm-dev
```

### 방법 B: Nginx 직접 설정 (Docker Compose 환경)

`examples/nginx-sso.conf`의 location 블록을 기존 Dify Nginx 설정에 추가합니다.

```nginx
server {
    listen 443 ssl;
    server_name dify.example.com;

    # ... 기존 SSL 설정 ...

    # ── Authelia (/auth 경로) ──
    location /auth {
        proxy_pass http://authelia:9091;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # ── 로그인 바이패스 → SSO ──
    location = /signin {
        return 302 /console/api/enterprise/sso/oidc/login?is_login=true;
    }

    # ── 관리자 로그인 → Dify 기본 로그인 ──
    location = /admin-login {
        proxy_pass http://dify-sso:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # ── SSO 인증 엔드포인트 → dify-sso ──
    location ~ ^/console/api/enterprise/sso/ {
        proxy_pass http://dify-sso:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # ── 시스템 기능 API → dify-sso ──
    location = /console/api/system-features {
        proxy_pass http://dify-sso:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # ... 나머지 dify-sso 경로 (nginx-sso.conf 참고) ...

    # ── Dify 본체 (아래에 위치) ──
    location /console/api {
        proxy_pass http://dify-api:5001;
        # ...
    }
    location / {
        proxy_pass http://dify-web:3000;
        # ...
    }
}
```

> **중요**: SSO/Authelia location 블록은 기존 Dify의 `/console/api` 블록보다 **위에** 배치해야 합니다.

```bash
nginx -t && nginx -s reload
```

---

## 6단계: 동작 확인

### 1. 서비스 상태 확인

```bash
# Authelia OIDC discovery 확인
curl https://dify.example.com/auth/.well-known/openid-configuration

# dify-sso 헬스체크
curl https://dify.example.com/health?detail=true

# OIDC 설정 로드 확인
curl https://dify.example.com/console/api/system-features
# sso_enforced_for_signin: true 확인
```

### 2. 로그인 흐름 테스트

**일반 사용자 (SSO):**
1. 브라우저에서 `https://dify.example.com` 접속
2. **Dify 로그인 화면이 보이지 않고** Authelia 로그인 화면으로 자동 이동되는지 확인
3. LDAP 계정으로 로그인
4. Dify 콘솔로 자동 리다이렉트되는지 확인

**관리자 (Dify 기본 로그인):**
1. `https://dify.example.com/admin-login` 접속
2. Dify 이메일/비밀번호 로그인 화면이 표시되는지 확인
3. 관리자 이메일/비밀번호로 로그인

### 3. 사용자 생성 확인

```sql
-- Dify DB에서 SSO로 생성된 사용자 확인
SELECT id, name, email, status, last_login_at
FROM accounts
ORDER BY created_at DESC
LIMIT 5;
```

---

## 7단계: Sandbox 설정 (코드 노드 실행 환경)

Dify 워크플로우에서 **코드 노드(Code Node)**를 사용하려면 Sandbox 서비스를 별도로 배포해야 합니다.
Sandbox는 사용자가 작성한 Python/JavaScript 코드를 **격리된 환경**에서 안전하게 실행하는 컨테이너입니다.

> **참고**: dify-api, dify-web만 배포한 상태에서 코드 노드를 실행하면 다음 에러가 발생합니다:
> `Failed to execute code, which is likely a network issue, please check if the sandbox service is running. (Error: [Errno -2] Name or service not known)`

### Sandbox란?

- Go 언어로 작성된 별도 프로젝트 ([dify-sandbox](https://github.com/langgenius/dify-sandbox))
- Seccomp를 사용하여 시스템 콜 수준에서 보안 적용
- CPU, 메모리, 실행 시간 제한으로 안전한 코드 실행 보장
- 기본적으로 외부 네트워크 접근 차단 (설정으로 허용 가능)

### Kubernetes (아코디언) 배포

아코디언 UI에서 새 워크로드를 추가합니다.

**컨테이너 설정:**

| 항목 | 값 |
|------|-----|
| 이미지 | `langgenius/dify-sandbox:latest` |
| 포트 | `8194` |

**환경변수:**

| 이름 | 값 | 설명 |
|------|-----|------|
| `SANDBOX_API_KEY` | `dify-sandbox` | dify-api와 통신용 키 |
| `SANDBOX_GIN_MODE` | `release` | 실행 모드 |
| `SANDBOX_WORKER_TIMEOUT` | `15` | 코드 실행 제한 시간(초) |
| `SANDBOX_ENABLE_NETWORK` | `true` | 외부 네트워크 허용 여부 (아래 참고) |

**`ENABLE_NETWORK` 설정 가이드:**

코드 노드 안에서 `requests.get("https://...")` 같은 외부 API 호출이 필요한 경우 `true`, 단순 계산/데이터 가공만 한다면 `false`로 설정합니다. Dify의 HTTP 요청 노드는 dify-api가 직접 처리하므로 이 설정과 무관합니다.

`true`로 설정하면 코드 노드에서 K8s 내부 서비스(DB, Redis 등)에도 접근할 수 있는 SSRF(Server-Side Request Forgery) 위험이 있습니다. 이를 방지하려면 SSRF Proxy(Squid)를 함께 배포하여 내부 IP 접근을 차단할 수 있습니다.

| 상황 | 권장 설정 |
|------|----------|
| 코드 노드에서 외부 API 호출 안 함 | `ENABLE_NETWORK=false` (SSRF Proxy 불필요) |
| 외부 API 호출 필요 + 내부 사용자만 사용 | `ENABLE_NETWORK=true` (SSRF Proxy 없어도 무방) |
| 외부 API 호출 필요 + 외부 사용자도 접근 | `ENABLE_NETWORK=true` + SSRF Proxy 필수 |

> **현재 환경**: 내부 사용자만 사용하므로 `ENABLE_NETWORK=true`, SSRF Proxy 없이 운영합니다.

**Service 생성:**

| 항목 | 값 |
|------|-----|
| 서비스 이름 | `dify-sandbox` |
| 포트 | `8194 → 8194` |
| 네임스페이스 | dify-api와 **동일한 네임스페이스** |

### dify-api 환경변수 추가

dify-api 파드에 아래 환경변수를 추가하고 재시작합니다.

| 이름 | 값 | 설명 |
|------|-----|------|
| `CODE_EXECUTION_ENDPOINT` | `http://dify-sandbox:8194` | Sandbox 서비스 주소 |
| `CODE_EXECUTION_API_KEY` | `dify-sandbox` | Sandbox의 `API_KEY`와 동일 |

> **주의**: dify-api와 dify-sandbox가 다른 네임스페이스에 있다면 `http://dify-sandbox.<네임스페이스>.svc.cluster.local:8194` 형태로 지정해야 합니다.

### 동작 확인

Dify 웹 UI → 워크플로우 → 코드 노드 추가 → 아래 코드 실행:

```python
def main() -> dict:
    return {"result": "sandbox works!"}
```

정상 실행되면 Sandbox 연결이 완료된 것입니다.

### 외부 Python 패키지 추가 (선택)

코드 노드에서 추가 라이브러리가 필요한 경우, 커스텀 이미지를 빌드합니다:

```dockerfile
FROM langgenius/dify-sandbox:latest
COPY python-requirements.txt /dependencies/python-requirements.txt
```

```bash
docker build --platform linux/amd64 -t <레지스트리>/dify-sandbox:custom .
docker push <레지스트리>/dify-sandbox:custom
```

### Docker Compose 배포 (참고)

```yaml
sandbox:
  image: langgenius/dify-sandbox:latest
  restart: always
  environment:
    API_KEY: dify-sandbox
    GIN_MODE: release
    WORKER_TIMEOUT: 15
    ENABLE_NETWORK: "true"
  ports:
    - "8194:8194"
  networks:
    - dify-network
```

---

## 문제 해결 (Troubleshooting)

### "Failed to load OIDC configuration"

- `OIDC_DISCOVERY_URL`이 올바른지 확인 (`https://dify.example.com/auth/.well-known/openid-configuration`)
- dify-sso 컨테이너에서 Authelia에 네트워크 접근 가능한지 확인

```bash
# 컨테이너 안에서 테스트
docker exec dify-sso curl -s https://dify.example.com/auth/.well-known/openid-configuration
```

> **주의**: dify-sso가 `dify.example.com` 도메인으로 Authelia에 접근하려면, 컨테이너 내부에서 해당 도메인이 resolve 가능해야 합니다. Docker 내부 네트워크에서는 `http://authelia:9091/auth/.well-known/openid-configuration`으로 직접 접근하는 것도 방법입니다. 이 경우 `.env`의 `OIDC_DISCOVERY_URL`을 내부 주소로 설정하세요.

### "Invalid or expired OAuth state"

- dify-sso와 Dify가 같은 Redis를 사용하는지 확인
- 인증 요청 후 5분 이내에 콜백이 오는지 확인 (state TTL: 5분)

### "Authentication failed" (콜백 에러)

- Authelia의 `redirect_uris`에 콜백 URL이 정확히 등록되어 있는지 확인
- `OIDC_CLIENT_SECRET`이 평문값인지 확인 (해시값이 아님)
- Authelia 로그에서 상세 에러 확인: `docker logs authelia`

### 로그인 화면이 여전히 보이는 경우

- Nginx 설정에서 `location = /signin` 블록이 Dify 프록시 블록보다 위에 있는지 확인
- Nginx 캐시 정리: `nginx -s reload`
- 브라우저 캐시/쿠키 삭제 후 재시도

### Authelia 페이지가 404인 경우

- Authelia `configuration.yml`에 `server.path: 'auth'`가 설정되어 있는지 확인
- Nginx의 `location /auth` 블록이 있는지 확인
- `curl https://dify.example.com/auth/` 로 접근 테스트

### 사용자가 생성되지 않는 경우

- dify-sso 로그 확인: `docker logs dify-sso`
- `TENANT_ID`가 올바른지 확인
- `SECRET_KEY`가 Dify와 동일한지 확인

### exec format error (컨테이너 시작 실패)

- 빌드 시 플랫폼 지정: `docker build --platform linux/amd64 -t dify-sso:1.0.0 .`
- Mac(arm64)에서 빌드한 이미지를 amd64 서버에서 실행하면 발생

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
| **Owner 역할 보호** | SSO 로그인 시 owner 역할은 변경되지 않음 (강등 방지) |
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
| `dataset_operator` | Dataset Operator | 데이터셋 편집 전용 |
| (없음) | `ACCOUNT_DEFAULT_ROLE` | .env에서 설정한 기본 역할 |

Authelia에서 roles claim을 설정하지 않으면 `.env`의 `ACCOUNT_DEFAULT_ROLE` (기본값: `normal`)이 적용됩니다.

> **참고**: Dify의 `owner` 역할은 SSO 로그인으로 변경되지 않습니다. 기존 owner 계정이 SSO로 로그인해도 owner 역할이 유지됩니다.

---

## 관리자 로그인

관리자는 두 가지 방법으로 로그인할 수 있습니다:

1. **SSO 로그인**: LDAP 계정의 `mail` 속성이 Dify 관리자 이메일과 동일하면 기존 관리자 계정으로 로그인됨
2. **Dify 기본 로그인**: `https://dify.example.com/admin-login` 접속 → 이메일/비밀번호로 로그인

`/admin-login` 접속 시 5분간 유효한 쿠키가 설정되며, 이 쿠키가 있는 동안 Dify 기본 로그인 화면이 표시됩니다. 로그인 완료 후에는 Dify JWT 토큰으로 세션이 유지되므로 쿠키 만료와 무관합니다.

---

## 로그아웃

Dify의 기본 로그아웃은 Dify 토큰만 삭제하고 `/signin`으로 리다이렉트합니다. SSO 환경에서는 Authelia 세션이 살아있기 때문에 **자동으로 다시 로그인**되어 로그아웃이 안 되는 것처럼 보입니다.

이 문제를 해결하기 위해 dify-sso가 `/console/api/logout`을 가로채서 **Dify 쿠키 삭제 + Authelia 로그아웃**을 함께 처리합니다.

### 로그아웃 흐름

```
사용자 로그아웃 클릭 → dify-sso /console/api/logout
    → Dify 인증 쿠키 삭제 (access_token, refresh_token, csrf_token)
    → Authelia /auth/#/logout 으로 리다이렉트
    → Authelia 세션 종료
    → 로그인 화면 표시
```

### Ingress 설정

`/console/api/logout`이 dify-api가 아닌 **dify-sso로 라우팅**되어야 합니다. 기존 `/console/api/*` (dify-api) 보다 **위에** 배치합니다.

```yaml
# dify-sso 경로에 추가
- path: /console/api/logout
  pathType: Exact
  backend:
    service:
      name: dify-sso-dev
      port:
        number: 8000
```

> **참고**: Authelia는 OIDC RP-Initiated Logout을 아직 지원하지 않아서, Authelia 자체 로그아웃 페이지(`/auth/#/logout`)를 사용합니다.

---

## WebApp 접근 제어

Dify 콘솔에서 앱 게시 시 접근 권한을 설정할 수 있습니다. 이 기능은 Dify 엔터프라이즈 전용이지만, dify-sso가 동일한 API를 제공하여 커뮤니티 에디션에서도 동작합니다.

### 접근 모드

| 모드 | 설명 |
|------|------|
| `public` | 누구나 접근 가능 (기본값) |
| `sso_verified` | SSO 인증된 사용자만 접근 가능 |
| `private_all` | SSO 인증 + 특정 사용자만 접근 가능 |

### Ingress 설정 (필수)

Dify WebApp 프론트엔드는 `/api/webapp/*` 경로로 접근 권한을 확인합니다. 이 경로가 dify-api가 아닌 **dify-sso로 라우팅**되어야 실제 권한 체크가 동작합니다.

기존 `/api/*` (dify-api) 보다 **위에** 배치합니다:

```yaml
# WebApp 접근 제어 → dify-sso (dify-api의 /api/* 보다 위에)
- path: /api/webapp/*
  pathType: ImplementationSpecific
  backend:
    service:
      name: dify-sso-dev
      port:
        number: 8000
```

> **주의**: 이 설정이 없으면 요청이 dify-api로 가서 항상 `public`으로 응답하기 때문에, 콘솔에서 접근 제한을 설정해도 누구나 앱에 접속할 수 있습니다.

### 동작 흐름

```
사용자가 WebApp 접속 (/workflow/xxxx)
    → 프론트엔드가 /api/webapp/access-mode 호출
    → dify-sso가 Redis에서 설정값 반환 (public / sso_verified / private_all)
    → 프론트엔드가 /api/webapp/permission 호출
    → dify-sso가 사용자 인증 상태 + 허용 목록 체크 후 허용/거부
```

### 조직(그룹) 기반 접근 제어

`private_all` 모드에서 개별 사용자뿐 아니라 **조직 단위**(회사/본부/부문/팀)로 접근 권한을 설정할 수 있습니다.

#### 조직 체계

```
회사 (org_level=1)
├── 본부 (org_level=2)
│   ├── 부문 (org_level=3)
│   │   ├── 팀 (org_level=4) ← 사용자 이름에서 추출: "홍길동(개발팀)"
│   │   └── 팀
│   └── 부문
└── 본부
```

**어떤 레벨이든 혼합하여** 권한 설정이 가능합니다. 예를 들어 "A본부"에 권한을 부여하면 A본부 하위의 모든 부문/팀 사용자가 접근할 수 있습니다.

#### organizations 테이블 설정

Dify PostgreSQL에 `organizations` 테이블이 필요합니다. 이미 다른 서버에 동일 테이블이 있다면 `pg_dump`로 마이그레이션합니다:

```bash
# 원본 서버에서 데이터 export
pg_dump -h <원본_DB_HOST> -U <원본_USER> -d <원본_DB> \
  -t organizations --no-owner --no-privileges \
  -f org_dump.sql

# Dify DB에 import (테이블 구조 + 데이터 한번에)
psql -h <DIFY_DB_HOST> -U <DIFY_USER> -d dify -f org_dump.sql
```

테이블이 없는 경우 `examples/create_organizations_table.sql`로 직접 생성할 수도 있습니다.

테이블 구조:

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `id` | TEXT (PK) | 조직 ID |
| `org_name` | TEXT | 조직명 |
| `org_level` | INTEGER | 1: 회사, 2: 본부, 3: 부문, 4: 팀 |
| `company_name` | TEXT | 소속 회사명 |
| `division_name` | TEXT | 소속 본부명 |
| `department_name` | TEXT | 소속 부문명 |
| `team_name` | TEXT | 소속 팀명 |

#### 권한 체크 흐름

```
콘솔에서 앱 접근 제한 설정 → 그룹 검색 시 조직 목록 표시 (회사/본부/부문/팀)
    → "A본부" 선택 → Redis에 저장

사용자 "홍길동(개발팀)" WebApp 접속
    → 이름에서 "개발팀" 추출
    → organizations 테이블에서 조직 체인 조회: [개발팀, A-1부문, A본부, 우리회사]
    → 앱에 허용된 "A본부"가 체인에 포함 → 접근 허용
```

#### 조직 데이터 업데이트

조직 변경 시 원본 서버에서 다시 dump 받아 import하거나, Dify DB에서 직접 INSERT/UPDATE합니다:

```bash
# 원본에서 재동기화
pg_dump -h <원본_DB_HOST> -U <원본_USER> -d <원본_DB> \
  -t organizations --data-only --no-owner --no-privileges \
  -f org_data.sql

# Dify DB에서 기존 데이터 삭제 후 재입력
psql -h <DIFY_DB_HOST> -U <DIFY_USER> -d dify \
  -c "TRUNCATE organizations;" -f org_data.sql
```

### WebApp SSO 로그인

WebApp SSO는 콘솔 SSO와 동일한 Authelia 인증을 사용하며, 별도의 설정은 필요하지 않습니다.
