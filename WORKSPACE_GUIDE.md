# 워크스페이스 추가 / 확인 가이드

Dify 오픈소스에는 **추가 워크스페이스 생성 UI/API가 없습니다**. 워크스페이스는 사용자 가입 시 자동으로 1개만 생성되며, 그 이후로는 추가가 불가능한 구조입니다.

이 문서는 **로컬 Python + DB 접속** 기반으로 워크스페이스를 추가하는 절차를 다룹니다. Private key 파일 배포만 서버 Pod 터미널에서 수행합니다.

---

## 사전 정보

### Dify의 워크스페이스 구조

워크스페이스(=Tenant)를 완전히 만들려면 3개 테이블에 데이터가 필요합니다.

| 테이블 | 용도 |
|--------|------|
| `tenants` | 워크스페이스 본체. `encrypt_public_key`에 RSA 공개키 저장 |
| `tenant_account_joins` | 사용자-워크스페이스 연결 (role: owner/admin/editor/normal) |
| `tenant_plugin_auto_upgrade_strategies` | 플러그인 자동 업그레이드 설정 |

추가로 **파일시스템 스토리지**(`/app/api/storage/privkeys/{tenant_id}/private.pem`)에 RSA 개인키 저장이 필요합니다.

### 왜 RSA 키가 필요한가

Dify는 워크스페이스별로 LLM 공급자 API 키(OpenAI 등)를 **RSA 하이브리드 암호화**로 저장합니다.

- `encrypt_public_key` (DB): API 키 저장 시 암호화용
- `private.pem` (storage): LLM 호출 시 복호화용

둘 다 없으면 공급자 API 키 저장/사용이 실패합니다.

---

## 사전 준비

### 로컬 Python 환경

```bash
pip install pycryptodome psycopg2-binary
```

### DB 접속 정보 확인

- `DB_HOST`, `DB_PORT`, `DB_DATABASE`, `DB_USERNAME`, `DB_PASSWORD`

### 서버 스토리지 경로 확인 (최초 1회만)

dify-api Pod 터미널에서:

```bash
echo $STORAGE_LOCAL_PATH
ls -la /app/api/storage/privkeys/
df -h /app/api/storage
```

- 기본값: `/app/api/storage`
- 기존 워크스페이스 `tenant_id` 디렉토리가 보이면 경로가 맞음

### dify-worker와 스토리지 공유 여부 확인

dify-api Pod:
```bash
ls /app/api/storage/privkeys/
```

dify-worker Pod:
```bash
ls /app/api/storage/privkeys/
```

- **같은 목록이 보이면** → 공유 PVC ✅ (api Pod에만 키 파일 배치해도 OK)
- **다르면** → 두 Pod에 모두 배치 필요

---

## 워크스페이스 추가 절차

### 1단계: 로컬에서 키 생성 + DB INSERT

다음 스크립트를 `create_workspace.py`로 저장:

```python
import uuid
import os
import psycopg2
from Crypto.PublicKey import RSA

# ========== 수정 필요 ==========
WORKSPACE_NAME = "새 워크스페이스"
OWNER_EMAIL = "owner@example.com"

DB_CONFIG = {
    "host": "your-db-host",
    "port": 5432,
    "dbname": "dify",
    "user": "dify",
    "password": "your-db-password",
}
# ===============================

# 1. 키 페어 생성
tenant_id = str(uuid.uuid4())
private_key = RSA.generate(2048)
pem_public = private_key.publickey().export_key().decode()
pem_private = private_key.export_key().decode()

# 2. private key를 로컬에 저장 (서버 업로드용)
local_dir = f"./privkeys/{tenant_id}"
os.makedirs(local_dir, exist_ok=True)
with open(f"{local_dir}/private.pem", "w") as f:
    f.write(pem_private)
print(f"Private key saved locally: {local_dir}/private.pem")

# 3. DB 접속
conn = psycopg2.connect(**DB_CONFIG)
cur = conn.cursor()

# 4. owner account_id 조회
cur.execute("SELECT id FROM accounts WHERE email = %s", (OWNER_EMAIL,))
row = cur.fetchone()
if not row:
    raise Exception(f"Account not found: {OWNER_EMAIL}")
owner_id = row[0]
print(f"Owner account found: {owner_id}")

# 5. tenant 생성
cur.execute("""
    INSERT INTO tenants (id, name, encrypt_public_key, plan, status, created_at, updated_at)
    VALUES (%s, %s, %s, 'basic', 'normal', NOW(), NOW())
""", (tenant_id, WORKSPACE_NAME, pem_public))

# 6. owner 연결
cur.execute("""
    INSERT INTO tenant_account_joins (id, tenant_id, account_id, role, "current", created_at, updated_at)
    VALUES (gen_random_uuid(), %s, %s, 'owner', false, NOW(), NOW())
""", (tenant_id, owner_id))

# 7. 플러그인 전략
cur.execute("""
    INSERT INTO tenant_plugin_auto_upgrade_strategies (
        id, tenant_id, strategy_setting, upgrade_time_of_day, upgrade_mode,
        exclude_plugins, include_plugins, created_at, updated_at
    )
    VALUES (gen_random_uuid(), %s, 'fix_only', 0, 'exclude',
            '[]'::jsonb, '[]'::jsonb, NOW(), NOW())
""", (tenant_id,))

conn.commit()
cur.close()
conn.close()

print()
print(f"Workspace created in DB:")
print(f"  Name: {WORKSPACE_NAME}")
print(f"  Tenant ID: {tenant_id}")
print()
print("Next step: upload private.pem to the Pod")
print(f"  Local file: {local_dir}/private.pem")
print(f"  Target path: /app/api/storage/privkeys/{tenant_id}/private.pem")
```

