# Outline + ExcaliDash K8s 배포 계획 (사내 운영)

회사 EKS(Accordion 관리)에 Outline(위키) + ExcaliDash(드로잉)를 배포하는 계획서. **기존 dify 인프라(`배포설정.md`) 위에 얹는 작업**이지, 인프라 새로 만드는 게 아님. 로컬 docker-compose 검증은 끝났고 (`/Users/youngbo/Dev/collab-tools-test/`), 이제 운영 배포.

**관련 문서:**
- `배포설정.md` — dify 본체 배포 가이드 (RDS/Redis/Authelia 인프라 정의된 곳)
- `OUTLINE_EXCALIDASH_TEST_GUIDE.md` — 로컬 테스트 가이드
- `이 문서` — Outline/ExcaliDash 운영 배포 (dify 인프라 재사용)

회사 인프라 정보:
- EKS + Accordion 관리 (kubectl 불가, Pod 터미널 가능)
- 시크릿/환경변수는 **Accordion UI에서 직접 입력** (manifest에 안 박음)
- ALB ingress controller (internal, dify와 공유)
- Authelia: `https://dify.oilbank.co.kr/auth` (dify 도메인 서브패스, K8s에선 `llm-dev` 네임스페이스의 `authelia-dev`)
- `authelia-sso-dev`(dify-sso 브릿지)는 Dify 전용 — Outline/ExcaliDash 배포에선 무관
- 이미지: 외부 직접 pull

---

## 0. 한 페이지 요약

| 항목 | 결정 |
|------|------|
| 네임스페이스 | **앱별 분리** — Outline은 `docs-dev`, ExcaliDash는 `exc-dev` (dev 환경. 추후 `*-prod` 별도) |
| Accordion 앱 이름 | `docs-dev/docs-outline-dev`, `exc-dev/exc-backend-dev`, `exc-dev/exc-frontend-dev` |
| 호스트명 | `docs.oilbank.co.kr`, `exc.oilbank.co.kr` |
| Outline 이미지 | `outlinewiki/outline:1.7.1` (공식 직접 pull) |
| ExcaliDash 이미지 | `zimengxiong/excalidash-backend:0.5.0`, `zimengxiong/excalidash-frontend:0.5.0` |
| Outline DB | RDS (dify 인스턴스에 `outline` database 추가) |
| Outline Redis | **dify Redis 재사용** (`redis-dev.llm-dev.svc.cluster.local:6379/3`, 비번 없음) |
| ExcaliDash DB | SQLite (PVC) |
| 첨부 저장소 | **S3** (RAG 대비, 결정됨) |
| ECR | **사용 안 함** (외부 직접 pull, 결정됨) |
| EFS | **신규 할당** (Accordion 표준 패턴, 결정됨) |
| TLS | ALB에서 termination, 기존 와일드카드 ACM 재사용 |
| ALB | **dify와 공유** (`alb.ingress.kubernetes.io/group.name`로 묶음) — 새 LB 안 띄움 |
| 인증 | 기존 `llm-dev/authelia-dev`에 OIDC client 2개 추가 (`authelia-sso-dev`는 Dify 전용, 건드리지 않음) |
| 권한 정책 | LDAP 그룹 매핑 사용 안 함. 첫 admin = 부트스트랩(ExcaliDash) / 첫 로그인(Outline). 이후 각 앱 UI에서 승격. **추후 조직도 DB 기반 팀 권한 별도 설계 (dify 패턴)** |

---

## 1. 작업 순서 (의존성 순)

```
✅ Accordion 네임스페이스 수령: docs-dev, exc-dev, EFS 할당분

[1] AWS 인프라 사전작업 (병렬 가능)
   ├─ Route53: docs/exc 레코드
   ├─ ACM: 기존 cert 재사용 확인
   ├─ RDS: outline database/user 생성 (docs-dev에서 사용)
   ├─ Security Group: docs-dev / exc-dev Pod CIDR → RDS, Redis
   └─ S3 버킷 + IRSA (docs-dev에 마운트)
        ↓
[2] 회사 Authelia configuration.yml에 OIDC client 2개 추가
        ↓
[3] Outline 배포 (docs-dev) → 동작 확인
        ↓
[4] ExcaliDash 배포 (exc-dev) → 동작 확인
        ↓
[5] (별도 작업) RAG 연계 설계
```

---

## 2. AWS 사전작업

