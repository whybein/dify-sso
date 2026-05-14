# Outline 배포·운영 가이드 (dev)

회사 K8s(EKS, Accordion 관리)에 Outline 위키를 배포한 구성 정리.
**대상**: 인프라/운영 담당자, 추후 prod 환경 구축 시 참고용.

- 서비스 주소: https://docsdev.oilbank.co.kr
- 환경: dev (`docs-dev` 네임스페이스)
- 이미지: `outlinewiki/outline:1.7.1`

> 첫 배포 절차: `OUTLINE_DEPLOYMENT.md` | S3/IRSA 설정: `S3_IRSA_REQUEST.md`

---

## 1. 아키텍처 개요

```
[사용자 브라우저]
    ↓ HTTPS
[ALB (internal, dify와 공유)]
    ↓ docs.oilbank.co.kr 호스트 라우팅
[K8s Service: docs-outline-dev:3000]
    ↓
[Pod: docs-outline-dev (Node.js + Koa)]
    ├── OIDC 인증 → Authelia (llm-dev/authelia-dev)
    │                  └── LDAP 인증
    ├── DB → RDS PostgreSQL (outline database)
    ├── Cache/Queue → Redis (llm-dev/redis-dev:6379/3)
    └── 파일 저장 → S3 (IRSA로 인증)
                     ↑
              브라우저가 presigned URL로 직접 업로드 (CORS 필요)
```

---

## 2. 인프라 구성 요약

| 항목 | 값 | 비고 |
|------|-----|------|
| 클러스터 | EKS (Accordion 관리) | dify와 동일 클러스터 |
| 네임스페이스 | `docs-dev` | 신규 (전용) |
| 워크로드 | `docs-outline-dev` (Deployment, replica=1) | Outline 단일 인스턴스 권장 |
| ServiceAccount | `docs-dev/default` | IRSA 어노테이션 부착 |
| 도메인 | `docsdev.oilbank.co.kr` | Route53 → 기존 internal ALB Alias |
| ALB | dify와 공유 | `group.name`으로 묶음, 새 LB 안 띄움 |
| TLS | 기존 와일드카드 ACM | `*.oilbank.co.kr` |
| 이미지 | `outlinewiki/outline:1.7.1` | 외부 Docker Hub 직접 pull (AMD64) |
| DB | RDS PostgreSQL | dify 인스턴스에 `outline` database 추가 |
| Cache/Queue | Redis | dify의 `redis-dev` 공유, DB index `/3` |
| 파일 저장 | AWS S3 | `hdo-s3-dev-an2-bao-...` |
| 인증 | OIDC | Authelia (`llm-dev/authelia-dev`) |

---

## 3. 첫 배포

첫 배포 시 필요한 전체 단계는 별도 문서 참고:

- **[OUTLINE_DEPLOYMENT.md](./OUTLINE_DEPLOYMENT.md)** — 전체 배포 순서 (RDS, Authelia OIDC, Route53, K8s 워크로드, 환경변수, 검증)
- **[S3_IRSA_REQUEST.md](./S3_IRSA_REQUEST.md)** — S3 버킷 + IAM Role + IRSA + CORS 상세 설정

---

## 4. 운영 작업

### 이미지 업그레이드

```
1. Accordion → docs-outline-dev → Workload 편집
2. 이미지 태그 변경 (예: 1.7.1 → 1.7.2)
3. 저장 → 자동 재배포
4. Pod 로그 확인 — DB 마이그레이션 자동 실행됨
5. 정상 응답 확인 후 사용 재개
```

> 메이저 버전 업그레이드 시 release notes 확인. DB 마이그레이션은 자동.

### Pod 재시작

환경변수 변경 또는 ConfigMap 변경 후:
```
Accordion → 애플리케이션 → 카탈로그 → "배포하기"
```

### 환경변수 변경

```
Accordion → 애플리케이션 → 카탈로그 → 수정 → 배포리소스 설정 탭 → "환경변수" 열고 추가/수정/삭제
→ 이후 Pod 재시작
```

### 로그 확인

```
Accordion → 네임스페이스(docs-dev) → 워크로드 → 파드 → 로그
```

검색할 키워드:
- `oidc`, `auth`: 인증 흐름
- `s3`, `attachment`: 파일 업로드
- `smtp`, `email`: 메일 발송
- `error`, `Error`: 일반 에러

### DB 백업

RDS 자동 백업 정책에 의존 (dify와 동일 인스턴스).
별도 outline 데이터만 백업하려면:

```bash
pg_dump -h <RDS> -U dify -d outline > outline-backup-$(date +%Y%m%d).sql
```

### Outline 자체 export (사용자 데이터)

UI에서 `Settings → Export workspace` (Admin만 가능). JSON ZIP 형식.

