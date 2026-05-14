# ExcaliDash 로컬 테스트 가이드

회사 Authelia를 그대로 사용해서 **ExcaliDash(드로잉)** 를 로컬에서 띄워보는 가이드.
정식 배포가 아니라 **OIDC 흐름과 동작 검증용**이다.

---

## 1. 사전 준비

| 항목 | 비고 |
|------|------|
| Docker Desktop (compose v2 포함) | |
| 빈 포트 `6767` | ExcaliDash |
| Authelia `configuration.yml` 수정 권한 | Accordion에서 ConfigMap 편집 |
| 본인 LDAP 그룹명 | Admin 매핑용 (선택) |

---

## 2. Authelia에 ExcaliDash OIDC client 추가

### 2.1 client_secret 생성

```bash
# 평문 시크릿 생성
openssl rand -hex 24

# 평문을 Authelia hash로 변환
authelia crypto hash generate pbkdf2 --variant sha512 --password '<위에서 만든 평문>'
```

- **평문**: `.env.excalidash`의 `EXCALIDASH_OIDC_CLIENT_SECRET`에 사용
- **`$pbkdf2-sha512$...` hash**: Authelia `configuration.yml`의 `client_secret`에 사용

### 2.2 `llm-dev/authelia-config` ConfigMap에 client 추가

`identity_providers.oidc.clients` 리스트에 이어붙임. 기존 dify/outline client는 건드리지 않는다.

```yaml
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

> `groups` scope가 필요하다. Authelia 4.38+에서 `groups` claim을 발급하려면 `claims_policies` 설정이 추가로 필요할 수 있음.

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

### 4.1 공식 compose 파일 받기

```bash
curl -OL https://raw.githubusercontent.com/ZimengXiong/ExcaliDash/main/docker-compose.prod.yml
```

### 4.2 `.env.excalidash`

```bash
# 회사 Authelia URL
AUTHELIA_ISSUER_URL=https://dify.oilbank.co.kr/auth

# ExcaliDash
EXCALIDASH_URL=http://localhost:6767
EXCALIDASH_JWT_SECRET=            # openssl rand -hex 32

# ExcaliDash OIDC
EXCALIDASH_OIDC_CLIENT_ID=excalidash
EXCALIDASH_OIDC_CLIENT_SECRET=    # Authelia에 등록한 평문
EXCALIDASH_ADMIN_GROUPS=          # 선택. LDAP 그룹명 (예: dify-admins)
```

### 4.3 `docker-compose.prod.yml`의 backend에 env 추가

받아온 파일 `backend:` 서비스의 `environment:` 아래에 다음을 **추가** (기존 항목은 두고):

```yaml
    environment:
      # ... 기존 항목 그대로 ...
      JWT_SECRET: ${EXCALIDASH_JWT_SECRET}
      FRONTEND_URL: ${EXCALIDASH_URL}
      TRUST_PROXY: "false"

      AUTH_MODE: hybrid                    # 로컬 admin + OIDC 병행 (테스트 권장)
      OIDC_PROVIDER_NAME: Authelia
      OIDC_ISSUER_URL: ${AUTHELIA_ISSUER_URL}
      OIDC_CLIENT_ID: ${EXCALIDASH_OIDC_CLIENT_ID}
      OIDC_CLIENT_SECRET: ${EXCALIDASH_OIDC_CLIENT_SECRET}
      OIDC_REDIRECT_URI: ${EXCALIDASH_URL}/api/auth/oidc/callback
      OIDC_SCOPES: "openid profile email groups"
      OIDC_GROUPS_CLAIM: groups
      OIDC_ADMIN_GROUPS: ${EXCALIDASH_ADMIN_GROUPS}
```

> `AUTH_MODE: oidc_enforced`로 하면 OIDC 전용. 테스트 초기엔 `hybrid` 추천.

---

## 5. 실행

```bash
docker compose --env-file .env.excalidash -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml logs -f backend
```

---

## 6. 첫 admin 부트스트랩 (`AUTH_MODE: hybrid` 시)

사용자가 0명이면 backend 로그에 1회용 부트스트랩 코드가 출력된다:

```bash
docker compose -f docker-compose.prod.yml logs backend --tail=200 | grep "BOOTSTRAP SETUP"
```

→ `http://localhost:6767` 접속 → 안내대로 로컬 admin 계정 생성 → 이후 OIDC 로그인 가능.

`AUTH_MODE=oidc_enforced`이면 부트스트랩 없이 바로 OIDC 로그인.

---

## 7. 동작 확인 체크리스트

- [ ] `http://localhost:6767` 접속 → OIDC 로그인 버튼 보임
- [ ] 클릭 시 Authelia 로그인 화면으로 리다이렉트
- [ ] LDAP 계정으로 로그인 → 대시보드 진입
- [ ] admin 그룹 매핑 시 Admin 메뉴 노출 확인
- [ ] 새 그림 만들고 저장 → 재로그인 후에도 유지
- [ ] 공유 링크로 동시 편집 확인 (선택)

---

## 8. 자주 막히는 곳

| 증상 | 원인/해결 |
|------|-----------|
| `client not found` | configuration.yml client_id 오타 또는 Authelia 미재시작 |
| `redirect_uri mismatch` | `http://localhost:6767/api/auth/oidc/callback` 정확히 일치해야 함 |
| 로그인 후 "Forbidden" | `OIDC_GROUPS_CLAIM`과 Authelia가 발급하는 claim 이름 불일치 |
| Authelia가 `groups` claim 안 보냄 | Authelia 4.38+에서 `claims_policies`로 groups 발급 설정 필요 |
| 부트스트랩 코드가 안 찍힘 | 이미 사용자가 있거나 `AUTH_MODE=oidc_enforced`인 경우 |

---

## 9. 정리

```bash
docker compose --env-file .env.excalidash -f docker-compose.prod.yml down -v
```

`-v`는 볼륨까지 삭제. 데이터 보존 시 `-v` 제외.

---

## 10. 다음 단계 (테스트 통과 후)

- Authelia `redirect_uris`에 운영 도메인 추가
- ExcaliDash는 SQLite 그대로 사용, PVC만 붙이면 됨
- `AUTH_MODE` 운영 환경에 맞게 결정 (`hybrid` 또는 `oidc_enforced`)
- Accordion으로 K8s 배포