실행:
```bash
python create_workspace.py
```

### 2단계: Private key를 서버 Pod에 업로드

출력된 `tenant_id`와 로컬 `private.pem` 내용을 서버로 옮깁니다.

로컬에서 pem 내용 출력:
```bash
cat ./privkeys/<TENANT_ID>/private.pem
```

전체 내용(`-----BEGIN...`부터 `-----END...`까지) 복사 후, **dify-api Pod 터미널에서**:

```bash
TENANT_ID=<방금_생성된_tenant_id>
mkdir -p /app/api/storage/privkeys/$TENANT_ID
cat > /app/api/storage/privkeys/$TENANT_ID/private.pem << 'EOF'
-----BEGIN RSA PRIVATE KEY-----
(붙여넣기)
-----END RSA PRIVATE KEY-----
EOF
chmod 600 /app/api/storage/privkeys/$TENANT_ID/private.pem
ls -la /app/api/storage/privkeys/$TENANT_ID/
```

**dify-worker가 스토리지를 공유하지 않으면**, worker Pod 터미널에서도 동일하게 반복.

### 3단계: 브라우저에서 확인

1. Dify 콘솔 **로그아웃**
2. 다시 **로그인**
3. 좌측 상단 워크스페이스 이름 클릭
4. 드롭다운에 새 워크스페이스 표시됨 → 클릭해서 전환

---

## 멤버 추가 (이메일 기반)

### 단일 멤버 추가

워크스페이스 이름과 사용자 이메일만 알면 되는 SQL 한 줄:

```sql
INSERT INTO tenant_account_joins (id, tenant_id, account_id, role, "current", created_at, updated_at)
SELECT
    gen_random_uuid(),
    t.id,
    a.id,
    'normal',     -- owner / admin / editor / normal / dataset_operator
    false,
    NOW(),
    NOW()
FROM tenants t, accounts a
WHERE t.name = '워크스페이스_이름'
  AND a.email = 'member@example.com'
  AND NOT EXISTS (
      SELECT 1 FROM tenant_account_joins
      WHERE tenant_id = t.id AND account_id = a.id
  );
```

중복 체크(`NOT EXISTS`) 덕분에 이미 멤버면 아무것도 안 합니다.

### 여러 명 한 번에 추가

```sql
INSERT INTO tenant_account_joins (id, tenant_id, account_id, role, "current", created_at, updated_at)
SELECT
    gen_random_uuid(),
    t.id,
    a.id,
    'normal',
    false,
    NOW(),
    NOW()
FROM tenants t
CROSS JOIN accounts a
WHERE t.name = '워크스페이스_이름'
  AND a.email IN (
      'member1@example.com',
      'member2@example.com',
      'member3@example.com'
  )
  AND NOT EXISTS (
      SELECT 1 FROM tenant_account_joins
      WHERE tenant_id = t.id AND account_id = a.id
  );
```

### Role 변경

```sql
UPDATE tenant_account_joins
SET role = 'admin'
WHERE tenant_id = (SELECT id FROM tenants WHERE name = '워크스페이스_이름')
  AND account_id = (SELECT id FROM accounts WHERE email = 'member@example.com');
```

### 멤버 제거

```sql
DELETE FROM tenant_account_joins
WHERE tenant_id = (SELECT id FROM tenants WHERE name = '워크스페이스_이름')
  AND account_id = (SELECT id FROM accounts WHERE email = 'member@example.com');
```

### Role 종류

