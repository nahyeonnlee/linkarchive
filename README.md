# 📚 링크 아카이브 (나중에 읽기)

URL을 저장하면 제목·설명·썸네일(og 태그)을 자동으로 가져와 카드로 보여주는 Flask + PostgreSQL 웹앱입니다.
Azure **VM**과 **App Service** 두 방식으로 배포하는 실습용으로 만들었습니다. (같은 GitHub 레포, 같은 Azure PostgreSQL 사용)

- URL 저장 시 `og:title / og:description / og:image` 추출 (없으면 `<title>`, 실패하면 URL만 저장)
- 쉼표로 구분한 태그, 태그 필터, 제목·설명 검색
- 읽음/안읽음 토글, 삭제, 중복 URL 안내
- `/health` : 앱 상태 + DB 연결 여부 JSON
- DB 연결 실패 시에도 메인 페이지에 에러 메시지 표시 (배포 디버깅용)

> 화면 상단 이름 / 하단 GitHub 링크는 `app.py` 맨 위 `OWNER_NAME`, `GITHUB_URL` 을 바꾸세요.

## 파일 구조

```
app.py                 # Flask 앱 (시작 시 links 테이블 자동 생성)
templates/index.html   # 화면 (외부 CSS 프레임워크 없음)
requirements.txt
.env.example           # DATABASE_URL 형식 예시
deploy/setup_vm.sh     # Ubuntu VM 배포/업데이트 스크립트
```

## 환경변수

| 이름 | 설명 |
|---|---|
| `DATABASE_URL` | `postgresql://USER:PASSWORD@SERVER.postgres.database.azure.com:5432/DBNAME?sslmode=require` |

- 코드에는 비밀번호가 없습니다. 로컬/VM은 `.env` 파일, App Service는 **환경 변수(앱 설정)** 로 넣습니다.
- 비밀번호에 `@ : / # ? %` 등 특수문자가 있으면 URL 인코딩하세요. (`@`→`%40`, `#`→`%23`, `%`→`%25`)
  ```bash
  python3 -c "import urllib.parse; print(urllib.parse.quote(input('pw: '), safe=''))"
  ```

---

## 1. 로컬 실행

```bash
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env              # 그리고 .env 의 DATABASE_URL 수정
python app.py                     # http://localhost:8000
```

- 확인: http://localhost:8000/health → `"db": "connected"`
- gunicorn 으로 실행해 보기: `gunicorn --bind 0.0.0.0:8000 app:app`
- 로컬 PC의 공인 IP가 PostgreSQL 방화벽에 등록되어 있어야 합니다. (아래 4번)

---

## 2. Azure VM 배포 (Ubuntu)

1. **VM 생성**: Ubuntu Server 22.04/24.04, 인증은 SSH 키 또는 암호
2. **포트 열기**: Portal → VM → **네트워킹(네트워크 설정)** → 인바운드 포트 규칙 추가
   - 대상 포트 `8000`, 프로토콜 TCP, 작업 허용 (SSH `22` 도 열려 있어야 함)
3. **PostgreSQL 방화벽**에 VM **공용 IP** 추가 (아래 4번)
4. **SSH 접속 후 배포**
   ```bash
   ssh azureuser@<VM 공용 IP>
   git clone https://github.com/<계정>/<레포>.git
   cd <레포>
   cp .env.example .env
   nano .env                      # DATABASE_URL 입력 후 저장 (Ctrl+O, Enter, Ctrl+X)
   bash deploy/setup_vm.sh
   ```
5. 브라우저에서 `http://<VM 공용 IP>:8000` 접속

**코드 수정 후 반영**: 로컬에서 `git push` → VM에서 `bash deploy/setup_vm.sh` 다시 실행 (git pull + 서비스 재시작)

| 명령 | 용도 |
|---|---|
| `sudo systemctl status linkarchive` | 서비스 상태 |
| `journalctl -u linkarchive -f` | 실시간 로그 |
| `sudo systemctl restart linkarchive` | 재시작 (.env 수정 후에도 필요) |

> systemd 서비스로 등록되어 있어 SSH 를 끊거나 VM을 재부팅해도 계속 실행됩니다.

---

## 3. Azure App Service 배포 (Linux, Python, B1)

1. **웹앱 만들기**: Portal → App Services → 만들기 → 웹앱
   - 게시: **코드**, 런타임 스택: **Python 3.12**, 운영체제: **Linux**
   - 지역: PostgreSQL 과 같은 지역 권장, 요금제: **기본 B1**
