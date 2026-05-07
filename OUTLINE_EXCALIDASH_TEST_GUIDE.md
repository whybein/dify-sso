# Outline + ExcaliDash 로컬 테스트 가이드

기존 회사 Authelia를 그대로 사용해서 **Outline(위키)** 과 **ExcaliDash(드로잉)** 를 로컬에서 띄워보는 가이드.
정식 배포가 아니라 **OIDC 흐름과 동작 검증용**이다. 운영 결정은 동작 확인 후.

---

## 0. 결론 요약

- 둘 다 **공식 Docker 이미지** 그대로 사용 (커스텀 빌드 불필요)
- Authelia에 OIDC client 2개만 추가하면 끝
- **본인 Mac에서 docker-compose로 먼저 테스트** → 동작 확인 후 → 회사 K8s에 정식 배포
  - `docker compose up -d` 명령 한 번에 Outline/Postgres/Redis 컨테이너가 같이 떠서 검증이 빠름
- 테스트 단계에서는 DB/Redis/스토리지도 컨테이너로 같이 띄움 (RDS·회사 Redis는 K8s 배포 단계에서 연결)

---

## 1. 사전 준비

### 1.1 회사 Authelia 정보 확보

| 항목 | 예시 | 메모 |
|------|------|------|
| Authelia 공개 URL | `https://auth.example.com` | dify가 쓰는 그 주소 |
| `configuration.yml` 수정 권한 | 직접/요청 | K8s configmap이면 Accordion으로 수정 |
| 본인 LDAP 그룹명 | `dify-admins` 같은 거 | ExcaliDash admin 매핑용 (선택) |

### 1.2 로컬 환경

- Docker Desktop (compose v2 포함)
- 빈 포트: `3000`(Outline), `6767`(ExcaliDash)

---

## 2. Authelia에 OIDC client 2개 추가

### 2.1 client_secret 생성 (Authelia 호스트에서)

```bash
# 평문 시크릿 2개 생성해두고 각각 메모
openssl rand -hex 24      # outline용
openssl rand -hex 24      # excalidash용

# 각 평문을 hash로 변환 (Authelia에 등록할 값)
authelia crypto hash generate pbkdf2 --variant sha512 --password '<위에서 만든 평문>'
```

- **평문**: `.env`의 `*_OIDC_CLIENT_SECRET`에 넣음
- **`$pbkdf2-sha512$...` hash**: Authelia configuration.yml의 `client_secret`에 넣음

### 2.2 configuration.yml에 추가

기존 dify client가 들어 있는 `identity_providers.oidc.clients` 리스트에 **이어붙임**. dify 항목은 건드리지 않는다.

```yaml
identity_providers:
  oidc:
    # ... 기존 hmac_secret, issuer_private_key 그대로 ...
    clients:
      - client_id: dify          # 기존 - 건드리지 말 것
        # ... 기존 설정 ...

      # ===== Outline (추가) =====
      - client_id: outline
        client_name: Outline Wiki
        client_secret: '$pbkdf2-sha512$...'   # 위에서 만든 hash
        public: false
        authorization_policy: one_factor       # 테스트라 1단계, 운영 시 two_factor
        redirect_uris:
          - http://localhost:3000/auth/oidc.callback
        scopes:
          - openid
          - profile
          - email
          - offline_access
        userinfo_signed_response_alg: none
        token_endpoint_auth_method: client_secret_post

      # ===== ExcaliDash (추가) =====
      - client_id: excalidash
        client_name: ExcaliDash
        client_secret: '$pbkdf2-sha512$...'
        public: false
        authorization_policy: one_factor
        redirect_uris:
          - http://localhost:6767/api/auth/oidc/callback
        scopes:
          - openid
          - profile
          - email
          - groups
        userinfo_signed_response_alg: none
        token_endpoint_auth_method: client_secret_post
```

### 2.3 Authelia 재시작/리로드

K8s + Accordion이면 Pod 재시작. 호스트 직접 운영이면 `systemctl restart authelia` 또는 컨테이너 재시작.

