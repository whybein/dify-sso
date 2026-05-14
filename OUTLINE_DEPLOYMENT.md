# Outline 첫 배포 가이드 (dev)

회사 K8s(EKS)에 Outline을 처음 배포하기 위한 단계별 가이드.

- 운영 후 일상 작업: `OUTLINE_OPERATIONS_GUIDE.md` 참고
- S3 + IRSA 상세: `S3_IRSA_REQUEST.md` 참고
- 로컬 사전 검증: `OUTLINE_EXCALIDASH_TEST_GUIDE.md` 참고

---

## 1. 배포 순서 (전체)

```
1. RDS database 생성 (CREATE DATABASE outline)
   ↓
2. S3 셋업 (버킷 + IAM Role + CORS)  — S3_IRSA_REQUEST.md
   ↓
3. Authelia OIDC client 등록  — 본 문서 §4
   ↓
4. Route53 alias 추가 — docsdev.oilbank.co.kr → ALB
   ↓
5. Accordion에서 Workload 생성 (이미지 + 환경변수 + 포트)
   ↓
6. ServiceAccount(default)에 IRSA 어노테이션 추가
   ↓
7. Pod 재시작
   ↓
8. Accordion에서 Ingress 생성 (호스트 라우팅 + ALB annotation)
   ↓
9. 동작 검증 (로그인, 문서 작성, 이미지 업로드)
```

---

## 2. RDS Database 생성

dify가 사용하는 RDS 인스턴스에 outline 전용 database 추가:

```sql
CREATE DATABASE outline;
```

- 별도 user 안 만들고 dify 계정 재사용 (dev 환경 단순화)
- prod에선 별도 user 권장 (권한 격리)

DATABASE_URL 형식:
```
postgres://dify:<비번>@<RDS엔드포인트>:5432/outline
```

`PGSSLMODE=require` 환경변수 필수 (RDS는 SSL 강제).

---

## 3. S3 + IAM Role (IRSA)

상세 셋업은 [S3_IRSA_REQUEST.md](./S3_IRSA_REQUEST.md) 참고.

핵심 셋업 항목 요약:

| 항목 | 값 |
|------|-----|
| 버킷 | `hdo-s3-dev-an2-bao-691729631040-ap-northeast-2-an` |
| IAM Role | `docs-outline-dev-s3-access` |
| Trust Policy 대상 SA | `system:serviceaccount:docs-dev:default` |
| Permission | `s3:PutObject`, `s3:GetObject`, `s3:DeleteObject`, `s3:ListBucket`, `s3:GetBucketLocation`, `s3:PutObjectAcl` |
| CORS Origin | `https://docsdev.oilbank.co.kr` |

ServiceAccount 어노테이션 (배포 후 Accordion에서 적용):
```yaml
metadata:
  annotations:
    eks.amazonaws.com/role-arn: arn:aws:iam::691729631040:role/docs-outline-dev-s3-access
```

---

## 4. Authelia OIDC Client 등록

`llm-dev/authelia-config` ConfigMap의 `identity_providers.oidc.clients` 리스트에 추가:

```yaml
- client_id: outline
  client_name: Outline Wiki
  client_secret: '$pbkdf2-sha512$310000$...'
  authorization_policy: one_factor
  consent_mode: 'pre-configured'
  pre_configured_consent_duration: '1y'
  redirect_uris:
    - https://docsdev.oilbank.co.kr/auth/oidc.callback
  post_logout_redirect_uris:
    - https://docsdev.oilbank.co.kr/
    - https://docsdev.oilbank.co.kr
  scopes: [openid, profile, email, offline_access]
  # require_pkce: false (기본값) — Outline passport-oauth2가 PKCE 미지원
```

### 주의 사항

| 항목 | 이유 |
|------|------|
| `offline_access` scope 필수 | 제거 시 `/api/auth.info` 401 발생 |
| `require_pkce: false` (또는 미설정) | Outline 1.7.1의 passport-oauth2가 PKCE 미지원 |
| `consent_mode: pre-configured` | `implicit`은 offline_access 때문에 매번 동의 화면 표시됨 |
| `post_logout_redirect_uris` | 로그아웃 후 Outline 메인으로 복귀 |