2. **환경 변수 추가**: 웹앱 → 설정 → **환경 변수** → 앱 설정 → `+ 추가`
   - 이름 `DATABASE_URL`, 값 `postgresql://...?sslmode=require` → 적용 → 확인 (앱 재시작됨)
   - (빌드가 안 될 경우) `SCM_DO_BUILD_DURING_DEPLOYMENT` = `true` 도 추가
3. **시작 명령**: 설정 → **구성** → 일반 설정 → 시작 명령
   ```
   gunicorn --bind=0.0.0.0 --timeout 600 app:app
   ```
   → 저장
4. **GitHub 연결**: 배포 → **배포 센터**
   - 원본: **GitHub** → 권한 부여 → 조직/리포지토리/분기(`main`) 선택 → 저장
   - 레포에 GitHub Actions 워크플로(`.github/workflows/...yml`)가 자동 커밋되고 배포가 시작됩니다.
   - 로컬에서 `git pull` 로 그 워크플로 파일을 받아두세요. (안 그러면 다음 push 가 거부됨)
   - 진행 상황: GitHub 레포 → **Actions** 탭, 또는 배포 센터 → 로그
5. `https://<앱이름>.azurewebsites.net` 및 `/health` 확인

**코드 수정 후 반영**: `git push` 만 하면 GitHub Actions 가 자동 배포합니다.

---

## 4. PostgreSQL 방화벽 설정 (Flexible Server)

Portal → PostgreSQL 유연한 서버 → 설정 → **네트워킹**

| 접속 위치 | 설정 |
|---|---|
| 로컬 PC | `+ 현재 클라이언트 IP 주소 추가` (IP가 바뀌면 다시 추가) |
| Azure VM | `+ 방화벽 규칙 추가` → 시작/끝 IP 모두 **VM 공용 IP** |
| App Service | ☑ **Azure 내의 모든 Azure 서비스에서 이 서버에 대한 공용 액세스 허용** |

→ 맨 위 **저장** 누르기 (반영까지 1~2분)

> "Azure 서비스 허용"은 다른 사람의 Azure 리소스도 포함하는 넓은 범위입니다. 실습용으로는 편하지만,
> 운영 환경이라면 App Service 의 아웃바운드 IP(웹앱 → 속성 → 아웃바운드 IP 주소)만 등록하거나 VNet 통합을 쓰세요.

---

## 5. 트러블슈팅

먼저 **`/health`** 를 열어보세요. `database_url_set`, `db`, `db_error` 값으로 대부분 원인을 알 수 있습니다.

| 증상 / 메시지 | 원인 → 해결 |
|---|---|
| `DATABASE_URL 환경변수가 설정되지 않았습니다` | 로컬/VM: `.env` 가 앱 폴더에 있는지 확인. App Service: 환경 변수 이름 오타, 적용 후 저장했는지 확인 |
| `timeout expired` / `Connection timed out` | PostgreSQL **방화벽**에 해당 IP가 없음 (로컬 IP 변경, VM 공용 IP, Azure 서비스 허용 체크 확인) |
| `no pg_hba.conf entry for host ...` | 방화벽 미허용 또는 SSL 미사용. 방화벽 규칙 확인 |
| `password authentication failed` | 사용자/비밀번호 오류. 비밀번호 특수문자 **URL 인코딩** 확인 |
| `could not translate host name` | 서버 주소 오타 (`<이름>.postgres.database.azure.com`) |
| `database "xxx" does not exist` | URL 마지막 DB 이름 확인 (기본 DB는 `postgres`) |
| VM: 브라우저에서 접속 안 됨 | NSG 인바운드 **8000** 포트 규칙, `sudo systemctl status linkarchive`, `curl localhost:8000/health` |
| VM: `.env` 수정했는데 반영 안 됨 | `sudo systemctl restart linkarchive` |
| App Service: "Application Error" / 시작 안 됨 | 로그 스트림 확인 (웹앱 → 모니터링 → **로그 스트림**). 시작 명령 오타, `requirements.txt` 가 레포 루트에 있는지 확인 |
| App Service: `ModuleNotFoundError` | 빌드가 안 됨 → 앱 설정 `SCM_DO_BUILD_DURING_DEPLOYMENT=true` 추가 후 재배포 |
| App Service: 첫 접속이 느림 | B1 콜드 스타트. 구성 → 일반 설정 → **항상 설정(Always On)** 켜기 |
| 저장은 되는데 제목이 URL로만 나옴 | 해당 사이트가 봇 차단/타임아웃(5초). 정상 동작이며 URL만 저장됨 |
| GitHub push 가 `rejected` | 배포 센터가 워크플로 파일을 커밋했기 때문 → `git pull --rebase` 후 push |