| Role | 권한 |
|------|------|
| `owner` | 전체 제어, 소유권 이전 가능 |
| `admin` | 멤버/설정 관리 |
| `editor` | 앱/콘텐츠 편집 |
| `normal` | 조회/사용만 |
| `dataset_operator` | 데이터셋만 관리 |

---

## 워크스페이스 확인 방법

### 전체 워크스페이스 목록

```sql
SELECT
    id,
    name,
    status,
    plan,
    created_at,
    (encrypt_public_key IS NOT NULL) AS has_pubkey
FROM tenants
ORDER BY created_at DESC;
```

### 특정 사용자의 워크스페이스 조회

```sql
SELECT
    t.id AS tenant_id,
    t.name AS workspace_name,
    t.status,
    taj.role,
    taj."current" AS is_current
FROM tenants t
JOIN tenant_account_joins taj ON t.id = taj.tenant_id
JOIN accounts a ON a.id = taj.account_id
WHERE a.email = 'user@example.com';
```

### 특정 워크스페이스의 멤버 조회

```sql
SELECT
    a.email,
    a.name,
    taj.role,
    taj.created_at AS joined_at
FROM accounts a
JOIN tenant_account_joins taj ON a.id = taj.account_id
JOIN tenants t ON t.id = taj.tenant_id
WHERE t.name = '워크스페이스_이름'
ORDER BY taj.role, taj.created_at;
```

### Private key 파일 존재 확인 (Pod)

dify-api Pod에서:

```bash
for dir in /app/api/storage/privkeys/*/; do
    tenant_id=$(basename "$dir")
    if [ -f "$dir/private.pem" ]; then
        echo "OK   $tenant_id"
    else
        echo "MISS $tenant_id"
    fi
done
```

---

## 워크스페이스 삭제

하드 삭제는 앱/데이터셋/대화이력 등 모든 관련 데이터를 정리해야 하므로 **archive 상태 변경을 권장**합니다.

### 소프트 삭제 (권장)

```sql
UPDATE tenants
SET status = 'archive'
WHERE name = '삭제할_워크스페이스_이름';
```

archive 상태의 워크스페이스는 드롭다운에 표시되지 않습니다. 복구는 `status = 'normal'`로 변경.

### 하드 삭제 (주의)

```sql
-- 관련 데이터가 있으면 FK 제약 오류 발생. 정리 후 실행.
DELETE FROM tenant_account_joins WHERE tenant_id = '삭제할_tenant_id';
DELETE FROM tenant_plugin_auto_upgrade_strategies WHERE tenant_id = '삭제할_tenant_id';
DELETE FROM tenants WHERE id = '삭제할_tenant_id';
```

Pod 스토리지에서도 정리:
```bash
rm -rf /app/api/storage/privkeys/<삭제할_tenant_id>
```

---

## 트러블슈팅

### 드롭다운에 새 워크스페이스가 안 보임

- 브라우저 로그아웃/재로그인 했는지 확인
- `tenant_account_joins`에 해당 account가 연결되어 있는지 확인
- `tenants.status`가 `normal`인지 확인 (`archive`면 숨김)

### 워크스페이스 전환은 되는데 API 키 저장/사용 시 오류

- `tenants.encrypt_public_key`가 비어있지 않은지 확인
- Pod의 `/app/api/storage/privkeys/<tenant_id>/private.pem` 존재 확인
- dify-worker Pod에도 동일 파일이 있는지 확인 (스토리지 비공유 시)

### "Account not found" 에러

- 이메일이 `accounts.email`과 정확히 일치하는지 확인 (대소문자 포함)
- Authelia/SSO로 **처음 로그인해야** account 레코드가 생성됨 → 아직 로그인 안 한 사용자는 추가 불가

### 멤버 추가 SQL이 0 rows affected

- 워크스페이스 이름 또는 이메일 오타
- 이미 멤버로 등록되어 있음 (`NOT EXISTS`에 걸림) → 확인 쿼리로 현재 role 조회

### Pod 재시작 후 기존 워크스페이스들의 private key가 사라짐

스토리지가 emptyDir로 설정된 경우 발생. 해결책:
1. 배포 매니페스트에 PVC 추가 (선배포자와 협의 필요)
2. 임시 방편: 새 키페어 생성 후 `UPDATE tenants SET encrypt_public_key = ...` (단, 기존에 저장된 LLM API 키 암호문은 복호화 불가 → 재입력 필요)

---

## 참고

- Dify 소스 기준: `api/services/account_service.py`의 `TenantService.create_tenant()`
- RSA 키 생성 로직: `api/libs/rsa.py`의 `generate_key_pair()`
- 스토리지 경로 규칙: `privkeys/{tenant_id}/private.pem`