### client_secret 생성

```bash
# 1. 평문 시크릿 생성 (24바이트 = 48자 hex)
openssl rand -hex 24

# 2. Authelia hash로 변환
authelia crypto hash generate pbkdf2 --variant sha512 --password '<평문>'
```

- 평문: Outline env `OIDC_CLIENT_SECRET`에 사용
- `$pbkdf2-sha512$...` hash: Authelia config에 사용

### 적용

ConfigMap 수정 후 `authelia-dev` Pod 재시작 (Accordion에서 rollout).

---

## 5. Route53 + ACM

| 항목 | 값 |
|------|-----|
| 레코드 | `docsdev.oilbank.co.kr` |
| 타입 | A (Alias) |
| 대상 | 기존 dify ALB DNS (internal) |
| 인증서 | 기존 `*.oilbank.co.kr` 와일드카드 재사용 |

---

## 6. K8s 워크로드 구성

### 6.1 Deployment

| 항목 | 값 |
|------|-----|
| 이미지 | `outlinewiki/outline:1.7.1` |
| Replicas | 1 (Outline 단일 인스턴스만 지원) |
| 컨테이너 포트 | 3000 |
| 헬스체크 | `GET /_health` |
| 리소스 요청 | CPU 200m, Memory 512Mi |
| 리소스 제한 | CPU 1, Memory 1.5Gi |

### 6.2 Service

| 항목 | 값 |
|------|-----|
| 이름 | `docs-outline-dev` |
| 타입 | ClusterIP |
| 포트 | 3000 → 3000 |
| 포트 이름 | http (또는 http-port — 중복 주의) |

### 6.3 Ingress

dify와 같은 ALB 그룹 또는 따로: 인프라팀에서 처리

```yaml
metadata:
  annotations:
    kubernetes.io/ingress.class: alb
    alb.ingress.kubernetes.io/group.name: dify-shared    # dify와 동일
    alb.ingress.kubernetes.io/scheme: internal
    alb.ingress.kubernetes.io/target-type: ip
    alb.ingress.kubernetes.io/listen-ports: '[{"HTTPS":443}]'
    alb.ingress.kubernetes.io/ssl-redirect: '443'
    alb.ingress.kubernetes.io/certificate-arn: <기존 와일드카드 ACM ARN>
    alb.ingress.kubernetes.io/healthcheck-path: /_health
spec:
  rules:
    - host: docsdev.oilbank.co.kr
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: docs-outline-dev
                port:
                  number: 3000
```

> `group.name`은 dify ingress에서 정확한 값 확인 후 일치시킴.

---

## 7. 환경변수 (Accordion)

### 7.1 시크릿 종류

| 변수 | 값 |
|------|-----|
| `SECRET_KEY` | `openssl rand -hex 32` (64자 hex) |
| `UTILS_SECRET` | `openssl rand -hex 32` |
| `DATABASE_URL` | `postgres://dify:<비번>@<RDS>:5432/outline` |
| `REDIS_URL` | `redis://redis-dev.llm-dev.svc.cluster.local:6379/3` |
| `OIDC_CLIENT_SECRET` | Authelia에 등록한 outline client_secret 평문 |

### 7.2 일반

```
NODE_ENV                  = production
URL                       = https://docsdev.oilbank.co.kr
PORT                      = 3000
PGSSLMODE                 = require
FORCE_HTTPS               = true
ENABLE_UPDATES            = false

# S3
FILE_STORAGE              = s3
AWS_REGION                = ap-northeast-2
AWS_S3_UPLOAD_BUCKET_NAME = hdo-s3-dev-an2-bao-691729631040-ap-northeast-2-an
AWS_S3_UPLOAD_BUCKET_URL  = https://s3.ap-northeast-2.amazonaws.com
AWS_S3_FORCE_PATH_STYLE   = false
AWS_S3_UPLOAD_MAX_SIZE    = 10485760    # 10 MB

# OIDC
OIDC_CLIENT_ID            = outline
OIDC_AUTH_URI             = https://dify.oilbank.co.kr/auth/api/oidc/authorization
OIDC_TOKEN_URI            = https://dify.oilbank.co.kr/auth/api/oidc/token
OIDC_USERINFO_URI         = https://dify.oilbank.co.kr/auth/api/oidc/userinfo
OIDC_LOGOUT_URI           = https://dify.oilbank.co.kr/auth/logout
OIDC_USERNAME_CLAIM       = preferred_username
OIDC_DISPLAY_NAME         = Authelia
OIDC_SCOPES               = openid profile email offline_access

# SMTP (현재 발송 불가, 메일팀 협의 중)
SMTP_HOST                 = mail.oilbank.co.kr
SMTP_PORT                 = 25
SMTP_SECURE               = false
SMTP_FROM_EMAIL           = outline-noreply@oilbank.co.kr
SMTP_NAME                 = Oilbank Outline
```

