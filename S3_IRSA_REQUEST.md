# S3 + IRSA 셋업 요청 (Outline)

Outline 첨부/이미지 업로드용 S3 + IRSA 셋업을 인프라팀에 요청하기 위한 정리 문서.

증상: Outline에서 이미지 업로드 시 `CredentialsProviderError: Could not load credentials from any providers`.
원인: ServiceAccount에 IRSA 어노테이션 없음 → AWS SDK가 자격증명 못 찾음.

---

## 0. 미리 셋업 권장 (우선순위)

| 항목 | 필수도 | 이유 |
|------|------|------|
| **IAM Role + IRSA 어노테이션** | 🔴 필수 | 없으면 자격증명 불가 |
| **CORS 설정** | 🔴 필수 | 없으면 브라우저 업로드 무조건 실패 (CORS 차단) |
| **Prefix (공유 버킷이면)** | 🟡 권장 | 다른 앱과 격리, IAM 권한 좁히기 |
| Bucket Policy | 🟢 선택 | 회사 보안 표준 따름 |
| 암호화 (SSE) | 🟢 선택 | 회사 표준 |

→ **IAM Role + CORS + (필요시) Prefix** 3가지를 첫 요청에서 한 번에 받는 게 효율적.

---

## 1. 받은 정보

| 항목 | 값 |
|------|-----|
| 환경 | 개발 (dev) |
| 서비스 | S3 |
| 버킷 이름 | `hdo-s3-dev-an2-bao-691729631040-ap-northeast-2-an` |
| 버킷 ARN | `arn:aws:s3:::hdo-s3-dev-an2-bao-691729631040-ap-northeast-2-an` |
| AWS 계정 ID | `691729631040` (버킷 이름에 박혀 있음) |
| 리전 | `ap-northeast-2` (버킷 이름에서 추론) |

---

## 2. 인프라팀에 확인 필요한 항목

### Q1. 이 버킷이 Outline 전용인가, 공유인가?

이름에 `bao`가 있어 여러 앱이 공유하는 버킷으로 추정됩니다.

- 전용이면 → 그대로 사용
- 공유면 → outline용 prefix(폴더) 지정 필요 (예: `outline/`, `docs-dev/`)

**답변 예상:**
- "outline 전용입니다" / "공유입니다, prefix는 `outline/`로 쓰세요" / 기타

### Q2. 버킷 CORS 설정이 이미 있는지

Outline은 브라우저에서 S3로 직접 PUT 업로드합니다. CORS 필요.

```json
[
  {
    "AllowedHeaders": ["*"],
    "AllowedMethods": ["GET", "PUT", "POST", "DELETE"],
    "AllowedOrigins": ["https://docsdev.oilbank.co.kr"],
    "ExposeHeaders": ["ETag"],
    "MaxAgeSeconds": 3000
  }
]
```

- 이미 위와 같이 설정돼 있으면 OK
- 없거나 다르면 → 추가/수정 요청

### Q3. 버킷 정책 (bucket policy) 별도 필요 여부

- 일부 환경은 bucket policy + IAM Role 양쪽 필요
- 어느 쪽인지 확인. IRSA만으로 충분한지

---

## 2-A. IRSA 동작 원리 (이해를 위한 설명)

회사가 S3 호스팅 방식 처음이라 IRSA 구조 정리. **자동/수동으로 나뉘는 부분 명확화.**

### 필요한 4가지 구성요소

| 구성요소 | 누가 셋업 | 자동/수동 |
|---------|---------|---------|
| 1. **IAM Role** (Trust Policy + Permission Policy) | 인프라팀 | 수동 |
| 2. **EKS OIDC Provider 등록** | 인프라팀 (1회성) | 수동 |
| 3. **ServiceAccount 어노테이션** | 인프라팀 또는 본인 | 수동 |
| 4. **`AWS_WEB_IDENTITY_TOKEN_FILE` 토큰** | EKS Pod Identity webhook | **자동** |

→ 1~3만 셋업하면 4번은 EKS가 자동 처리. **별도 토큰 발급/관리 작업 없음.**

### 전체 흐름

