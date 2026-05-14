# Outline S3 + IRSA 셋업 가이드

Outline 첨부/이미지 업로드를 위한 AWS S3 + IRSA(IAM Role for ServiceAccount) 셋업 가이드.

대상 증상: Outline에서 이미지 업로드 시 `CredentialsProviderError: Could not load credentials from any providers` 발생.
원인: ServiceAccount에 IRSA 어노테이션 없음 → AWS SDK가 자격증명 못 찾음.

---

## 1. 환경 정보

| 항목 | 값 |
|------|-----|
| 환경 | dev |
| AWS 계정 ID | `691729631040` |
| 리전 | `ap-northeast-2` |
| S3 버킷 | `hdo-s3-dev-an2-bao-691729631040-ap-northeast-2-an` |
| 버킷 ARN | `arn:aws:s3:::hdo-s3-dev-an2-bao-691729631040-ap-northeast-2-an` |
| K8s 네임스페이스 | `docs-dev` |
| ServiceAccount | `docs-outline-dev` |
| Outline 도메인 | `https://docsdev.oilbank.co.kr` |

---

## 2. Outline의 S3 사용 방식

Outline 백엔드는 **Node.js + AWS SDK v3** (`@aws-sdk/client-s3`, `@aws-sdk/s3-presigned-post`) 사용.

업로드 흐름:

```
1. [브라우저] 이미지 첨부 클릭
   ↓ POST /api/attachments.create

2. [Outline 서버] AWS SDK로 presigned POST URL 생성
   - 자격증명: IRSA로 받은 임시 STS 토큰
   - 서버→S3 SDK 호출 (CORS 무관)
   ↓ JSON 응답 (presigned URL + 필드)

3. [브라우저] presigned URL로 S3에 직접 PUT
   - 브라우저→S3 (cross-origin → CORS 검사)
   - S3가 CORS AllowedOrigins 확인

4. [S3] 파일 저장 후 200 응답

5. [브라우저 → Outline 서버] 업로드 완료 알림 → DB에 메타데이터 저장
```

→ **2단계는 IRSA(서버↔S3), 3단계는 CORS(브라우저↔S3) 둘 다 필요.**

---

## 3. IRSA 동작 원리

### 필요한 4가지 구성요소

| 구성요소 | 수동/자동 |
|---------|---------|
| 1. EKS OIDC Provider 등록 (클러스터 단위, 1회성) | 수동 |
| 2. IAM Role (Trust Policy + Permission Policy) | 수동 |
| 3. ServiceAccount 어노테이션 | 수동 |
| 4. `AWS_WEB_IDENTITY_TOKEN_FILE` 토큰 + 환경변수 주입 | **자동** (EKS Pod Identity webhook) |

→ 1~3만 셋업하면 4번은 EKS가 자동 처리. 토큰 발급/갱신/주입은 별도 작업 없음.

### 전체 흐름

```
[셋업 — 수동]
1. EKS OIDC Provider 등록 (1회성)
   ↓ EKS 클러스터 ↔ IAM 연동

2. IAM Role 생성
   - Trust Policy: 특정 ServiceAccount만 AssumeRoleWithWebIdentity 허용
   - Permission Policy: S3 권한
   ↓

3. ServiceAccount 어노테이션 추가
   eks.amazonaws.com/role-arn: arn:aws:iam::691729631040:role/...
   ↓

4. Pod 재시작

[Pod 실행 시 — 자동]
5. EKS Pod Identity webhook:
   - ServiceAccount 어노테이션 감지 → IAM Role 매핑
   - Pod에 자동 주입:
     · 환경변수: AWS_ROLE_ARN, AWS_WEB_IDENTITY_TOKEN_FILE, AWS_REGION
     · 파일: /var/run/secrets/eks.amazonaws.com/serviceaccount/token (JWT)

6. AWS SDK가 자동으로:
   - 토큰 파일 읽음
   - STS에 AssumeRoleWithWebIdentity 호출
   - 임시 자격증명 받음 (15분 단위 자동 갱신)

7. S3 API 호출 정상 (presigned URL 생성 가능)
```

---

## 4. 셋업 방법

### 4.1 EKS OIDC Provider 등록 확인 (사전 점검)

IRSA가 동작하려면 EKS 클러스터에 IAM OIDC Provider 연결돼 있어야 함.

#### AWS 콘솔 방법

1. EKS 콘솔 → 클러스터 → "Overview" 또는 "Configuration" 탭
2. "OpenID Connect provider URL" 값 복사 (예: `https://oidc.eks.ap-northeast-2.amazonaws.com/id/ABC123...`)
3. IAM 콘솔 → "Identity providers" 메뉴
4. 위 URL이 목록에 있는지 확인
   - 있음 → 그대로 진행
   - 없음 → "Add provider" 클릭
     - Type: OpenID Connect
     - Provider URL: 위에서 복사한 URL
     - Audience: `sts.amazonaws.com`
     - "Add provider"

#### CLI 방법 (참고)

```bash
aws eks describe-cluster --name <클러스터명> \
  --query 'cluster.identity.oidc.issuer'
aws iam list-open-id-connect-providers

# 미등록 시
eksctl utils associate-iam-oidc-provider \
  --cluster <클러스터명> --approve
```