### 2.1 RDS (본인 권한 있음)

dify가 쓰는 RDS 인스턴스에 **기존 `dify` 유저 그대로** outline database만 신규 생성 (별도 비번 관리 X):

```bash
# psql로 RDS 접속 (어느 database로 들어가도 무관, 보통 dify로)
psql -h <RDS엔드포인트> -U dify -d dify
```

```sql
-- 접속 후 실행
CREATE DATABASE outline OWNER dify;
\l                    -- outline 보이는지 확인
\q
```

→ DATABASE_URL: `postgres://dify:기존DIFY비번@RDS엔드포인트:5432/outline`
→ `PGSSLMODE=require` (RDS는 SSL 필요)
→ ENCODING은 RDS 기본 UTF8이라 명시 불필요 (걱정되면 `ENCODING 'UTF8'` 추가)

> dev 환경이라 dify 유저 재사용으로 단순화. prod는 별도 outline 유저 생성 권장 (권한 격리).

### 2.2 Route53 (인프라팀 또는 본인)

A (Alias) 레코드 2개 추가. **둘 다 dify가 쓰는 그 ALB와 동일한 DNS를 가리킴** (별도 LB 안 띄움).

| 레코드 | 타입 | 가리키는 곳 |
|--------|------|-------------|
| `docs.oilbank.co.kr` | A (Alias) | 기존 dify ALB DNS (`internal-...elb.amazonaws.com`) |
| `exc.oilbank.co.kr` | A (Alias) | 위와 동일 |

→ ALB는 Host 헤더 보고 어느 K8s Service로 보낼지 결정. 그 라우팅 규칙은 §4.5, §5.5의 Ingress 리소스로 정의.

### 2.3 ACM 인증서

dify가 쓰는 와일드카드 인증서 (`*.oilbank.co.kr`)면 `docs`, `exc` 둘 다 커버되어 그대로 재사용.
SAN cert면 `docs.oilbank.co.kr`, `exc.oilbank.co.kr` 추가 필요.

### 2.4 EFS

Accordion에서 신규 할당 받음 (워크로드별 EFS 표준 패턴).
- Outline은 S3 사용이라 EFS 불필요. 단, 임시 캐시/로그 용도로 작은 PVC 1개 정도는 권장.
- `excalidash-data` — SQLite 저장 (필수)

→ Accordion이 자동으로 StorageClass 매핑.

### 2.5 Security Group

`docs-dev`, `exc-dev` 두 네임스페이스의 Pod CIDR이 다음에 접근 가능해야 함:
- RDS 5432 (Outline만 사용 — `docs-dev` 필수, `exc-dev`는 SQLite라 불필요)
- Redis 6379 (Outline만 사용 — `docs-dev` 필수)

dify 네임스페이스 룰을 카피해서 적용. 인프라팀에 두 네임스페이스 Pod CIDR 추가 요청.

### 2.6 S3 + IRSA (Outline 첨부)

새 버킷 1개:
- 이름: `oilbank-docs-dev-outline-attachments` (또는 회사 명명규칙에 맞게)
- 리전: `ap-northeast-2`
- 퍼블릭 액세스 차단: ALL
- 버전 관리: 활성화 (실수 삭제 복구용)
- 서버 측 암호화: SSE-S3 또는 SSE-KMS

