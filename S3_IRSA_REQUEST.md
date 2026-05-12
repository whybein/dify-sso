# S3 + IRSA 셋업 요청 (Outline)

Outline 첨부/이미지 업로드용 S3 + IRSA 셋업을 인프라팀에 요청하기 위한 정리 문서.

증상: Outline에서 이미지 업로드 시 `CredentialsProviderError: Could not load credentials from any providers`.
원인: ServiceAccount에 IRSA 어노테이션 없음 → AWS SDK가 자격증명 못 찾음.

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

## 3. 인프라팀에 요청 사항

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

### Pod 재시작 후 검증

Pod 터미널에서:

```bash
# IRSA 환경변수 자동 주입 확인
env | grep AWS_
# 기대:
# AWS_ROLE_ARN=arn:aws:iam::691729631040:role/...
# AWS_WEB_IDENTITY_TOKEN_FILE=/var/run/secrets/eks.amazonaws.com/serviceaccount/token

# 토큰 파일 존재 확인
ls -la /var/run/secrets/eks.amazonaws.com/serviceaccount/token
```

### 동작 검증

- Outline 워크스페이스 설정 → 로고 업로드 시도 → 성공 시 S3에 파일 생성됨
- AWS 콘솔에서 버킷 안 객체 확인

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
| `CredentialsProviderError` 계속 | IRSA annotation 미적용 또는 Pod이 다른 SA 사용 | Pod의 serviceAccountName 확인 |
| `Access Denied` | IAM Policy 권한 부족 | Resource ARN 정확한지, Action 빠진 거 없는지 |
| `CORS error` (브라우저) | 버킷 CORS 미설정 | Q2 결과 따라 추가 |
| `Bucket does not exist` | 버킷명 오타 | env 값과 실제 버킷명 비교 |
| Trust policy 거부 | OIDC issuer / SA 이름 오타 | Role의 trust relationship 확인 |