> `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`는 설정하지 않음 (IRSA가 처리).

### 업로드 크기 단계별 임곗값

| 단계 | 크기 | bytes |
|------|------|-------|
| 보수적 시작 | 10 MB | `10485760` |
| 일반 위키 | 25 MB | `26214400` |
| PPT 첨부 위주 | 50 MB | `52428800` |
| 영상 첨부도 | 100 MB | `104857600` |

---

## 8. 동작 검증

### 8.1 Pod 상태

```
Accordion → docs-dev → docs-outline-dev Pod
→ Status: Running, Ready: 1/1
→ Logs: "Listening on https://docsdev.oilbank.co.kr"
```

### 8.2 IRSA 환경변수

Pod 터미널에서:

```bash
env | grep AWS_
```

기대:
```
AWS_ROLE_ARN=arn:aws:iam::691729631040:role/docs-outline-dev-s3-access
AWS_WEB_IDENTITY_TOKEN_FILE=/var/run/secrets/eks.amazonaws.com/serviceaccount/token
AWS_REGION=ap-northeast-2
```

### 8.3 사용자 동작

1. https://docsdev.oilbank.co.kr 접속
2. "Continue with Authelia" 클릭
3. LDAP 계정으로 로그인
4. 워크스페이스 자동 생성 (첫 사용자 = admin)
5. 새 문서 작성 → 본문에 이미지 업로드 테스트

### 8.4 S3 업로드 확인

브라우저 DevTools → Network 탭:
- S3 도메인(`*.amazonaws.com`)으로 PUT 요청 200 OK
- 응답 헤더에 ETag 보임

AWS 콘솔 → S3 버킷:
- 새 객체 보임 (생성 시각·크기)

---

## 9. 일반적인 첫 배포 실패

| 증상 | 원인 | 대응 |
|------|------|------|
| `SECRET_KEY must be 64 hexadecimal characters` | env 값 길이 부족 | `openssl rand -hex 32` (윈도우는 `1..32` PowerShell) |
| `redirect_uri mismatch` | Authelia 등록 URI와 불일치 | client redirect_uris 값 정확히 매칭 |
| `code_challenge missing` | PKCE 강제 | Authelia client `require_pkce` 제거 |
| `/api/auth.info` 401 | offline_access scope 누락 | OIDC_SCOPES + Authelia scopes 양쪽 일치 |
| `exec format error` | 이미지 아키텍처 불일치 (ARM64 → AMD64 노드) | `crane pull --platform linux/amd64`로 이미지 다시 |
| `CredentialsProviderError` | IRSA 미적용 | ServiceAccount 어노테이션 + Pod 재시작 |
| `CORS error` (브라우저) | S3 버킷 CORS 미설정 | S3 버킷 CORS 추가 |

자세한 운영 트러블슈팅은 `OUTLINE_OPERATIONS_GUIDE.md` 참고.

---

## 10. 배포 후 다음 단계

배포 완료 후:

1. `OUTLINE_OPERATIONS_GUIDE.md`로 일상 운영 참고
2. 사용자에게 도메인 안내 → 자동 가입으로 워크스페이스 합류
3. 워크스페이스 이름·로고 변경 (Outline UI)
4. 운영 후 점검 항목:
   - PVC/S3 사용량 추이
   - Pod 메모리·CPU 사용량
   - 로그인 성공률
   - 첨부 업로드 성공률