IAM 정책 (IRSA로 outline ServiceAccount에 부여):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:PutObjectAcl",
        "s3:GetObject",
        "s3:DeleteObject",
        "s3:ListBucket",
        "s3:GetBucketLocation"
      ],
      "Resource": [
        "arn:aws:s3:::oilbank-docs-dev-outline-attachments",
        "arn:aws:s3:::oilbank-docs-dev-outline-attachments/*"
      ]
    }
  ]
}
```

CORS 설정 (Outline이 브라우저에서 직접 업로드/다운로드):

```json
[
  {
    "AllowedHeaders": ["*"],
    "AllowedMethods": ["GET", "PUT", "POST", "DELETE"],
    "AllowedOrigins": ["https://docs.oilbank.co.kr"],
    "ExposeHeaders": ["ETag"],
    "MaxAgeSeconds": 3000
  }
]
```

---

## 3. 회사 Authelia에 OIDC client 추가 (`llm-dev/authelia-dev`)

### 3.1 client_secret 생성

**[macOS / Linux]** 평문 시크릿 2개 생성:
```bash
openssl rand -hex 24   # outline용
openssl rand -hex 24   # excalidash용
```

**[Windows PowerShell]** OpenSSL 없을 때 — **바이트 수 주의** (용도별로 다름):

```powershell
function Get-RandHex { param([int]$bytes)
  $b = New-Object byte[] $bytes
  [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
  -join ($b | ForEach-Object { $_.ToString('x2') })
}

# OIDC client_secret (Authelia 등록용 평문) — 24바이트
Get-RandHex 24    # outline용
Get-RandHex 24    # excalidash용

# SECRET_KEY / UTILS_SECRET 등 앱 시크릿 — 32바이트 (64자!)
Get-RandHex 32
```

또는 한 줄로 (숫자 부분이 바이트 수):
```powershell
-join (1..24 | ForEach-Object { '{0:x2}' -f (Get-Random -Maximum 256) })   # 24바이트
-join (1..32 | ForEach-Object { '{0:x2}' -f (Get-Random -Maximum 256) })   # 32바이트
```

| 용도 | 바이트 | hex 길이 |
|------|--------|----------|
| OIDC_CLIENT_SECRET | 24 | 48자 |
| SECRET_KEY, UTILS_SECRET, JWT_SECRET, CSRF_SECRET | 32 | **64자** |

**[Windows cmd / Git Bash]** Git이 설치돼 있으면 `openssl`이 같이 깔려서 macOS 명령 그대로 사용 가능:
```bash
openssl rand -hex 24
```

---

각 평문을 hash로 변환 (Authelia 호스트/Pod에서):
```bash
authelia crypto hash generate pbkdf2 --variant sha512 --password '<위에서 만든 평문>'
```

→ 평문은 Accordion env에 `OIDC_CLIENT_SECRET`으로 입력
→ `$pbkdf2-sha512$...` hash는 Authelia configuration.yml의 `client_secret`에 입력

### 3.2 configuration.yml 수정

기존 dify client 옆에 이어붙임:

```yaml
identity_providers:
  oidc:
    clients:
      - client_id: dify           # 기존 - 건드리지 말 것
        # ...

      - client_id: outline
        client_name: Outline Wiki
        client_secret: '$pbkdf2-sha512$...'
        authorization_policy: one_factor          # dify 정책에 맞춰 통일
        consent_mode: 'pre-configured'            # 첫 로그인만 동의 화면, 이후 안 뜸 (Authelia 4.39+에서 offline_access 있으면 매번 뜰 수도)
        pre_configured_consent_duration: '1y'
        redirect_uris:
          - https://docs.oilbank.co.kr/auth/oidc.callback
        post_logout_redirect_uris:                # 로그아웃 후 Outline 메인으로 복귀
          - https://docs.oilbank.co.kr/
          - https://docs.oilbank.co.kr
        scopes: [openid, profile, email, offline_access]   # offline_access 필수 (제거 시 /api/auth.info 401)
        # require_pkce: 명시 안 함 = 기본값 false
        # → Outline 1.7.1의 passport-oauth2가 PKCE 미지원. true면 'code_challenge missing' 에러.

      - client_id: excalidash
        client_name: ExcaliDash
        client_secret: '$pbkdf2-sha512$...'
        authorization_policy: one_factor          # dify 정책에 맞춰 통일
        consent_mode: 'implicit'                  # 동의 화면 안 뜸 (offline_access 없으니 implicit 동작)
        redirect_uris:
          - https://exc.oilbank.co.kr/api/auth/oidc/callback
        scopes: [openid, profile, email, groups]
        require_pkce: true                        # ExcaliDash는 PKCE(S256) 지원 확인됨
```

### 3.3 PKCE 호환성 (앱별 차이)

| 앱 | `require_pkce` | 이유 |
|----|---------------|------|
| Outline | **false** (기본값) | Outline 1.7.1이 사용하는 `passport-oauth2` 라이브러리가 PKCE 미지원. `true`로 두면 로그에 `Clients must include a 'code_challenge'` 에러 발생 |
| ExcaliDash | **true** | 로컬 테스트에서 PKCE(S256) 정상 동작 확인 |

> Outline은 confidential client (client_secret 보유) 라 PKCE 없어도 Authorization Code Flow + secret로 충분한 보안. 운영에서도 outline은 `require_pkce: false` 유지.

### 3.4 consent_mode 차이 (앱별)

| 앱 | `consent_mode` | 이유 |
|----|---------------|------|
| Outline | **pre-configured (1y)** | `offline_access` scope 사용 시 Authelia가 implicit 모드라도 매번 동의 요구 (OAuth 2.1 보안). pre-configured는 첫 1회만 묻고 1년간 기억 |
| ExcaliDash | **implicit** | offline_access 미사용 → implicit으로 동의 화면 완전 생략 가능 |

> `consent_mode: implicit`이 안 먹는 케이스 = scope에 `offline_access` 있을 때. 리프레시 토큰 발급은 보안상 명시적 사용자 동의 필요. `pre-configured`로 우회.

### 3.5 offline_access 필수 (Outline)

Outline `OIDC_SCOPES`에 **`offline_access`는 반드시 포함**해야 합니다. 제거 시:
- OIDC 로그인 자체는 성공
- 직후 `/api/auth.info`에서 **401 Unauthorized** 발생
- 사용자에게 "인증실패 - 현재 로그인할 수 없습니다" 표시

원인: Outline 1.7.1이 grant_flow에서 refresh_token 기대. offline_access 빠지면 grant_types와 불일치 → 세션 생성 실패.

→ Authelia client `scopes`와 Outline `OIDC_SCOPES` 양쪽 모두 `offline_access` 포함.

### 3.6 Authelia 재시작

`llm-dev` 네임스페이스의 `authelia-dev` ConfigMap 수정 후 Pod rollout (Accordion에서). `authelia-sso-dev`(dify-sso 브릿지)는 건드리지 말 것 — Dify 전용이라 변경 불필요.

---

## 4. Accordion에서 Outline 배포 (`docs-dev`)

### 4.1 워크로드 정의

| 항목 | 값 |
|------|-----|
| 앱 이름 (Accordion) | `docs-outline-dev` |
| 네임스페이스 | `docs-dev` |
| 워크로드 종류 | Deployment |
| Replicas | 1 (Outline은 단일 인스턴스만 지원) |
| 이미지 | `outlinewiki/outline:1.7.1` |
| 컨테이너 포트 | 3000 |
| Service 포트 | 3000 (Service 이름도 `docs-outline-dev`) |
| 헬스체크 | HTTP GET `/_health` 포트 3000 |
| 리소스 요청 | CPU 200m, Memory 512Mi |
| 리소스 제한 | CPU 1, Memory 1.5Gi |

### 4.2 스토리지 (S3 사용)

Outline 첨부는 S3로 → **PVC 불필요**.

ServiceAccount에 IRSA 어노테이션 붙임:
```yaml
serviceAccountName: docs-outline-dev
# 별도 ServiceAccount 리소스에 IRSA annotation
# eks.amazonaws.com/role-arn: arn:aws:iam::<account>:role/docs-outline-dev-s3-access
```

### 4.3 Accordion env (시크릿 종류)

> 비밀번호/토큰류는 마스킹 입력. 다른 곳에 노출되지 않도록 입력 후 한 번 더 확인.

```
SECRET_KEY               = openssl rand -hex 32
UTILS_SECRET             = openssl rand -hex 32
DATABASE_URL             = postgres://dify:기존DIFY비번@RDS엔드포인트:5432/outline
REDIS_URL                = redis://redis-dev.llm-dev.svc.cluster.local:6379/3
OIDC_CLIENT_SECRET       = (Authelia에 등록한 outline 평문)
```

### 4.4 Accordion env (일반)

```
NODE_ENV                 = production
URL                      = https://docs.oilbank.co.kr
PORT                     = 3000
PGSSLMODE                = require
FORCE_HTTPS              = true
ENABLE_UPDATES           = false

# S3 첨부 저장소
FILE_STORAGE             = s3
AWS_REGION               = ap-northeast-2
AWS_S3_UPLOAD_BUCKET_NAME = oilbank-docs-dev-outline-attachments
AWS_S3_UPLOAD_BUCKET_URL = https://s3.ap-northeast-2.amazonaws.com
AWS_S3_FORCE_PATH_STYLE  = false
AWS_S3_UPLOAD_MAX_SIZE   = 262144000
# IRSA로 권한 부여하므로 access key 불필요

# OIDC (Authelia)
OIDC_CLIENT_ID           = outline
OIDC_AUTH_URI            = https://dify.oilbank.co.kr/auth/api/oidc/authorization
OIDC_TOKEN_URI           = https://dify.oilbank.co.kr/auth/api/oidc/token
OIDC_USERINFO_URI        = https://dify.oilbank.co.kr/auth/api/oidc/userinfo
OIDC_LOGOUT_URI          = https://dify.oilbank.co.kr/auth/logout
OIDC_USERNAME_CLAIM      = preferred_username
OIDC_DISPLAY_NAME        = Authelia        # 또는 "Oilbank SSO" 등 사용자 친화적 이름
OIDC_SCOPES              = openid profile email offline_access   # ★ offline_access 필수 — 제거 시 /api/auth.info 401

# SMTP (메일 발송 — 초대장, 멘션 알림 등)
SMTP_HOST                = mail.oilbank.co.kr      # 회사 SMTP relay 주소
SMTP_PORT                = 587                      # 587(STARTTLS) / 465(SMTPS) / 25(평문)
SMTP_SECURE              = false                    # ★ 포트별 매핑: 587→false, 465→true, 25→false
SMTP_FROM_EMAIL          = outline-noreply@oilbank.co.kr   # 회사 SMTP가 허용하는 from 도메인
SMTP_NAME                = Oilbank Outline          # 발신자 표시 이름
SMTP_REPLY_EMAIL         =                           # 답장 받을 주소 (선택)
# SMTP_USERNAME, SMTP_PASSWORD: 회사 relay가 인증 필요 시만. 사내 IP relay면 미설정으로 OK

# SMTP_SECURE 의미:
#  true  = 연결 시작부터 직접 SSL/TLS (포트 465 전용)
#  false = 평문 시작 → STARTTLS로 자동 업그레이드 (포트 587 표준)
# 잘못된 조합 시 "wrong version number" TLS 에러 발생
```

### 4.4-1 SMTP 환경변수 추가 설명

회사 SMTP 정보 확인 후 위 값 채워 입력:

| 변수 | 의미 | 일반 값 |
|------|------|---------|
| `SMTP_HOST` | SMTP 서버 호스트 | `smtp.oilbank.co.kr` 또는 사내 IP |
| `SMTP_PORT` | 포트 | 587 (가장 흔함) |
| `SMTP_SECURE` | TLS 사용 | `true` |
| `SMTP_FROM_EMAIL` | 발신자 주소 | SMTP가 허용하는 from 도메인 (대개 `@oilbank.co.kr`) |
| `SMTP_NAME` | 발신자 이름 | 사용자에게 표시 |
| `SMTP_USERNAME` | 인증 계정 | **사내 IP relay면 빈 값** (인증 없음). SMTP AUTH 필요 시만 |
| `SMTP_PASSWORD` | 비번 | 위와 동일 (시크릿) |

> SMTP_USERNAME/PASSWORD 사용 시 → §4.3 시크릿 섹션으로 이동.

확인 사항 (인프라/메일팀에 문의):
- 허용 포트 (25/465/587)
- 인증 방식 (relay vs SMTP AUTH)
- 허용 from 도메인 (보통 `@회사도메인`만)

테스트:
- Outline에서 사용자 초대 → 본인 이메일 도착 확인
- 안 되면 Outline Pod 로그에 `smtp` 키워드로 에러 확인

### 4.5 Service

Accordion에서 Workload 생성 시 **Service 포트**를 입력하면 Service까지 자동 생성됨. 별도 메뉴로 만들 필요 없는 경우가 많지만, 명시적으로 확인:

| 항목 | 값 |
|------|-----|
| 이름 | `docs-outline-dev` (Deployment 이름과 동일) |
| 네임스페이스 | `docs-dev` |
| 타입 | ClusterIP (기본) |
| 포트 | 3000 |
| Target Port | 3000 (Pod의 컨테이너 포트) |
| 셀렉터 | Workload의 라벨 (자동 매핑) |

> Accordion에 Service 별도 메뉴가 있으면 위 값 그대로 입력. Workload 생성 시 자동 생성되면 별도 작업 불필요. 생성 후 Services 메뉴에서 `docs-outline-dev`가 보이는지 확인.

### 4.6 Ingress

**ALB 자체는 dify와 공유.** AWS Load Balancer Controller가 `alb.ingress.kubernetes.io/group.name`이 같은 Ingress들을 한 ALB에 묶어줌. 새 LB DNS 안 만듦.

> 사전 작업: dify의 기존 Ingress에서 다음 annotation 값들을 확인하고 우리도 **같은 값으로 통일** (또는 미지정으로 비워서 자동 관리에 위임):
> - `alb.ingress.kubernetes.io/group.name` ★ **필수 동일** (같은 ALB에 묶임)
> - `alb.ingress.kubernetes.io/load-balancer-name` (지정돼 있으면 동일하게, 아니면 비움)
> - `alb.ingress.kubernetes.io/security-groups` (지정돼 있으면 동일하게, 아니면 비움 — ALB 1개에 SG 다르게 박으면 충돌)
> - `alb.ingress.kubernetes.io/scheme`, `target-type`, `listen-ports`, `certificate-arn` 등 ALB 공통 설정도 dify와 일치시킴

Outline Ingress 리소스 (Accordion에 manifest 또는 UI로 입력):

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: docs-outline-dev
  namespace: docs-dev
  annotations:
    kubernetes.io/ingress.class: alb
    alb.ingress.kubernetes.io/group.name: dify-shared           # ← dify와 동일 값
    alb.ingress.kubernetes.io/scheme: internal
    alb.ingress.kubernetes.io/target-type: ip
    alb.ingress.kubernetes.io/listen-ports: '[{"HTTPS":443}]'
    alb.ingress.kubernetes.io/ssl-redirect: '443'
    alb.ingress.kubernetes.io/certificate-arn: <기존 와일드카드 ACM ARN>
    alb.ingress.kubernetes.io/healthcheck-path: /_health
spec:
  rules:
    - host: docs.oilbank.co.kr
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

### 4.7 검증

- [ ] Pod 정상 (`Ready`)
- [ ] `https://docs.oilbank.co.kr/` 접속 → 200
- [ ] `https://docs.oilbank.co.kr/api/auth.config` → JSON에 Authelia provider 보임
- [ ] 브라우저 로그인 → Authelia로 리다이렉트 → 사내 LDAP 계정으로 로그인 → Outline 워크스페이스 자동 생성
- [ ] 새 문서 만들고 저장 → Pod 재시작 후 유지 확인 (PVC 동작)

---

## 5. Accordion에서 ExcaliDash 배포 (`exc-dev`)

### 5.1 워크로드 정의 (2개 Deployment, 네임스페이스 `exc-dev`)

**backend** — 앱 이름 `exc-backend-dev`

| 항목 | 값 |
|------|-----|
| 앱 이름 (Accordion) | `exc-backend-dev` |
| 이미지 | `zimengxiong/excalidash-backend:0.5.0` |
| Replicas | 1 (SQLite + in-memory 상태라 단일 인스턴스만) |
| 포트 | 8000 (Service 이름도 `exc-backend-dev`) |
| 헬스체크 | HTTP GET `/health` 포트 8000 |
| PVC | `exc-backend-dev-data` 5Gi → `/app/prisma` |
| 리소스 | CPU 100m, Memory 256Mi |

**frontend** — 앱 이름 `exc-frontend-dev`

| 항목 | 값 |
|------|-----|
| 앱 이름 (Accordion) | `exc-frontend-dev` |
| 이미지 | `zimengxiong/excalidash-frontend:0.5.0` |
| Replicas | 1 |
| 포트 | 80 (Service 이름도 `exc-frontend-dev`) |
| 헬스체크 | HTTP GET `/` 포트 80 |
| 리소스 | CPU 50m, Memory 128Mi |

### 5.2 Accordion env — backend (시크릿)

```
JWT_SECRET               = openssl rand -hex 32
CSRF_SECRET              = openssl rand -hex 32
OIDC_CLIENT_SECRET       = (Authelia에 등록한 excalidash 평문)
```

### 5.3 Accordion env — backend (일반)

```
NODE_ENV                 = production
PORT                     = 8000
DATABASE_URL             = file:/app/prisma/dev.db
TRUST_PROXY              = 1
FRONTEND_URL             = https://exc.oilbank.co.kr

AUTH_MODE                = hybrid
OIDC_PROVIDER_NAME       = Authelia
OIDC_ISSUER_URL          = https://dify.oilbank.co.kr/auth
OIDC_CLIENT_ID           = excalidash
OIDC_REDIRECT_URI        = https://exc.oilbank.co.kr/api/auth/oidc/callback
OIDC_SCOPES              = openid profile email groups
OIDC_GROUPS_CLAIM        = groups
OIDC_ADMIN_GROUPS        =                        # 빈 값 (정책: LDAP 그룹 안 씀)
```

> **권한 정책: LDAP 그룹 기반 admin 매핑은 사용하지 않음.** 회사 조직도/팀별 권한은 별도 DB로 관리하는 방향(dify와 동일 패턴). ExcaliDash admin은 부트스트랩 코드로 첫 1명 만들고, 이후 ExcaliDash UI에서 승격하는 방식으로 운영. `groups` scope는 미래 확장 위해 토큰엔 실어두지만 매핑엔 사용 안 함.

### 5.4 Accordion env — frontend

```
BACKEND_URL              = exc-backend-dev.exc-dev.svc.cluster.local:8000
```

### 5.5 Service

각 Workload별로 Service 1개씩 (Accordion이 Workload 생성 시 자동, 또는 별도 메뉴):

| Service 이름 | 네임스페이스 | 타입 | 포트 | Target Port | 비고 |
|-------------|------------|------|------|-------------|------|
| `exc-backend-dev` | exc-dev | ClusterIP | 8000 | 8000 | frontend가 내부 DNS로 호출 |
| `exc-frontend-dev` | exc-dev | ClusterIP | 80 | 80 | Ingress가 가리킴 |

> backend Service의 cluster DNS: `exc-backend-dev.exc-dev.svc.cluster.local:8000` — frontend의 `BACKEND_URL` 환경변수가 이걸 참조.

### 5.6 Ingress

§4.5와 동일 패턴 (같은 ALB 공유). frontend로 라우팅 — `/api/*`는 frontend의 nginx가 backend로 reverse proxy.

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: exc-frontend-dev
  namespace: exc-dev
  annotations:
    kubernetes.io/ingress.class: alb
    alb.ingress.kubernetes.io/group.name: dify-shared           # ← dify와 동일 값
    alb.ingress.kubernetes.io/scheme: internal
    alb.ingress.kubernetes.io/target-type: ip
    alb.ingress.kubernetes.io/listen-ports: '[{"HTTPS":443}]'
    alb.ingress.kubernetes.io/ssl-redirect: '443'
    alb.ingress.kubernetes.io/certificate-arn: <기존 와일드카드 ACM ARN>
    alb.ingress.kubernetes.io/healthcheck-path: /
spec:
  rules:
    - host: exc.oilbank.co.kr
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: exc-frontend-dev
                port:
                  number: 80
```

### 5.7 첫 admin 부트스트랩

`AUTH_MODE=hybrid` 첫 배포 시 backend 로그에 1회용 부트스트랩 코드 출력:
```
[BOOTSTRAP SETUP] One-time admin setup code: ABC123XYZ
```

→ Accordion Pod 로그에서 확인 → `https://exc.oilbank.co.kr` 접속해 admin 계정 생성. 그 다음부터 OIDC 로그인 가능.

대안: `AUTH_MODE=oidc_enforced`로 시작하면 부트스트랩 단계 건너뛰고 OIDC만 사용 (추천).

### 5.8 검증

- [ ] backend Pod healthy (`/health` 200)
- [ ] frontend Pod healthy
- [ ] `https://exc.oilbank.co.kr/` 접속 → 200
- [ ] OIDC 로그인 흐름 정상
- [ ] admin 그룹 매핑 확인 (어드민 메뉴 노출)
- [ ] SSO: 같은 브라우저로 Outline 먼저 로그인 → ExcaliDash 들어가면 자동 로그인됨

---

## 6. 운영 후 점검

배포 1주~2주 시점에 확인:

- [ ] PVC 사용량 추이 (Outline 첨부, ExcaliDash SQLite 크기)
- [ ] RDS 연결 수 (dify와 합산해 임계 안 넘는지)
- [ ] Redis 메모리 사용량 (dify와 outline 합산)
- [ ] Outline 백그라운드 잡 정상 처리 (search index, document export 등)
- [ ] ExcaliDash 그림 동시 편집 정상

---

## 7. RAG 연계 (별도 작업, 나중)

Outline 본문을 dify RAG에 연결할 때 검토할 항목:
- API 토큰 발급용 서비스계정 (`rag-indexer` 등)
- 컬렉션 인덱싱 정책 (제외 컬렉션 표식 합의)
- 임베딩: 기존 Weaviate 인스턴스 재사용
- 동기화: webhook 기반 또는 주기 잡
- 관련 메모: `~/.claude/projects/-Users-youngbo-Dev-dify/memory/project_outline_excalidash_local_test.md`

## 7-1. 팀 권한 (조직도 DB 기반, 나중)

dify와 동일하게 회사 조직도/팀 정보를 DB에 두고 그 기준으로 앱 권한을 부여할 계획.

**1단계 (지금):** 각 앱 자체 admin UI로 수동 관리 — 부트스트랩 또는 첫 로그인으로 시드 admin 1명 → UI로 권한 승격.

**2단계 (나중):** 조직도 DB 통합. 후보 방식:
- Authelia가 OIDC 토큰에 부서/팀 custom claim 추가 → 앱이 claim 보고 권한 부여
- 또는 앱 자체 API로 조직도 DB 동기화 (Outline은 API 풍부, ExcaliDash는 제한적)
- dify-sso가 어떻게 처리하는지 보고 같은 패턴 채택 (일관성)

LDAP 그룹은 인증 통과 외에는 권한 결정에 안 씀.

---

## 8. 신규 리소스 vs 공유 리소스 한눈에

| 리소스 | 신규 작성 | 공유/재사용 | 비고 |
|--------|-----------|-------------|------|
| ALB (AWS LB) | | ✅ dify ALB 그대로 | `group.name`으로 묶음 |
| ALB DNS | | ✅ 같은 DNS | Route53 alias 둘 다 같은 곳 |
| ACM 인증서 | | ✅ 와일드카드면 재사용 | SAN cert면 추가 발급 |
| Route53 A 레코드 | ✅ 2개 (`docs`, `exc`) | | |
| RDS 인스턴스 | | ✅ dify와 같은 인스턴스 | `outline` DB만 신규 |
| Redis 인스턴스 | | ✅ dify와 같은 인스턴스 | DB index만 다르게 (`/3`) |
| EFS | ✅ ExcaliDash용 1개 | | Outline은 S3 |
| S3 버킷 | ✅ 1개 (Outline 첨부) | | |
| K8s Namespace | ✅ 받음: `docs-dev`, `exc-dev` | | 앱별 분리 |
| K8s Ingress 리소스 | ✅ 2개 (`docs-outline-dev`, `exc-frontend-dev`) | | 호스트별 라우팅 |
| K8s Deployment | ✅ 3개 (`docs-outline-dev`, `exc-backend-dev`, `exc-frontend-dev`) | | |
| K8s Service | ✅ 3개 (Deployment 이름과 동일) | | |
| K8s ServiceAccount | ✅ `docs-outline-dev` (IRSA) | | S3 권한 |
| Authelia OIDC client | ✅ 2개 등록 | | configuration.yml ConfigMap에 추가 |
| LDAP | | ✅ dify 때 연결한 거 그대로 | 새 작업 없음 |

---

## 9. 로컬 테스트와 차이점 요약

로컬은 OIDC 흐름만 검증한 것이고, K8s에선 다음 항목이 바뀜:

| 항목 | 로컬 테스트 | K8s 운영 |
|------|------------|----------|
| Authelia | 4.37.5 컨테이너 + Caddy TLS | 회사 Authelia 그대로 |
| 사용자 | testadmin/testuser (가짜) | 회사 LDAP |
| Outline DB | PostgreSQL 컨테이너 | RDS (dify 인스턴스, outline DB) |
| Outline Redis | Redis 컨테이너 | dify Redis 재사용 (`/3` index) |
| ExcaliDash DB | SQLite (컨테이너 볼륨) | SQLite (EFS PVC) |
| Outline 첨부 | 컨테이너 볼륨 | S3 (`oilbank-docs-dev-outline-attachments`) |
| 도메인 | localhost:3000, :6767 | docs.*, exc.* |
| TLS | self-signed (Caddy) | ALB + ACM 와일드카드 |
| `NODE_TLS_REJECT_UNAUTHORIZED=0` | 필요 | **제거** (정상 cert) |
| `NODE_ENV` | development (ExcaliDash) | production (https라 OK) |
| `AUTH_MODE` | oidc_enforced | hybrid 또는 oidc_enforced |

OIDC env 변수 키 이름과 redirect URI 경로 구조는 동일. 도메인/시크릿 값만 바뀜.