---

## 5. 트러블슈팅

### 인증 흐름

| 증상 | 원인 | 대응 |
|------|------|------|
| `redirect_uri mismatch` | Authelia redirect_uris와 불일치 | Authelia client config 확인 |
| `invalid_scope: offline_access` | scope 미허용 | Authelia client `scopes`에 offline_access 추가 |
| `code_challenge missing` | PKCE 강제 | Authelia client `require_pkce: false` |
| 동의 화면 매번 표시 | offline_access scope 사용 + storage 영속화 미완 | Authelia storage를 RDS PostgreSQL로 전환 (별도 작업) |
| `/api/auth.info` 401 | offline_access scope 누락 | OIDC_SCOPES에 offline_access 추가 |

### S3 파일 업로드

| 증상 | 원인 | 대응 |
|------|------|------|
| `CredentialsProviderError` | IRSA 미적용 | ServiceAccount 어노테이션 + Pod 재시작 |
| `Access Denied` | IAM Policy 권한 부족 | Role의 Permission Policy 확인 |
| `blocked by CORS policy` (브라우저) | S3 버킷 CORS 미설정 | S3 콘솔 → Permissions → CORS 추가 |
| `Bucket does not exist` | 버킷명 오타 | 환경변수 값 재확인 |

### 메일

| 증상 | 원인 | 대응 |
|------|------|------|
| `421 4.3.2 Service not available` | SMTP relay가 Pod IP 거부 | 메일팀에 IP 허용 또는 SMTP AUTH 발급 요청 |
| `wrong version number` (TLS) | 포트와 SECURE 설정 불일치 | 587 → SECURE=false (STARTTLS), 465 → SECURE=true |

### 일반

| 증상 | 원인 | 대응 |
|------|------|------|
| `SECRET_KEY must be 64 hexadecimal characters` | env 값 길이 부족 | `openssl rand -hex 32` 결과로 다시 박기 |
| `exec format error` (배포 시) | ARM64 이미지를 AMD64 노드에 띄움 | `crane pull --platform linux/amd64`로 재생성 |
| Pod CrashLoopBackOff | DB/Redis 연결 실패 | DATABASE_URL, REDIS_URL 형식과 도달 가능성 확인 |

---

## 6. 현재 제한 사항 (운영 관점)

### 메일 발송 미동작

- SMTP relay가 Pod NAT IP(`43.200.153.193`) 거부
- 영향: 초대 메일, 멘션/댓글 알림 발송 불가
- 우회: 사용자가 직접 URL 접속 → 도메인 자동 가입
- 해결 방안:
  1. 메일팀에 Pod NAT IP 허용 추가 요청 (진행 중)
  2. 또는 SMTP AUTH 계정 발급
  3. 또는 별도 발송용 relay 서버 사용

### 단일 Pod 운영

- replica=1 강제 (Outline 설계상 — SQLite collab state 등)
- 대규모 동시 협업 시 성능 한계
- prod 갈 때 검토 필요

### Authelia consent 매번 표시

- `consent_mode: pre-configured`로 1년 기억 설정했으나 매번 표시되는 케이스 있음
- 원인: Authelia storage가 SQLite + ConfigMap 마운트로 영속화 안 됨
- 해결: Authelia storage를 PostgreSQL로 전환 (별도 인프라 작업)

---

## 7. 관련 문서

- `OUTLINE_DEPLOYMENT.md` — 첫 배포 단계별 가이드 (RDS, Authelia, K8s, 환경변수)
- `S3_IRSA_REQUEST.md` — S3 + IRSA 셋업 상세
- `AUTHELIA_STORAGE_REQUEST.md` — Authelia storage 영속화 (예정)
- `OUTLINE_EXCALIDASH_TEST_GUIDE.md` — 로컬 테스트 가이드

---

## 8. 향후 작업

| 항목 | 우선순위 | 비고 |
|------|---------|------|
| 메일 발송 활성화 | 중 | 메일팀 협의 중 |
| Authelia storage 영속화 (RDS) | 중 | consent 매번 표시 문제 해결 |
| 2FA 활성화 | 낮 | Authelia 정책 통합 후 |
| RAG 연동 (Dify가 Outline 문서 조회) | 중 | API token + Weaviate 임베딩 |
| prod 환경 분리 | 높 (운영 전환 시) | 별도 RDS·S3·도메인 |
| 백업 자동화 | 중 | RDS 자동 백업 + S3 versioning |

---

## 9. 변경 이력

| 일자 | 내용 | 담당 |
|------|------|------|
| 2026-05-12 | 초기 배포 (dev) | - |
| 2026-05-13 | S3 IRSA 셋업 완료 | - |
| 2026-05-14 | 운영 가이드 문서화 | - |