→ 클러스터에 IRSA 사용 이력 없으면 이 단계 필수.

### 4.2 IAM Role 생성

#### Role 이름 (예시)
```
docs-outline-dev-s3-access
```

#### AWS 콘솔 단계

1. IAM 콘솔 → "Roles" → "Create role"
2. **Trusted entity type:** "Web identity"
3. **Identity provider:** 4.1에서 확인한 OIDC provider URL 선택
4. **Audience:** `sts.amazonaws.com`
5. "Add condition" 없이 Next → Permissions 단계
6. **Permission Policy 생성:**
   - "Create policy" 클릭 (별도 탭 열림)
   - JSON 탭 → 아래 Permission Policy 붙여넣기
   - 이름: `docs-outline-dev-s3-access-policy`
   - "Create policy"
7. 원래 탭으로 → 방금 만든 정책 검색해서 선택 → Next
8. **Role name:** `docs-outline-dev-s3-access`
9. "Create role"
10. **Trust Policy 수정:**
    - 생성된 Role 클릭 → "Trust relationships" 탭 → "Edit trust policy"
    - 아래 Trust Policy JSON으로 교체
    - "Update policy"
11. 완료 후 Role의 ARN 복사 (예: `arn:aws:iam::691729631040:role/docs-outline-dev-s3-access`)

#### Trust Policy

특정 ServiceAccount만 이 Role을 Assume 가능하도록:

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

`<EKS OIDC issuer>` 부분은 4.1에서 확인한 issuer URL의 `https://` 제외한 부분.

예시 (실제 값으로):
```json
"Federated": "arn:aws:iam::691729631040:oidc-provider/oidc.eks.ap-northeast-2.amazonaws.com/id/ABC123"
"oidc.eks.ap-northeast-2.amazonaws.com/id/ABC123:sub": "system:serviceaccount:docs-dev:docs-outline-dev"
"oidc.eks.ap-northeast-2.amazonaws.com/id/ABC123:aud": "sts.amazonaws.com"
```

#### Permission Policy

S3 버킷 read/write 권한:

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

> 공유 버킷이고 prefix(예: `outline/`) 사용 시 Resource를 `/outline/*`로 좁힐 수 있음.

생성 결과: **Role ARN** (예: `arn:aws:iam::691729631040:role/docs-outline-dev-s3-access`)

### 4.3 ServiceAccount 어노테이션 추가

`docs-dev` 네임스페이스의 `docs-outline-dev` ServiceAccount에:

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: docs-outline-dev
  namespace: docs-dev
  annotations:
    eks.amazonaws.com/role-arn: arn:aws:iam::691729631040:role/docs-outline-dev-s3-access
```

Accordion UI에서 추가하거나 직접 manifest 수정.

### 4.4 S3 버킷 CORS 설정

브라우저가 S3에 직접 PUT 업로드하므로 CORS 필수.

#### AWS 콘솔 단계

1. S3 콘솔 → 버킷 `hdo-s3-dev-an2-bao-691729631040-ap-northeast-2-an`
2. "Permissions" 탭 클릭
3. 아래로 스크롤 → "Cross-origin resource sharing (CORS)" 섹션
4. "Edit" 클릭
5. 아래 JSON 붙여넣기
6. "Save changes"

#### CORS JSON

```json
[
  {
    "AllowedHeaders": ["*"],
    "AllowedMethods": ["GET", "PUT", "POST", "DELETE", "HEAD"],
    "AllowedOrigins": ["https://docsdev.oilbank.co.kr"],
    "ExposeHeaders": ["ETag"],
    "MaxAgeSeconds": 3000
  }
]
```

운영 도메인 추가 시:
```json
"AllowedOrigins": [
  "https://docsdev.oilbank.co.kr",
  "https://docs.oilbank.co.kr"
]
```

### 4.5 (선택) S3 Bucket Policy

IAM Role만으로 충분하지만 보안 강화 시:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowOutlineRole",
      "Effect": "Allow",
      "Principal": {
        "AWS": "arn:aws:iam::691729631040:role/docs-outline-dev-s3-access"
      },
      "Action": [
        "s3:PutObject", "s3:PutObjectAcl", "s3:GetObject",
        "s3:DeleteObject", "s3:ListBucket", "s3:GetBucketLocation"
      ],
      "Resource": [
        "arn:aws:s3:::hdo-s3-dev-an2-bao-691729631040-ap-northeast-2-an",
        "arn:aws:s3:::hdo-s3-dev-an2-bao-691729631040-ap-northeast-2-an/*"
      ]
    },
    {
      "Sid": "DenyInsecureTransport",
      "Effect": "Deny",
      "Principal": "*",
      "Action": "s3:*",
      "Resource": [
        "arn:aws:s3:::hdo-s3-dev-an2-bao-691729631040-ap-northeast-2-an",
        "arn:aws:s3:::hdo-s3-dev-an2-bao-691729631040-ap-northeast-2-an/*"
      ],
      "Condition": {
        "Bool": {
          "aws:SecureTransport": "false"
        }
      }
    }
  ]
}
```

