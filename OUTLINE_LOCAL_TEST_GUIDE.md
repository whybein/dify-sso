# Outline 로컬 테스트 가이드

회사 Authelia를 그대로 사용해서 **Outline(위키)** 을 로컬에서 띄워보는 가이드.
정식 배포가 아니라 **OIDC 흐름과 동작 검증용**이다.

- 정식 K8s 배포: `OUTLINE_DEPLOYMENT.md` 참고

---

## 1. 사전 준비

| 항목 | 비고 |
|------|------|
| Docker Desktop (compose v2 포함) | |
| 빈 포트 `3000` | Outline |
| Authelia `configuration.yml` 수정 권한 | Accordion에서 ConfigMap 편집 |

---

## 2. Authelia에 Outline OIDC client 추가

### 2.1 client_secret 생성

```bash
# 평문 시크릿 생성
openssl rand -hex 24

# 평문을 Authelia hash로 변환
authelia crypto hash generate pbkdf2 --variant sha512 --password '<위에서 만든 평문>'
```

- **평문**: `.env.outline`의 `OUTLINE_OIDC_CLIENT_SECRET`에 사용
- **`$pbkdf2-sha512$...` hash**: Authelia `configuration.yml`의 `client_secret`에 사용

### 2.2 `llm-dev/authelia-config` ConfigMap에 client 추가

`identity_providers.oidc.clients` 리스트에 이어붙임. 기존 dify client는 건드리지 않는다.

```yaml
- client_id: outline
  client_name: Outline Wiki
  client_secret: '$pbkdf2-sha512$...'
  public: false
  authorization_policy: one_factor
  redirect_uris:
    - http://localhost:3000/auth/oidc.callback
  scopes:
    - openid
    - profile
    - email
    - offline_access
  userinfo_signed_response_alg: none
  token_endpoint_auth_method: client_secret_post
```

> `redirect_uris`의 마지막이 `/auth/oidc.callback` (점 주의).

### 2.3 Authelia Pod 재시작

Accordion → `llm-dev/authelia-dev` → Restart

---

## 3. 로컬 디렉토리 준비

```bash
mkdir -p ~/Dev/collab-tools-test
cd ~/Dev/collab-tools-test
```

---

## 4. 설정 파일 작성

### 4.1 `.env.outline`

```bash
# 회사 Authelia URL
AUTHELIA_ISSUER_URL=https://dify.oilbank.co.kr/auth

# Outline
OUTLINE_URL=http://localhost:3000
OUTLINE_SECRET_KEY=        # openssl rand -hex 32
OUTLINE_UTILS_SECRET=      # openssl rand -hex 32

# Outline OIDC
OUTLINE_OIDC_CLIENT_ID=outline
OUTLINE_OIDC_CLIENT_SECRET=   # Authelia에 등록한 평문

# Postgres (테스트용 컨테이너)
POSTGRES_USER=outline
POSTGRES_PASSWORD=         # openssl rand -hex 16
POSTGRES_DB=outline
```

값 한번에 채우기:
```bash
echo "OUTLINE_SECRET_KEY=$(openssl rand -hex 32)"
echo "OUTLINE_UTILS_SECRET=$(openssl rand -hex 32)"
echo "POSTGRES_PASSWORD=$(openssl rand -hex 16)"
```

### 4.2 `docker-compose.outline.yml`

