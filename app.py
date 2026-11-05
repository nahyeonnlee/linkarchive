"""링크 아카이브(나중에 읽기) - Flask + PostgreSQL

실행
  로컬:   python app.py            (0.0.0.0:8000)
  배포:   gunicorn app:app
DB 연결 정보는 환경변수 DATABASE_URL 로만 받습니다. (로컬은 .env 파일 사용 가능)
"""

import os
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import psycopg2
import psycopg2.extras
import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, redirect, render_template, request, url_for

# ──────────────────────────────────────────────
# ✏️ 여기만 바꾸면 화면 상단/하단 표시가 바뀝니다
# ──────────────────────────────────────────────
OWNER_NAME = "이나현"
GITHUB_URL = "https://github.com/nahyeonnlee"
# ──────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# python-dotenv: .env 파일이 있으면 읽고, 없거나 패키지가 없어도 그냥 진행
try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(BASE_DIR, ".env"))
except ImportError:
    pass

FETCH_TIMEOUT = 5
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 LinkArchiveBot/1.0"
)

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS links (
    id SERIAL PRIMARY KEY,
    url TEXT NOT NULL UNIQUE,
    title TEXT,
    description TEXT,
    image_url TEXT,
    tags TEXT[] DEFAULT '{}',
    is_read BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
"""

app = Flask(__name__)
app.json.ensure_ascii = False  # /health JSON 에 한글 그대로 출력

DISPLAY_TZ = ZoneInfo(os.environ.get("DISPLAY_TZ", "Asia/Seoul"))


@app.template_filter("localtime")
def localtime(dt):
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(DISPLAY_TZ).strftime("%Y-%m-%d %H:%M")

_db_initialized = False


# ──────────────────────────────────────────────
# DB
# ──────────────────────────────────────────────
class DBConfigError(Exception):
    pass


def get_conn():
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        raise DBConfigError(
            "DATABASE_URL 환경변수가 설정되지 않았습니다. "
            "(로컬: .env 파일 / VM: ~/앱폴더/.env / App Service: 환경 변수(앱 설정))"
        )
    # Azure PostgreSQL 은 SSL 필수
    return psycopg2.connect(dsn, sslmode="require", connect_timeout=5)


def init_db():
    """테이블 자동 생성. 성공하면 True."""
    global _db_initialized
    if _db_initialized:
        return True
    conn = get_conn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(CREATE_TABLE_SQL)
        _db_initialized = True
    finally:
        conn.close()
    return True


def db_error_message(e):
    """화면/health 에 보여줄 에러 문자열 (비밀번호는 psycopg2 에러에 포함되지 않음)."""
    if isinstance(e, DBConfigError):
        return str(e)
    return f"{type(e).__name__}: {str(e).strip()}"


# ──────────────────────────────────────────────
# 메타데이터 추출
# ──────────────────────────────────────────────
def normalize_url(raw):
    url = (raw or "").strip()
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return ""  # javascript: 등 http(s)가 아닌 URL은 거부
    return url


def fetch_metadata(url):
    """og:title / og:description / og:image 추출. 실패하면 빈 dict."""
    meta = {"title": None, "description": None, "image_url": None}
    try:
        resp = requests.get(
            url,
            timeout=FETCH_TIMEOUT,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "ko,en;q=0.8"},
            allow_redirects=True,
        )
        resp.raise_for_status()
        if "html" not in resp.headers.get("Content-Type", "html"):
            return meta
        # bytes 를 넘겨서 BeautifulSoup 이 <meta charset> 으로 인코딩을 판단하게 함 (한글 깨짐 방지)
        soup = BeautifulSoup(resp.content, "html.parser")

        def og(prop):
            tag = soup.find("meta", attrs={"property": prop}) or soup.find(
                "meta", attrs={"name": prop}
            )
            if tag and tag.get("content"):
                return tag["content"].strip()
            return None

        meta["title"] = og("og:title")
        if not meta["title"] and soup.title and soup.title.string:
            meta["title"] = soup.title.string.strip()

        meta["description"] = og("og:description") or og("description")

        image = og("og:image")
        if image:
            meta["image_url"] = urljoin(resp.url, image)
    except Exception as e:  # 네트워크 오류, 타임아웃, 파싱 오류 등 → URL만 저장
        app.logger.warning("메타데이터 추출 실패 (%s): %s", url, e)
    return meta


def parse_tags(raw):
    tags = []
    for t in (raw or "").split(","):
        t = t.strip()
        if t and t not in tags:
            tags.append(t)
    return tags


# ──────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────
def back_to_index(msg=None, kind="info"):
    params = {}
    if msg:
        params = {"msg": msg, "kind": kind}
    # 검색/태그 필터 상태 유지
    for key in ("q", "tag"):
        val = request.form.get(key) or request.args.get(key)
        if val:
            params[key] = val
    return redirect(url_for("index", **params))


@app.route("/")
def index():
    q = request.args.get("q", "").strip()
    tag = request.args.get("tag", "").strip()
    links, all_tags, db_error = [], [], None

    try:
        init_db()
        conn = get_conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                sql = "SELECT * FROM links WHERE TRUE"
                params = []
                if q:
                    sql += " AND (title ILIKE %s OR description ILIKE %s)"
                    params += [f"%{q}%", f"%{q}%"]
                if tag:
                    sql += " AND %s = ANY(tags)"
                    params.append(tag)
                sql += " ORDER BY created_at DESC, id DESC"
                cur.execute(sql, params)
                links = cur.fetchall()

                cur.execute(
                    "SELECT DISTINCT unnest(tags) AS t FROM links ORDER BY t"
                )
                all_tags = [r["t"] for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception as e:
        db_error = db_error_message(e)
        app.logger.error("DB 오류: %s", db_error)

    return render_template(
        "index.html",
        owner_name=OWNER_NAME,
        github_url=GITHUB_URL,
        links=links,
        all_tags=all_tags,
        q=q,
        tag=tag,
        db_error=db_error,
        msg=request.args.get("msg"),
        kind=request.args.get("kind", "info"),
    )


@app.route("/add", methods=["POST"])
def add_link():
    url = normalize_url(request.form.get("url"))
    if not url:
        return back_to_index("올바른 URL을 입력해 주세요. (http/https)", "error")
    tags = parse_tags(request.form.get("tags"))

    try:
        init_db()
        conn = get_conn()
        try:
            # 1) 중복이면 굳이 페이지를 가져오지 않음
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM links WHERE url = %s", (url,))
                if cur.fetchone():
                    return back_to_index("이미 저장된 링크입니다.", "warn")

            # 2) 메타데이터 추출 후 저장 (동시 요청 대비 ON CONFLICT)
            meta = fetch_metadata(url)
            with conn, conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO links (url, title, description, image_url, tags)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (url) DO NOTHING
                    RETURNING id
                    """,
                    (url, meta["title"], meta["description"], meta["image_url"], tags),
                )
                inserted = cur.fetchone()
        finally:
            conn.close()
    except Exception as e:
        return back_to_index(f"저장 실패 - {db_error_message(e)}", "error")

    if not inserted:
        return back_to_index("이미 저장된 링크입니다.", "warn")
    if not meta["title"]:
        return back_to_index("저장했습니다. (페이지 정보를 가져오지 못해 URL만 저장)", "info")
    return back_to_index("저장했습니다.", "ok")