```
[셋업 단계 — 수동]
1. EKS OIDC Provider 등록 (인프라팀, 1회성)
   ↓ EKS 클러스터 ↔ IAM 연동

2. IAM Role 생성 (인프라팀)
   - Trust Policy: "docs-dev/docs-outline-dev SA가 이 Role을 Assume 가능"
   - Permission Policy: S3 권한
   ↓

3. ServiceAccount에 어노테이션 추가 (인프라팀 또는 본인)
   eks.amazonaws.com/role-arn: arn:aws:iam::691729631040:role/...
   ↓

4. Pod 재시작

[Pod 실행 시 — 자동]
5. EKS Pod Identity webhook이 Pod 생성 감지
   - ServiceAccount 어노테이션 확인 → IAM Role 매핑 발견
   - Pod에 자동 주입:
     · 환경변수: AWS_ROLE_ARN, AWS_WEB_IDENTITY_TOKEN_FILE, AWS_REGION
     · 파일: /var/run/secrets/eks.amazonaws.com/serviceaccount/token
   ↓

6. AWS SDK가 Pod 안에서 자동으로:
   - 토큰 파일 읽음
   - STS에 AssumeRoleWithWebIdentity 호출
   - 임시 자격증명 받음 (15분 단위 자동 갱신)
   ↓

7. S3 API 호출 정상 (presigned URL 생성 가능)
```

### EKS OIDC Provider 등록 확인 (사전 점검)

IRSA가 동작하려면 **EKS 클러스터에 IAM OIDC Provider가 연결돼 있어야** 함:

```bash
# 인프라팀 확인 명령
aws eks describe-cluster --name <클러스터명> \
  --query 'cluster.identity.oidc.issuer'
# 출력 예: https://oidc.eks.ap-northeast-2.amazonaws.com/id/ABC123...

# OIDC provider 등록됐는지
aws iam list-open-id-connect-providers
# 위 issuer URL이 결과에 있어야 함
```

미등록이면 IRSA 자체 동작 안 함. 등록은:
```bash
eksctl utils associate-iam-oidc-provider \
  --cluster <클러스터명> \
  --approve
```

→ **dify가 IRSA 안 쓰면 이 OIDC provider 미등록 상태일 수 있음.** 인프라팀에 확인 요청.

---

## 3. 인프라팀에 요청 사항

### R0. EKS OIDC Provider 등록 확인 (사전 점검)

dify가 IRSA 안 쓰고 있을 가능성이라 OIDC provider 미등록일 수도. 먼저 확인:

```bash
aws iam list-open-id-connect-providers
```

→ 등록 안 됐으면:
```bash
eksctl utils associate-iam-oidc-provider --cluster <클러스터명> --approve
```

이건 1회성. 클러스터 단위 셋업. R1 진행 전 필수.

### R1. IAM Role 생성 (IRSA용)

```
이름: docs-outline-dev-s3-access (또는 회사 명명규칙)
```

**Trust Policy** (어떤 SA가 이 Role을 Assume 할 수 있는지):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::691729631040:oidc-provider/<EKS OIDC issuer>"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "<EKS OIDC issuer>:sub": "system:serviceaccount:docs-dev:docs-outline-dev",
          "<EKS OIDC issuer>:aud": "sts.amazonaws.com"
        }
      }
    }
  ]
}
```

> `<EKS OIDC issuer>`는 인프라팀이 클러스터 정보로 채움

**Permission Policy** (이 Role이 가질 권한):

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
        "arn:aws:s3:::hdo-s3-dev-an2-bao-691729631040-ap-northeast-2-an",
        "arn:aws:s3:::hdo-s3-dev-an2-bao-691729631040-ap-northeast-2-an/*"
      ]
    }
  ]
}
```

> 만약 prefix 사용이면 Resource를 `/outline/*` 같이 좁힐 수 있음 (Q1 답변 후 결정)

**작업 완료 후 알려주실 것:**
- 생성된 Role의 **ARN** (예: `arn:aws:iam::691729631040:role/docs-outline-dev-s3-access`)

### R2. ServiceAccount 어노테이션 추가

`docs-dev` 네임스페이스의 `docs-outline-dev` ServiceAccount에 다음 annotation 추가:

```yaml
metadata:
  annotations:
    eks.amazonaws.com/role-arn: arn:aws:iam::691729631040:role/<R1에서 받은 Role 이름>
```

