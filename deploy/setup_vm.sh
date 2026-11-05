#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# 링크 아카이브 - Ubuntu VM 배포 스크립트
#
# 첫 실행:  git clone <레포> && cd <레포> && cp .env.example .env && nano .env
#           bash deploy/setup_vm.sh
# 업데이트: bash deploy/setup_vm.sh   (git pull → pip install → 서비스 재시작)
#
# gunicorn 을 systemd 서비스(linkarchive)로 등록 → 0.0.0.0:8000 상시 실행, 재부팅 후 자동 시작
# ─────────────────────────────────────────────────────────────
set -euo pipefail

SERVICE_NAME="linkarchive"
PORT=8000
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_USER="$(id -un)"
VENV="$APP_DIR/venv"

info() { echo -e "\n\033[1;34m▶ $*\033[0m"; }
warn() { echo -e "\033[1;33m⚠ $*\033[0m"; }

if [[ "$RUN_USER" == "root" ]]; then
  warn "root 가 아닌 일반 사용자(예: azureuser)로 실행하세요. 필요한 곳에서만 sudo 를 사용합니다."
  exit 1
fi

cd "$APP_DIR"
info "앱 폴더: $APP_DIR (실행 사용자: $RUN_USER)"

# 1) 시스템 패키지
info "apt 패키지 설치"
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv python3-pip git curl

# 2) 최신 코드 받기 (git 레포일 때)
if [[ -d .git ]]; then
  info "git pull"
  git pull --ff-only || warn "git pull 실패 (로컬 변경사항 확인: git status). 현재 코드로 계속 진행합니다."
fi

# 3) venv + 의존성
if [[ ! -x "$VENV/bin/python" ]]; then
  info "venv 생성"
  python3 -m venv "$VENV"
fi
info "pip install"
"$VENV/bin/pip" install --upgrade pip -q
"$VENV/bin/pip" install -r requirements.txt -q

# 4) .env 확인
if [[ ! -f .env ]]; then
  warn ".env 파일이 없습니다. 아래처럼 만든 뒤 스크립트를 다시 실행하세요:"
  echo "    cp .env.example .env"
  echo "    nano .env      # DATABASE_URL=postgresql://... 입력"
  echo "    bash deploy/setup_vm.sh"
  exit 1
fi
chmod 600 .env

# 5) systemd 서비스 등록
info "systemd 서비스 등록: $SERVICE_NAME"
sudo tee "/etc/systemd/system/${SERVICE_NAME}.service" > /dev/null <<EOF
[Unit]
Description=Link Archive (Flask + gunicorn)
After=network-online.target
Wants=network-online.target

[Service]
User=${RUN_USER}
WorkingDirectory=${APP_DIR}
ExecStart=${VENV}/bin/gunicorn --workers 2 --bind 0.0.0.0:${PORT} --timeout 60 --access-logfile - app:app
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME" > /dev/null
sudo systemctl restart "$SERVICE_NAME"

# 6) 확인
info "상태 확인"
sleep 3
sudo systemctl --no-pager --lines=5 status "$SERVICE_NAME" || true
echo
curl -s "http://127.0.0.1:${PORT}/health" && echo || warn "/health 응답 없음 → 로그 확인: journalctl -u $SERVICE_NAME -n 50"

PUBLIC_IP="$(curl -s --max-time 3 https://api.ipify.org || echo '<VM 공용 IP>')"
info "완료! 브라우저에서 http://${PUBLIC_IP}:${PORT} 접속"
echo "  (안 열리면 Azure Portal → VM → 네트워킹에서 인바운드 TCP ${PORT} 허용 규칙 추가)"
echo "  로그:   journalctl -u ${SERVICE_NAME} -f"
echo "  재시작: sudo systemctl restart ${SERVICE_NAME}"