@app.route("/toggle/<int:link_id>", methods=["POST"])
def toggle_read(link_id):
    try:
        conn = get_conn()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    "UPDATE links SET is_read = NOT is_read WHERE id = %s", (link_id,)
                )
        finally:
            conn.close()
    except Exception as e:
        return back_to_index(f"변경 실패 - {db_error_message(e)}", "error")
    return back_to_index()


@app.route("/delete/<int:link_id>", methods=["POST"])
def delete_link(link_id):
    try:
        conn = get_conn()
        try:
            with conn, conn.cursor() as cur:
                cur.execute("DELETE FROM links WHERE id = %s", (link_id,))
        finally:
            conn.close()
    except Exception as e:
        return back_to_index(f"삭제 실패 - {db_error_message(e)}", "error")
    return back_to_index("삭제했습니다.", "ok")


@app.route("/health")
def health():
    result = {
        "status": "ok",
        "time": datetime.now(timezone.utc).isoformat(),
        "database_url_set": bool(os.environ.get("DATABASE_URL", "").strip()),
        "db": "disconnected",
    }
    try:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        finally:
            conn.close()
        result["db"] = "connected"
    except Exception as e:
        result["db_error"] = db_error_message(e)
    return jsonify(result)


# 앱 시작 시 테이블 자동 생성 (실패해도 앱은 뜸 → 요청 때 다시 시도)
try:
    init_db()
except Exception as _e:
    app.logger.error("시작 시 DB 초기화 실패: %s", db_error_message(_e))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), debug=False)