---

## 3. 로컬 디렉토리 준비

작업 위치 추천: `~/Dev/collab-tools-test/` (dify 소스와 분리)

```bash
mkdir -p ~/Dev/collab-tools-test
cd ~/Dev/collab-tools-test
```

이 가이드는 두 서비스를 **별도 compose 파일**로 띄운다 (ExcaliDash는 공식 prod compose를 그대로 쓰는 게 안전).

---

## 4. Outline 띄우기

### 4.1 `.env.outline`

```bash
# Authelia
AUTHELIA_ISSUER_URL=https://auth.example.com   # 회사 값으로 교체

# Outline 본체
OUTLINE_URL=http://localhost:3000
OUTLINE_SECRET_KEY=                            # openssl rand -hex 32
OUTLINE_UTILS_SECRET=                          # openssl rand -hex 32

# Outline OIDC
OUTLINE_OIDC_CLIENT_ID=outline
OUTLINE_OIDC_CLIENT_SECRET=                    # Authelia에 등록한 평문

# Postgres (테스트용 컨테이너)
POSTGRES_USER=outline
POSTGRES_PASSWORD=                             # openssl rand -hex 16
POSTGRES_DB=outline
```

값 채우기:
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

      # OIDC (Authelia)
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

### 4.3 실행

```bash
docker compose --env-file .env.outline -f docker-compose.outline.yml up -d
docker compose -f docker-compose.outline.yml logs -f outline
```

→ 브라우저에서 `http://localhost:3000` → "Continue with Authelia" 버튼 → 로그인.

---

## 5. ExcaliDash 띄우기

공식 README가 `docker-compose.prod.yml` 사용을 권장하므로 그대로 받아 OIDC env만 추가하는 방식.

### 5.1 prod compose 파일 받기

```bash
curl -OL https://raw.githubusercontent.com/ZimengXiong/ExcaliDash/main/docker-compose.prod.yml
```

### 5.2 `.env.excalidash`

```bash
AUTHELIA_ISSUER_URL=https://auth.example.com

EXCALIDASH_URL=http://localhost:6767
EXCALIDASH_JWT_SECRET=                         # openssl rand -hex 32

EXCALIDASH_OIDC_CLIENT_ID=excalidash
EXCALIDASH_OIDC_CLIENT_SECRET=                 # Authelia에 등록한 평문
EXCALIDASH_ADMIN_GROUPS=                       # 선택. 본인 LDAP 그룹명 (예: dify-admins)
```

### 5.3 `docker-compose.prod.yml`의 backend 서비스에 env 추가

받아온 파일을 열어 `backend:` 서비스의 `environment:` 아래에 다음을 **추가**한다 (기존 항목은 두고).

```yaml
    environment:
      # ... 기존 항목 그대로 ...
      JWT_SECRET: ${EXCALIDASH_JWT_SECRET}
      FRONTEND_URL: ${EXCALIDASH_URL}
      TRUST_PROXY: "false"

      AUTH_MODE: hybrid                          # 테스트는 hybrid 추천 (로컬 admin + OIDC 병행)
      OIDC_PROVIDER_NAME: Authelia
      OIDC_ISSUER_URL: ${AUTHELIA_ISSUER_URL}
      OIDC_CLIENT_ID: ${EXCALIDASH_OIDC_CLIENT_ID}
      OIDC_CLIENT_SECRET: ${EXCALIDASH_OIDC_CLIENT_SECRET}
      OIDC_REDIRECT_URI: ${EXCALIDASH_URL}/api/auth/oidc/callback
      OIDC_SCOPES: "openid profile email groups"
      OIDC_GROUPS_CLAIM: groups
      OIDC_ADMIN_GROUPS: ${EXCALIDASH_ADMIN_GROUPS}
```

### 5.4 실행

```bash
docker compose --env-file .env.excalidash -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml logs -f backend
```

### 5.5 첫 admin 부트스트랩 (로컬 계정 1개 만들 때만 필요)

