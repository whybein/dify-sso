# Dify SSO 연동

이 프로젝트는 Dify의 엔터프라이즈 SSO 연동을 구현하며, 현재 OIDC 프로토콜을 지원합니다.

## 기능

- OIDC 로그인 연동
- 사용자 DB 자동 생성 및 연결
- 사용자 역할 자동 갱신

## 기술 스택

- Python 3.8+
- Flask
- SQLAlchemy
- PostgreSQL  
- Redis
- Flask-Login
- Pydantic Settings
- PyJWT

## 시스템 요구 사항

- Python 3.8 이상
- PostgreSQL 12 이상
- Redis 6 이상
- OIDC를 지원하는 IdP(아이덴티티 프로바이더)

## 프로젝트 구조

```
dify-sso/
├── app/                        # 메인 애플리케이션 코드
│   ├── api/                    # API 라우트 및 엔드포인트
│   │   ├── dify/               # Dify 관련 API 엔드포인트 구현
│   │   └── router.py           # 라우트 설정
│   ├── configs/                # 설정 모듈(모듈형 설정 시스템)
│   │   ├── __init__.py         # 설정 통합 진입점
│   │   ├── app_config.py       # 앱 기본 설정
│   │   ├── database_config.py  # DB 설정
│   │   ├── redis_config.py     # Redis 설정
│   │   ├── logger_config.py    # 로깅 설정
│   │   └── sso_config.py       # SSO 설정
│   ├── extensions/             # 확장 모듈(플러그인 방식 초기화)
│   │   ├── __init__.py         # 확장 모듈 진입점
│   │   ├── ext_database.py     # DB 확장
│   │   ├── ext_redis.py        # Redis 확장
│   │   ├── ext_logging.py      # 로깅 확장
│   │   ├── ext_oidc.py         # OIDC 확장
│   │   ├── ext_timezone.py     # 타임존 확장
│   │   └── ext_blueprints.py   # 블루프린트 등록 확장
│   ├── models/                 # 데이터 모델
│   ├── services/               # 비즈니스 로직 서비스
│   ├── app.py                  # Flask 앱 팩토리
│   ├── __init__.py             # 패키지 초기화
│   └── main.py                 # 앱 진입점
├── examples/                   # Authelia 샘플 설정 파일
├── yaml/                       # 배포 설정 파일
│   ├── docker-compose.yaml     # Docker Compose 설정
│   └── k8s-deployment.yaml     # Kubernetes 배포 설정
├── .env.example                # 환경 변수 예시
├── .dockerignore               # Docker 제외 파일
├── .gitignore                  # Git 제외 파일
├── requirements.txt            # 프로젝트 의존성
└── Dockerfile                  # Docker 빌드 파일
```

## 설정

프로젝트는 모듈형 설정 시스템을 사용하며, 모든 설정은 Pydantic Settings로 관리되고 환경 변수와 `.env` 파일을 지원합니다.

OIDC SSO 연동에는 다음 환경 변수가 필요합니다.

```bash
# Dify 설정
CONSOLE_WEB_URL=your-dify-web-address  # Dify 웹 주소
SECRET_KEY=dify-secret-key  # Dify secret key
TENANT_ID=dify-tenant-id  # Dify tenant id
EDITION=SELF_HOSTED
ACCOUNT_DEFAULT_ROLE=editor  # 기본 사용자 역할. 선택값: normal, editor, admin

# 토큰 설정
ACCESS_TOKEN_EXPIRE_MINUTES=900
REFRESH_TOKEN_EXPIRE_DAYS=30
REFRESH_TOKEN_PREFIX=refresh_token:
ACCOUNT_REFRESH_TOKEN_PREFIX=account_refresh_token:

# OIDC 설정
OIDC_ENABLED=true  # OIDC 사용 여부
OIDC_CLIENT_ID=your-client-id  # OIDC 클라이언트 ID
OIDC_CLIENT_SECRET=your-client-secret  # OIDC 클라이언트 시크릿
OIDC_DISCOVERY_URL=https://your-oidc-provider/.well-known/openid-configuration  # OIDC 디스커버리 엔드포인트
OIDC_REDIRECT_URI=http://localhost:8000/console/api/enterprise/sso/oidc/callback  # 콜백 URL
OIDC_SCOPE=openid profile email roles  # 요청 스코프
OIDC_RESPONSE_TYPE=code  # 응답 타입

# 데이터베이스 설정
DB_HOST=127.0.0.1
DB_PORT=5432
DB_DATABASE=dify
DB_USERNAME=dify_admin
DB_PASSWORD=123456

# Redis 설정
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
REDIS_DB=0
REDIS_PASSWORD=  # Redis 비밀번호, 없으면 비워 둠
```

## 설치 및 실행

### Docker 사용

1. 이미지 빌드:

```bash
docker build -t dify-sso .
```

2. 컨테이너 실행:

```bash
docker run -p 8000:8000 --env-file .env dify-sso
```

### 로컬 개발

1. 저장소 클론:

