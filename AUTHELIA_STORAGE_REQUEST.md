# Authelia Storage 영속화 요청

`llm-dev/authelia-dev`의 SQLite 저장소가 영속 저장 안 되는 문제 해결 요청.

증상:
- Outline OIDC consent 화면이 매 로그인마다 표시됨 (pre-configured + 1y 설정해도 효과 없음)
- TOTP 디바이스 등록 등 사용자 상태도 Pod 재시작 시 사라질 가능성

원인 (Authelia 시작 로그에서 확인):
```
chown: /config/configuration.yml: Read-only file system
chown: /config/db.sqlite3: Operation not permitted
```

→ Authelia가 SQLite을 `/config/db.sqlite3`에 쓰려는데 해당 경로 권한 문제로 영속 저장 안 됨.

---

## 1. 현재 설정

`llm-dev/authelia-config` ConfigMap:
```yaml
storage:
  local:
    path: '/config/db.sqlite3'
```

**문제점:**
- `/config`에 ConfigMap이 마운트되어 있어 일부 read-only
- 또는 PVC가 마운트돼 있어도 ownership/권한 안 맞음
- 결과: consent_mode: pre-configured 설정해도 저장 안 되어 매번 동의 화면 표시

---

## 2. 인프라팀에 확인 필요한 항목

### Q1. 현재 PVC가 어디에 마운트돼 있는지

Authelia에 PVC가 있다고 들었는데 마운트 경로가:
- `/config` (ConfigMap과 같은 경로)
- `/data` (다른 경로)
- 다른 경로
- 혹은 PVC 마운트 안 됨

확인:
```
Accordion → llm-dev/authelia-dev 워크로드 → Volumes 섹션
또는 Pod manifest의 volumes/volumeMounts
```

### Q2. ConfigMap과 PVC 마운트 충돌 여부

- ConfigMap이 `/config`에 마운트되면서 PVC를 가리는 구조인지
- 또는 ConfigMap이 subPath로 특정 파일만 마운트되고 나머지 `/config`는 PVC인지

### Q3. Authelia Pod의 user ID와 PVC 권한

- chown 실패 = Authelia 프로세스 user(보통 uid 100 또는 1000)가 PVC 디렉토리 소유권 변경 권한 없음
- PVC 마운트 시 fsGroup이나 SecurityContext로 권한 자동 부여 가능

---

## 3. 해결 옵션 (택1, 인프라팀 협의)

### 옵션 A. SQLite을 별도 writable PVC로 이동 (간단)

기존 PVC를 `/data` 같은 경로로 마운트 (또는 신규 PVC):

```yaml
# Authelia 워크로드 volumeMounts
volumeMounts:
  - name: config
    mountPath: /config         # ConfigMap (read-only)
  - name: data
    mountPath: /data           # PVC (writable) ← 신규 또는 이동
```

ConfigMap storage 섹션 변경:
```yaml
storage:
  local:
    path: '/data/db.sqlite3'   # /config → /data
```

**필요한 작업:**
- PVC 마운트 경로 `/data`로 (기존 PVC 활용 또는 신규 1Gi)
- fsGroup 또는 SecurityContext로 Authelia user에 권한 부여
- ConfigMap의 storage.local.path 변경
- Pod 재시작

### 옵션 B. PostgreSQL (RDS) 사용 ⭐ 장기 권장

dify가 쓰는 RDS에 authelia 전용 database 생성:

```sql
CREATE DATABASE authelia OWNER dify;
```

ConfigMap storage 섹션 변경:
```yaml
storage:
  postgres:
    address: 'tcp://<RDS엔드포인트>:5432'
    database: 'authelia'
    username: 'dify'
    schema: 'public'
    timeout: '5s'
```

비밀번호는 env로 주입:
```
AUTHELIA_STORAGE_POSTGRES_PASSWORD = <dify 비번>
```

**장점:**
- 영속화 안전성 최고 (RDS 자동 백업)
- Pod 재시작/이동 무관
- HA 가능 (장기적)
- consent, TOTP 등 모든 상태 영구 보존

**필요한 작업:**
- RDS에 authelia DB 생성 (본인 권한 가능)
- ConfigMap storage 섹션 변경
- AUTHELIA_STORAGE_POSTGRES_PASSWORD env 추가
- Pod 재시작

### 옵션 C. ConfigMap을 subPath로 마운트 + 같은 PVC 활용 (기존 PVC 그대로)

PVC가 이미 `/config`에 마운트돼 있는 경우, ConfigMap을 subPath로 특정 파일만 덮어씌우는 패턴:

```yaml
volumeMounts:
  - name: config-files
    mountPath: /config/configuration.yml
    subPath: configuration.yml
  - name: config-files
    mountPath: /config/users_database.yml
    subPath: users_database.yml
  - name: pvc-data
    mountPath: /config        # PVC가 /config 전체 차지. ConfigMap은 특정 파일만 덮어씌움
```

→ SQLite는 PVC 영역에 저장 가능, ConfigMap 파일은 read-only.