`AUTH_MODE=hybrid`로 띄웠고 사용자가 0명이면 backend 로그에 1회용 부트스트랩 코드가 찍힌다:

```bash
docker compose -f docker-compose.prod.yml logs backend --tail=200 | grep "BOOTSTRAP SETUP"
```

→ 브라우저에서 `http://localhost:6767` → 안내대로 admin 계정 생성 → 그 다음부터 OIDC 로그인 가능.
OIDC만 쓸 거면 `AUTH_MODE=oidc_enforced`로 두고 부트스트랩 단계 건너뛰어도 됨.

---

## 6. 동작 확인 체크리스트

### Outline
- [ ] `http://localhost:3000` 접속 → "Continue with Authelia" 버튼 보임
- [ ] 클릭 시 Authelia 로그인 화면으로 리다이렉트
- [ ] LDAP 계정으로 로그인 → Outline으로 돌아와 워크스페이스 자동 생성
- [ ] 새 문서 만들고 저장 → 새로고침 후에도 유지

### ExcaliDash
- [ ] `http://localhost:6767` 접속 → OIDC 로그인 버튼 보임
- [ ] Authelia 로그인 → 대시보드 진입
- [ ] (admin 그룹 매핑 시) Admin 메뉴 노출 확인
- [ ] 새 그림 만들고 저장 → 다른 브라우저에서 로그인해 공유 링크로 동시 편집 확인 (선택)

---

## 7. 자주 막히는 곳

| 증상 | 원인/해결 |
|------|-----------|
| Authelia에서 `client not found` | configuration.yml에 client_id 오타 또는 Authelia 미재시작 |
| `redirect_uri mismatch` | redirect_uris에 포트/경로까지 정확히 일치해야 함. `http://localhost:3000/auth/oidc.callback` (Outline)은 마지막 `.callback` 점에 주의 |
| Outline에서 OIDC 버튼이 안 보임 | OIDC_CLIENT_ID/SECRET/AUTH_URI/TOKEN_URI/USERINFO_URI **5개 모두** 채워야 활성화 |
| ExcaliDash 로그인 후 "Forbidden" | `OIDC_GROUPS_CLAIM`과 Authelia가 발급하는 claim 이름 불일치. Authelia 쪽 `claims_policy`로 `groups` 발급되는지 확인 |
| Authelia가 `groups` claim을 안 보냄 | Authelia 4.38+에서 `claims_policies` 설정 필요. dify는 groups를 안 써서 그냥 둔 경우 많음 |
| Outline에서 `URL mismatch` | `URL` env가 브라우저 주소와 정확히 같아야 함. `localhost`로 띄웠으면 `127.0.0.1`로 접속하면 깨짐 |
| Postgres 연결 실패 | `PGSSLMODE=disable` 잊지 말 것 (테스트 컨테이너용) |

---

## 8. 정리/제거

```bash
# Outline
docker compose --env-file .env.outline -f docker-compose.outline.yml down -v

# ExcaliDash
docker compose --env-file .env.excalidash -f docker-compose.prod.yml down -v
```

`-v`는 볼륨까지 지움. 데이터 보존하려면 `-v` 빼고 `down`만.

---

## 9. 다음 단계 (테스트 통과 후)

- 호스트명 합의 (`wiki.사내도메인`, `draw.사내도메인`)
- 네임스페이스 결정 (추천: `collab-tools` 1개에 두 앱 같이)
- RDS에 `outline` database 생성 → 컨테이너 Postgres 제거
- 회사 Redis 재사용 → 컨테이너 Redis 제거
- ExcaliDash는 SQLite 그대로 (PVC만 붙이면 됨)
- S3 호환 스토리지로 Outline `FILE_STORAGE` 전환 (RAG 첨부 인덱싱 대비)
- Authelia redirect_uris에 운영 도메인 추가
- Accordion으로 K8s manifest 작성

RAG 연계는 별도 검토. 위키 본문은 Outline API로 뽑아 Weaviate에 임베딩하는 흐름이 자연스러움 (회사에 Weaviate 이미 있음).