본인이 Accordion에서 직접 가능하면 ARN만 받아서 직접 추가. 권한 없으면 인프라팀에 요청.

### R3. 버킷 CORS 설정 (Q2 답변에 따라)

Q2에서 CORS가 없거나 다른 도메인만 허용하면 추가:

```json
[
  {
    "AllowedHeaders": ["*"],
    "AllowedMethods": ["GET", "PUT", "POST", "DELETE"],
    "AllowedOrigins": ["https://docsdev.oilbank.co.kr"],
    "ExposeHeaders": ["ETag"],
    "MaxAgeSeconds": 3000
  }
]
```

---

## 4. dify 패턴 카피 (참고)

dify가 같은 방식으로 S3를 쓰고 있다면 같은 셋업 카피하면 일관성 좋습니다.

확인할 것:
- dify-api의 IRSA 어노테이션 (`eks.amazonaws.com/role-arn`)
- dify-api의 S3 관련 환경변수
- dify가 같은 버킷 + 다른 prefix 사용?

---

## 5. 본인이 할 작업 (위 정보 받은 후)

### Accordion에서 docs-outline-dev 환경변수 추가/확인

```
FILE_STORAGE              = s3
AWS_REGION                = ap-northeast-2
AWS_S3_UPLOAD_BUCKET_NAME = hdo-s3-dev-an2-bao-691729631040-ap-northeast-2-an
AWS_S3_UPLOAD_BUCKET_URL  = https://s3.ap-northeast-2.amazonaws.com
AWS_S3_FORCE_PATH_STYLE   = false
AWS_S3_UPLOAD_MAX_SIZE    = 262144000
# IRSA로 권한 → AWS_ACCESS_KEY_ID/SECRET_ACCESS_KEY 불필요
```

(prefix 받으면 추가: `AWS_S3_UPLOAD_PREFIX = outline/` — Outline 1.7.1이 지원하면)

### ServiceAccount 어노테이션 확인 (인프라팀이 안 했으면 본인이)

- Accordion → `docs-dev` 네임스페이스 → ServiceAccounts → `docs-outline-dev`
- Annotations에 `eks.amazonaws.com/role-arn: arn:aws:iam::...:role/...` 박혀 있는지

### Pod 재시작 후 검증 — 3단계

#### 1) 환경변수 자동 주입 확인

Pod 터미널에서:

```bash
env | grep AWS_
```

**기대 출력:**
```
AWS_ROLE_ARN=arn:aws:iam::691729631040:role/docs-outline-dev-s3-access
AWS_WEB_IDENTITY_TOKEN_FILE=/var/run/secrets/eks.amazonaws.com/serviceaccount/token
AWS_REGION=ap-northeast-2
AWS_DEFAULT_REGION=ap-northeast-2
```

위 4개 환경변수가 **EKS Pod Identity webhook이 자동 주입**한 것. 본인이 환경변수 추가 안 했어도 보여야 함.

| 결과 | 진단 |
|------|------|
| 4개 다 보임 | IRSA 셋업 OK → 다음 단계로 |
| 아무것도 안 보임 | ServiceAccount 어노테이션 미적용 or Pod이 다른 SA 사용 |
| AWS_REGION만 보임 | 본인이 env 박은 것만 있고 IRSA는 미동작 |

#### 2) 토큰 파일 존재 확인

```bash
ls -la /var/run/secrets/eks.amazonaws.com/serviceaccount/token
cat /var/run/secrets/eks.amazonaws.com/serviceaccount/token | head -c 50
```

**기대:** 파일 존재 + JWT 토큰 시작 (`eyJ...`).

없으면 → EKS Pod Identity webhook 동작 안 함. 인프라팀 확인.

#### 3) STS로 자격증명 받기 테스트

실제로 AWS API 호출이 되는지 검증:

```bash
node -e "
const { STSClient, AssumeRoleWithWebIdentityCommand } = require('@aws-sdk/client-sts');
const fs = require('fs');
(async () => {
  const token = fs.readFileSync(process.env.AWS_WEB_IDENTITY_TOKEN_FILE, 'utf8');
  const sts = new STSClient({ region: 'ap-northeast-2' });
  const res = await sts.send(new AssumeRoleWithWebIdentityCommand({
    RoleArn: process.env.AWS_ROLE_ARN,
    RoleSessionName: 'test',
    WebIdentityToken: token,
  }));
  console.log('OK:', res.Credentials?.AccessKeyId?.substring(0, 5) + '...');
})().catch(e => console.log('ERR:', e.message));
"
```