### 4.6 Pod 재시작

ServiceAccount 어노테이션 변경은 새 Pod에만 적용됨. 기존 Pod 재시작 필수.

### 4.7 Outline 환경변수

`docs-outline-dev` 워크로드에 다음 환경변수 설정:

```
FILE_STORAGE              = s3
AWS_REGION                = ap-northeast-2
AWS_S3_UPLOAD_BUCKET_NAME = hdo-s3-dev-an2-bao-691729631040-ap-northeast-2-an
AWS_S3_UPLOAD_BUCKET_URL  = https://s3.ap-northeast-2.amazonaws.com
AWS_S3_FORCE_PATH_STYLE   = false
AWS_S3_UPLOAD_MAX_SIZE    = 10485760    # 10 MB (필요 따라 조정)
```

`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`는 IRSA가 처리하므로 **설정 안 함.**

---

## 5. 검증

Pod 터미널에서 3단계 검증.

### 5.1 환경변수 자동 주입 확인

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

위 4개가 보이지 않으면 IRSA 셋업 미완료 (4.3 어노테이션 누락 또는 Pod 재시작 안 됨).

### 5.2 토큰 파일 존재 확인

```bash
ls -la /var/run/secrets/eks.amazonaws.com/serviceaccount/token
cat /var/run/secrets/eks.amazonaws.com/serviceaccount/token | head -c 50
```

기대: 파일 존재 + JWT 토큰 시작 (`eyJ...`).

### 5.3 STS 자격증명 발급 테스트

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

| 결과 | 의미 |
|------|------|
| `OK: AKIA...` | IRSA 완전 동작 → 다음 검증 |
| `ERR: AccessDenied` | Trust Policy 불일치 (SA 이름/네임스페이스/OIDC issuer) |
| `ERR: InvalidIdentityToken` | OIDC Provider 미등록 또는 토큰 만료 |

### 5.4 실제 업로드 동작 검증

1. Outline 워크스페이스 설정 → 로고 업로드 또는 문서 본문에 이미지 삽입
2. 브라우저 DevTools → Network 탭 → S3 도메인으로의 PUT 요청 200 OK 확인
3. AWS 콘솔 → S3 버킷 → 객체 목록에 새 파일 보이는지 확인

---

## 6. 트러블슈팅

| 증상 | 원인 | 대응 |
|------|------|------|
| `CredentialsProviderError` | IRSA 어노테이션 미적용 또는 Pod이 다른 SA 사용 | Pod의 `serviceAccountName` 확인, env에 `AWS_ROLE_ARN` 있는지 |
| `env`에 `AWS_ROLE_ARN` 안 보임 | ServiceAccount 어노테이션 누락 | 어노테이션 추가, Pod 재시작 |
| 토큰 파일 없음 | EKS Pod Identity webhook 미동작 | EKS 클러스터 OIDC provider 등록 확인 |
| `STS AccessDenied` | Trust Policy 불일치 | Trust Policy의 SA 이름·네임스페이스·OIDC issuer 재확인 |
| `STS InvalidIdentityToken` | OIDC Provider 미등록 또는 클러스터 OIDC URL 불일치 | `aws iam list-open-id-connect-providers` 확인 |
| `Access Denied` (S3 PutObject) | IAM Permission Policy 권한 부족 | Resource ARN·Action 재확인 |
| `CORS error` (브라우저) | 버킷 CORS 미설정 | 4.4 적용 |
| `Bucket does not exist` | 버킷명 오타 | env 값과 실제 버킷명 비교 |

### 단계별 진단 흐름

```
[Outline 업로드 시도]
     ↓
   에러?
     ↓
   CredentialsProviderError?
     │
     ├─ Yes → env | grep AWS_ 결과:
     │   ├─ AWS_ROLE_ARN 없음 → ServiceAccount 어노테이션 미적용
     │   │   → 어노테이션 추가 → Pod 재시작
     │   └─ AWS_ROLE_ARN 있음 → 토큰 파일 확인
     │       ├─ 없음 → EKS webhook 이슈 (OIDC provider 확인)
     │       └─ 있음 → STS 테스트
     │           ├─ AccessDenied → Trust Policy 잘못
     │           └─ OK → AWS SDK 캐싱, Pod 재시작
     │
     └─ No (다른 에러) →
         ├─ Access Denied (S3) → IAM Permission 부족
         ├─ CORS error → 버킷 CORS 미설정
         └─ Network error → VPC/SG 확인
```

---

## 7. 요약

| 셋업 항목 | 결과 |
|---------|------|
| EKS OIDC Provider 등록 | 클러스터 IRSA 사용 가능 |
| IAM Role (Trust + Permission) | 특정 SA가 S3 접근 가능 |
| ServiceAccount 어노테이션 | Pod에 IRSA 환경변수 자동 주입 |
| S3 버킷 CORS | 브라우저 직접 업로드 허용 |
| Pod 재시작 | 새 환경변수 반영 |

토큰 발급·갱신·환경변수 주입은 EKS Pod Identity webhook이 자동 처리.