**복잡도 중. 옵션 B (RDS)가 더 단순하고 안전.**

---

## 4. 권장 — 옵션 B (PostgreSQL)

이유:
1. 가장 안정적·영속적
2. dify와 같은 RDS 인스턴스라 인프라 추가 비용 없음
3. PVC ownership/권한 이슈 신경 안 써도 됨
4. consent, TOTP, password reset token 등 모든 상태 보존
5. 장기적으로 HA·백업 정책 적용 쉬움

---

## 5. 인프라팀에 보낼 통합 메시지

```
[Authelia storage 영속화 요청] llm-dev/authelia-dev

현재 storage가 SQLite (/config/db.sqlite3)인데 권한 문제로 영속 저장 안 됨.
시작 로그에 chown 실패 확인:
  chown: /config/configuration.yml: Read-only file system
  chown: /config/db.sqlite3: Operation not permitted

영향:
- Outline OIDC consent 화면이 pre-configured 설정해도 매 로그인마다 표시
- TOTP 등록 등 사용자 상태도 영속 보장 안 됨 (Pod 재시작 시 사라질 위험)

확인 요청:
1. 현재 PVC가 마운트돼 있다면 어느 경로? (/config? /data?)
2. ConfigMap과 PVC 마운트 구조

요청 (택1):

옵션 A — PVC 마운트 경로 변경
- PVC를 /data로 마운트 (또는 신규 1Gi)
- ConfigMap의 storage.local.path를 '/data/db.sqlite3'로 변경
- fsGroup 설정으로 Authelia user에 PVC 권한 부여

옵션 B — PostgreSQL로 전환 (권장)
- 기존 RDS에 authelia 전용 database 생성: CREATE DATABASE authelia OWNER dify;
- ConfigMap의 storage 섹션을 postgres로 변경:
    storage:
      postgres:
        address: 'tcp://<RDS>:5432'
        database: 'authelia'
        username: 'dify'
- Secret로 AUTHELIA_STORAGE_POSTGRES_PASSWORD 주입 (dify와 동일 비번)

옵션 B가 운영 안정성·인프라 일관성·유지보수성 면에서 더 추천.

작업 완료 후 Authelia Pod rollout 부탁드립니다.
authelia-sso-dev (dify-sso 브릿지)는 건드릴 필요 없습니다.
```

---

## 6. 본인이 할 작업 (옵션 B 채택 시)

1. **RDS에 authelia database 생성** (본인 권한 가능)
   ```bash
   psql -h <RDS> -U dify -d dify
   ```
   ```sql
   CREATE DATABASE authelia OWNER dify;
   \q
   ```

2. **인프라팀이 ConfigMap 변경 + Secret 주입 + Pod 재시작 완료 후 검증**
   - Authelia 새 Pod 로그에서 chown 에러 사라졌는지
   - `Storage schema is being checked for updates` 메시지 → PostgreSQL 연결 성공
   - 로그인 → 동의 화면 한 번 뜸 → 수락 → 다음 로그인 시 안 뜸 (1년 유지)

3. **TOTP 등 기존 사용자 데이터 마이그레이션 검토**
   - 이전 SQLite에 사용자 TOTP 등록 정보 있으면 export → PostgreSQL import 필요
   - dev 환경이라 기존 TOTP 미등록이면 새로 등록하면 됨

---

## 7. 영향 받는 기능 (PostgreSQL 전환 후 동작 정상화)

| 기능 | 현재 (SQLite read-only) | PostgreSQL 전환 후 |
|------|-------------------------|---------------------|
| OIDC consent 기억 | 매번 동의 화면 표시 | 1년간 안 뜸 |
| TOTP 디바이스 등록 | Pod 재시작 시 사라질 위험 | 영구 보존 |
| 사용자 세션 추적 | 휘발성 | 영속 |
| 비번 재설정 토큰 | 휘발성 | 영속 |
| 인증 시도 regulation | 휘발성 | 영속 |

---

## 8. 트러블슈팅 (작업 후 문제 시)

| 증상 | 원인 | 대응 |
|------|------|------|
| Authelia 시작 실패 + DB 연결 에러 | RDS 비번 틀림 또는 host 잘못 | env 값 재확인 |
| 기존 사용자 로그인 불가 | TOTP 정보 마이그레이션 안 됨 | TOTP 재등록 필요 |
| chown 에러 여전히 (옵션 A) | fsGroup 미설정 | SecurityContext 추가 |
| 동의 화면 여전히 매번 | Authelia 4.39.16의 UI 버그 (체크박스 미표시) | 4.38.10으로 다운그레이드 또는 옵션 B 채택 |

---

## 9. dify와의 일관성

dify가 어떤 storage 패턴 쓰는지 보고 같은 방식 채택 권장:
- dify가 RDS 쓰면 → Authelia도 RDS (옵션 B)
- dify가 PVC 쓰면 → Authelia도 PVC (옵션 A)

배포설정.md 또는 dify 워크로드 ConfigMap 확인.