| 결과 | 진단 |
|------|------|
| `OK: AKIA...` | IRSA 완전 동작 → Outline에서 업로드 가능해야 함 |
| `ERR: AccessDenied` | Trust Policy 잘못 (SA 이름·OIDC issuer 불일치) |
| `ERR: InvalidIdentityToken` | 토큰 파일 손상 또는 만료 |
| `ERR: ...` | 메시지 따라 디버깅 |

### 동작 검증

- Outline 워크스페이스 설정 → 로고 업로드 시도 → 성공 시 S3에 파일 생성됨
- AWS 콘솔에서 버킷 안 객체 확인 (생성 시각 + 크기 확인)
- 브라우저 DevTools Network 탭에서 S3 PUT 요청 200 OK 확인

---

## 6. 진행 흐름 요약

```
[지금]
1. 위 메시지 인프라팀에 전달
   - Q1, Q2, Q3 확인 요청
   - R1, R2, R3 작업 요청

[답변 받은 후]
2. Role ARN 받기 → ServiceAccount에 annotation 추가
3. Outline 환경변수 확인 (이미 박혀 있을 가능성 높음)
4. Pod 재시작
5. 업로드 검증

[운영 전환 시]
- 로컬·dev에서 동작 검증되면 prod에선 별도 버킷 + 별도 Role
```

---

## 7. 트러블슈팅 — 셋업 후에도 실패 시

| 증상 | 원인 | 대응 |
|------|------|------|
| `CredentialsProviderError` 계속 | IRSA annotation 미적용 또는 Pod이 다른 SA 사용 | Pod의 serviceAccountName 확인, env에 AWS_ROLE_ARN 있는지 |
| `env`에 AWS_ROLE_ARN 안 보임 | ServiceAccount 어노테이션 누락 | Accordion에서 어노테이션 확인, Pod 재시작 |
| `토큰 파일 없음` | EKS Pod Identity webhook 미동작 | 인프라팀에 EKS 설정 확인 요청 |
| `STS AccessDenied` | Trust Policy 불일치 (SA 이름·네임스페이스·OIDC issuer) | Role의 Trust Policy 확인 |
| `STS InvalidIdentityToken` | OIDC Provider 미등록 또는 토큰 만료 | `aws iam list-open-id-connect-providers` 확인 |
| `Access Denied` (S3) | IAM Permission Policy 권한 부족 | Resource ARN 정확한지, Action 빠진 거 없는지 |
| `CORS error` (브라우저) | 버킷 CORS 미설정 | Q2 결과 따라 추가 |
| `Bucket does not exist` | 버킷명 오타 | env 값과 실제 버킷명 비교 |

## 8. 단계별 진단 흐름

```
[Outline 업로드 시도]
     ↓
   에러?
     ↓
   CredentialsProviderError?
     │
     ├─ Yes → env | grep AWS_ 결과:
     │   ├─ AWS_ROLE_ARN 없음 → ServiceAccount 어노테이션 미적용
     │   │   → Accordion에서 어노테이션 추가 → Pod 재시작
     │   └─ AWS_ROLE_ARN 있음 → 토큰 파일 확인
     │       ├─ 없음 → EKS webhook 이슈 (인프라팀)
     │       └─ 있음 → STS 테스트
     │           ├─ AccessDenied → Trust Policy 잘못
     │           └─ OK → AWS SDK 캐싱 이슈, Pod 재시작
     │
     └─ No (다른 에러) → 메시지 따라:
         ├─ Access Denied (S3) → IAM Permission 부족
         ├─ CORS error → 버킷 CORS 미설정
         └─ Network error → VPC/SG 확인
```

## 9. 한 줄 요약

> IAM Role(Trust + Permission) + ServiceAccount 어노테이션 + (사전) EKS OIDC Provider 등록 + S3 CORS.
> 이 4가지 셋업되면 **토큰·환경변수 주입·STS 갱신은 EKS가 자동** 처리.