```bash
git clone https://github.com/lework/dify-sso.git
cd dify-sso
```

2. 가상 환경 생성 및 활성화:

```bash
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
.venv\Scripts\activate     # Windows
```

3. 의존성 설치:

```bash
pip install -r requirements.txt
```

4. 환경 변수 설정:

```bash
cp .env.example .env
# .env를 편집하여 OIDC 및 DB 설정을 입력
```

5. 애플리케이션 실행:

```bash
python -m app.main
```

### 연동 절차

1. OIDC 프로바이더 설정 (Authelia 등) — 상세 설정은 [SETUP_GUIDE.md](./SETUP_GUIDE.md) 참조

> 프로바이더에 scope `roles`를 설정하면 로그인 시 역할을 부여할 수 있습니다. 기본 역할은 `editor`이며, `.env`의 `ACCOUNT_DEFAULT_ROLE`로 변경 가능합니다.

2. dify-sso 컨테이너 기동

```bash
docker run -p 8000:8000 --env-file .env lework/dify-sso
```

3. dify-proxy의 nginx 설정에 다음을 추가:

```nginx
location ~ (/console/api/system-features|/console/api/enterprise/sso/) {
  proxy_pass http://dify-sso:8000;
  include proxy.conf;
}
```

> nginx 전체 예시는 [default.conf.template](https://github.com/langgenius/dify/blob/main/docker/nginx/conf.d/default.conf.template)를 참고하세요.

dify-proxy가 Kubernetes에 배포된 경우 [k8s-deployment.yaml](./yaml/k8s-deployment.yaml)로 dify-sso를 배포할 수 있습니다.

dify-proxy가 Docker에 배포된 경우 [docker-compose.yaml](./yaml/docker-compose.yaml)로 dify-sso를 배포할 수 있습니다.

## API 엔드포인트

OIDC SSO 연동에서 제공하는 API:

- **GET /console/api/enterprise/sso/oidc/login**: OIDC 로그인 플로우 시작, 사용자를 OIDC 프로바이더로 리다이렉트
- **GET /console/api/enterprise/sso/oidc/callback**: OIDC 콜백 처리, 인가 코드 처리 및 사용자 정보 획득
- **GET /console/api/system-features**: 시스템 기능 설정 조회
- **GET /console/api/enterprise/info**: 엔터프라이즈 정보 조회

## OIDC 인증 플로우

OIDC 로그인은 표준 **인가 코드 플로우(Authorization Code Flow)**를 따릅니다.

1. 사용자가 `/console/api/enterprise/sso/oidc/login`에 접근
2. 시스템이 인가 URL을 만들어 OIDC 프로바이더 로그인 페이지로 리다이렉트
3. 사용자가 OIDC 프로바이더에서 인증
4. OIDC 프로바이더가 인가 코드와 함께 `/console/api/enterprise/sso/oidc/callback`으로 리다이렉트
5. 시스템이 인가 코드로 액세스 토큰·ID 토큰 획득
6. 액세스 토큰으로 사용자 정보 조회
7. OIDC 사용자 정보의 `sub` 또는 `email`로 DB에서 사용자 존재 여부 확인:
   - 있으면 정보(이름 등) 갱신, 로그인 시각·IP 기록, SSO 역할과 불일치 시 역할 갱신
   - 없으면 신규 사용자 생성 후 기본 테넌트에 연결
8. JWT·리프레시 토큰 발급 후 Dify 콘솔로 리다이렉트

## 데이터베이스 테이블

주요 테이블:

- `accounts`: 사용자 계정
- `tenants`: 테넌트
- `tenant_account_joins`: 사용자–테넌트 연결

## 기여 방법

1. 이 저장소를 Fork
2. 기능 브랜치 생성 (`git checkout -b feature/AmazingFeature`)
3. 커밋 (`git commit -m 'Add some AmazingFeature'`)
4. 브랜치 푸시 (`git push origin feature/AmazingFeature`)
5. Pull Request 생성

## 라이선스

MIT 라이선스 — 자세한 내용은 [LICENSE](LICENSE) 파일을 참고하세요.

## ⚠️ 안내

Dify 공식 제품에는 상용 라이선스 모델이 있으며, 내장 SSO 기능은 상용 플랜에 포함되는 경우가 많습니다. 가능하다면 Dify 상용 라이선스 구매를 권장합니다.

본 프로젝트 `dify-sso`는 **Dify 공식 소스 코드를 수정하지 않는** 독립 외부 연동입니다. 표준 OIDC로 기업 IdP와 연결해 Dify에 SSO로 들어가는 **선택적 방법**을 제공하며, 이미 통합 인증 체계를 쓰는 기업 사용자를 위한 편의 목적입니다.

Dify의 지적 재산권과 비즈니스 모델을 존중합니다. 본 프로젝트가 Dify 상업적 이익에 영향을 줄 수 있다고 보시면 저장소 작성자에게 연락해 주시면 협의하거나 요청에 따라 조치하겠습니다.

## 참고 자료

- [OpenID Connect 사양](https://openid.net/connect/)