```yaml
services:
  outline:
    image: outlinewiki/outline:latest
    depends_on: [outline-postgres, outline-redis]
    ports:
      - "3000:3000"
    environment:
      NODE_ENV: production
      SECRET_KEY: ${OUTLINE_SECRET_KEY}
      UTILS_SECRET: ${OUTLINE_UTILS_SECRET}
      DATABASE_URL: postgres://${POSTGRES_USER}:${POSTGRES_PASSWORD}@outline-postgres:5432/${POSTGRES_DB}
      PGSSLMODE: disable
      REDIS_URL: redis://outline-redis:6379
      URL: ${OUTLINE_URL}
      PORT: 3000
      FORCE_HTTPS: "false"
      ENABLE_UPDATES: "false"
      FILE_STORAGE: local
      FILE_STORAGE_LOCAL_ROOT_DIR: /var/lib/outline/data
      FILE_STORAGE_UPLOAD_MAX_SIZE: "262144000"

      # OIDC
      OIDC_CLIENT_ID: ${OUTLINE_OIDC_CLIENT_ID}
      OIDC_CLIENT_SECRET: ${OUTLINE_OIDC_CLIENT_SECRET}
      OIDC_AUTH_URI: ${AUTHELIA_ISSUER_URL}/api/oidc/authorization
      OIDC_TOKEN_URI: ${AUTHELIA_ISSUER_URL}/api/oidc/token
      OIDC_USERINFO_URI: ${AUTHELIA_ISSUER_URL}/api/oidc/userinfo
      OIDC_LOGOUT_URI: ${AUTHELIA_ISSUER_URL}/logout
      OIDC_USERNAME_CLAIM: preferred_username
      OIDC_DISPLAY_NAME: Authelia
      OIDC_SCOPES: "openid profile email offline_access"
    volumes:
      - outline-data:/var/lib/outline/data

  outline-postgres:
    image: postgres:15-alpine
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
    volumes:
      - outline-pg:/var/lib/postgresql/data

  outline-redis:
    image: redis:7-alpine
    volumes:
      - outline-redis:/data

volumes:
  outline-data:
  outline-pg:
  outline-redis:
```

---

## 5. 실행

```bash
docker compose --env-file .env.outline -f docker-compose.outline.yml up -d
docker compose -f docker-compose.outline.yml logs -f outline
```

`Listening on http://localhost:3000` 로그 나오면 준비 완료.

브라우저에서 `http://localhost:3000` → "Continue with Authelia" → LDAP 로그인.

---

## 6. 동작 확인 체크리스트

- [ ] `http://localhost:3000` 접속 → "Continue with Authelia" 버튼 보임
- [ ] 클릭 시 Authelia 로그인 화면으로 리다이렉트
- [ ] LDAP 계정으로 로그인 → Outline으로 돌아와 워크스페이스 자동 생성
- [ ] 새 문서 만들고 저장 → 새로고침 후에도 유지
- [ ] 이미지 업로드 후 표시 확인

---

## 7. 자주 막히는 곳

| 증상 | 원인/해결 |
|------|-----------|
| `client not found` | configuration.yml client_id 오타 또는 Authelia 미재시작 |
| `redirect_uri mismatch` | `http://localhost:3000/auth/oidc.callback` — 마지막 `.callback` 점 주의 |
| OIDC 버튼이 안 보임 | `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `OIDC_AUTH_URI`, `OIDC_TOKEN_URI`, `OIDC_USERINFO_URI` 5개 모두 채워야 활성화 |
| `URL mismatch` | `URL` env가 브라우저 주소와 정확히 같아야 함. `localhost`로 띄웠으면 `127.0.0.1`로 접속 금지 |
| Postgres 연결 실패 | `PGSSLMODE=disable` 필수 (테스트 컨테이너용) |
| `code_challenge missing` | Authelia client에 `require_pkce: true`가 있으면 제거 (Outline은 PKCE 미지원) |

---

## 8. 정리

```bash
docker compose --env-file .env.outline -f docker-compose.outline.yml down -v
```

`-v`는 볼륨까지 삭제. 데이터 보존 시 `-v` 제외.

---

## 9. 다음 단계 (테스트 통과 후)

- Authelia `redirect_uris`에 운영 도메인 추가
- RDS에 `outline` database 생성 → 컨테이너 Postgres 제거
- 회사 Redis 재사용 → 컨테이너 Redis 제거
- S3 + IRSA 설정 (`S3_IRSA_REQUEST.md` 참고)
- Accordion으로 K8s 배포 (`OUTLINE_DEPLOYMENT.md` 참고)
