# -*- coding: utf-8 -*-
"""
익명 편지 라디오 앱 (v2)
- 참가자: QR로 접속해서 익명으로 편지 제출        →  기본 주소
- 진행자: 비밀번호로 들어가서 편지를 뽑아 낭독      →  주소 뒤에 ?mode=host
- 대시보드: 낭독된 편지를 한꺼번에 띄우기 (빔용)   →  주소 뒤에 ?mode=board

저장소:
- 구글 시트 설정(secrets)이 있으면  → 구글 시트에 저장 (서버 재시작에도 안전)
- 없으면                          → 로컬 letters.db 파일 (테스트용)

실행: streamlit run app.py
"""

import re
import html
import uuid
import sqlite3
import random
from datetime import datetime

import streamlit as st

# ─────────────────────────────────────────────
# 설정 (여기만 바꾸면 됨)
# ─────────────────────────────────────────────
DB_PATH = "letters.db"            # 로컬 저장 파일 (구글 시트 안 쓸 때)
HOST_PASSWORD = "changeme1234"    # 진행자 비밀번호 → 꼭 바꾸세요!
EVENT_TITLE = "익명 편지함 📮"     # 화면 맨 위 제목
BOARD_REFRESH_SEC = 20            # 대시보드 자동 새로고침 간격(초)
BOARD_COLUMNS = 2                 # 대시보드 카드 열 개수

HEADER = ["id", "recipient", "sender", "body", "created_at", "is_read"]


# ─────────────────────────────────────────────
# 저장소: 구글 시트가 준비돼 있으면 그걸 쓰고, 아니면 SQLite
# ─────────────────────────────────────────────
def _use_gsheets() -> bool:
    """secrets에 구글 시트 정보가 있으면 True."""
    try:
        return ("gcp_service_account" in st.secrets) and ("spreadsheet" in st.secrets)
    except Exception:
        return False


@st.cache_resource
def _get_worksheet():
    """구글 시트에 딱 한 번만 접속해서 워크시트를 돌려준다."""
    import gspread
    from google.oauth2.service_account import Credentials

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(
        dict(st.secrets["gcp_service_account"]), scopes=scopes
    )
    client = gspread.authorize(creds)

    url = st.secrets["spreadsheet"]["url"]
    m = re.search(r"/d/([a-zA-Z0-9-_]+)", url)   # URL에서 시트 key만 뽑기
    key = m.group(1) if m else url
    ws = client.open_by_key(key).sheet1

    # 헤더가 없으면 만들어 둔다
    if ws.row_values(1) != HEADER:
        ws.clear()
        ws.append_row(HEADER)
    return ws


def init_db():
    """SQLite 테이블 준비 (구글 시트 쓰면 필요 없음)."""
    if _use_gsheets():
        return
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS letters (
            id TEXT PRIMARY KEY,
            recipient TEXT,
            sender TEXT,
            body TEXT NOT NULL,
            created_at TEXT NOT NULL,
            is_read INTEGER DEFAULT 0
        )
        """
    )
    conn.commit()
    conn.close()


def add_letter(recipient, sender, body):
    lid = "L" + uuid.uuid4().hex[:8]  # 항상 문자로 시작하는 고유 id
    created = datetime.now().strftime("%Y-%m-%d %H:%M")
    r, s, b = recipient.strip(), sender.strip(), body.strip()

    if _use_gsheets():
        _get_worksheet().append_row([lid, r, s, b, created, "0"])
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT INTO letters (id, recipient, sender, body, created_at, is_read) "
            "VALUES (?, ?, ?, ?, ?, 0)",
            (lid, r, s, b, created),
        )
        conn.commit()
        conn.close()


def get_letters(only_unread=False):
    """편지 목록을 최신순으로 돌려준다."""
    if _use_gsheets():
        records = _get_worksheet().get_all_records()  # [{header: value}, ...]
        letters = []
        for row in records:
            letters.append({
                "id": str(row.get("id", "")),
                "recipient": str(row.get("recipient", "")),
                "sender": str(row.get("sender", "")),
                "body": str(row.get("body", "")),
                "created_at": str(row.get("created_at", "")),
                "is_read": int(str(row.get("is_read", 0)).strip() or 0),
            })
        letters.reverse()  # 최신순
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM letters ORDER BY rowid DESC").fetchall()
        conn.close()
        letters = [dict(r) for r in rows]

    if only_unread:
        letters = [l for l in letters if l["is_read"] == 0]
    return letters


def mark_read(letter_id, read=True):
    val = 1 if read else 0
    if _use_gsheets():
        ws = _get_worksheet()
        try:
            cell = ws.find(str(letter_id), in_column=1)  # id 열에서만 찾기
        except TypeError:
            cell = ws.find(str(letter_id))
        if cell:
            ws.update_cell(cell.row, 6, str(val))  # 6번째 열 = is_read
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("UPDATE letters SET is_read = ? WHERE id = ?", (val, letter_id))
        conn.commit()
        conn.close()


# ─────────────────────────────────────────────
# 편지 카드 (진행자/대시보드 공용)
# ─────────────────────────────────────────────
def render_letter_card(letter, compact=False):
    """편지 하나를 예쁜 카드로 그린다. 사용자 입력은 이스케이프해서 안전하게."""
    to_raw = letter["recipient"].strip()
    from_raw = letter["sender"].strip()
    to_line = f"받는 사람: {html.escape(to_raw)}" if to_raw else "받는 사람: (익명)"
    from_line = f"보낸 사람: {html.escape(from_raw)}" if from_raw else "보낸 사람: 익명의 누군가"
    body = html.escape(letter["body"])https://github.com/aes-j2/publicFilesMia/blob/main/app.py

    font = "1.0rem" if compact else "1.15rem"
    pad = "20px" if compact else "28px"

    st.markdown(
        f"""
        <div style="background:#fff8e7;border-radius:16px;padding:{pad};margin-bottom:16px;
                    border:1px solid #f0e0b0;font-size:{font};line-height:1.9;">
            <div style="color:#8a6d1a;margin-bottom:12px;">{to_line}</div>
            <div style="white-space:pre-wrap;">{body}</div>
            <div style="text-align:right;margin-top:18px;color:#8a6d1a;">{from_line}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ─────────────────────────────────────────────
# 화면 1: 참가자 제출 폼
# ─────────────────────────────────────────────
def submit_view():
    st.title(EVENT_TITLE)
    st.caption("하고 싶었던 말을 익명으로 남겨주세요 🙂")

    with st.form("letter_form", clear_on_submit=True):
        recipient = st.text_input("받는 사람 (선택)", placeholder="예: 우리 연구실 동기들에게")
        body = st.text_area("전하고 싶은 말", height=200,
                            placeholder="칭찬, 감사, 못 했던 말 무엇이든 좋아요!")
        sender = st.text_input("보내는 사람 별명 (선택)", placeholder="비워 두면 익명으로 전달됩니다")
        submitted = st.form_submit_button("편지 보내기 ✉️", use_container_width=True)

    if submitted:
        if not body.strip():
            st.warning("편지 내용을 적어주세요!")
        else:
            add_letter(recipient, sender, body)
            st.success("편지가 잘 도착했어요! 📮")
            st.balloons()


# ─────────────────────────────────────────────
# 화면 2: 진행자 낭독 화면
# ─────────────────────────────────────────────
def host_view():
    st.title("🎙️ 진행자 화면")

    if not st.session_state.get("authed", False):
        pw = st.text_input("진행자 비밀번호", type="password")
        if st.button("입장"):
            if pw == HOST_PASSWORD:
                st.session_state.authed = True
                st.rerun()
            else:
                st.error("비밀번호가 틀렸어요.")
        return

    letters = get_letters()
    unread = [l for l in letters if l["is_read"] == 0]

    c1, c2, c3 = st.columns(3)
    c1.metric("전체", len(letters))
    c2.metric("안 읽음", len(unread))
    c3.metric("읽음", len(letters) - len(unread))

    st.divider()

    if st.button("🎲 안 읽은 편지 랜덤으로 뽑기", use_container_width=True, type="primary"):
        if unread:
            st.session_state.current = random.choice(unread)
        else:
            st.session_state.current = None
            st.info("안 읽은 편지가 없어요!")

    cur = st.session_state.get("current")
    if cur:
        render_letter_card(cur)
        if st.button("✅ 이 편지 읽음으로 표시", use_container_width=True):
            mark_read(cur["id"], True)
            st.session_state.current = None
            st.rerun()

    st.divider()

    with st.expander("📋 전체 편지 목록 / 직접 고르기"):
        for l in letters:
            status = "✅ 읽음" if l["is_read"] else "🟡 대기"
            preview = l["body"][:30] + ("…" if len(l["body"]) > 30 else "")
            col_a, col_b = st.columns([4, 1])
            col_a.write(f"{status} · {preview}")
            if col_b.button("펼치기", key=f"pick_{l['id']}"):
                st.session_state.current = l
                st.rerun()

    st.caption("대시보드 화면 주소: 지금 주소 뒤에 ?mode=board 를 붙이세요.")


# ─────────────────────────────────────────────
# 화면 3: 낭독된 편지 대시보드 (빔프로젝터용)
# ─────────────────────────────────────────────
def board_view():
    # 자동 새로고침 (패키지 없으면 수동 새로고침 버튼으로 대체)
    try:
        from streamlit_autorefresh import st_autorefresh
        st_autorefresh(interval=BOARD_REFRESH_SEC * 1000, key="board_refresh")
    except Exception:
        if st.button("🔄 새로고침"):
            st.rerun()

    st.title("📮 낭독된 편지 모음")

    read_letters = [l for l in get_letters() if l["is_read"] == 1]
    if not read_letters:
        st.info("아직 낭독된 편지가 없어요.")
        return

    cols = st.columns(BOARD_COLUMNS)
    for i, l in enumerate(read_letters):
        with cols[i % BOARD_COLUMNS]:
            render_letter_card(l, compact=True)


# ─────────────────────────────────────────────
# 메인: 화면 선택
# ─────────────────────────────────────────────
def _sidebar_nav(current):
    """왼쪽 사이드바에 화면 이동 버튼. 참가자에겐 기본으로 접혀 있음."""
    with st.sidebar:
        st.markdown("### 🧭 화면 이동")
        st.caption("진행자 / 참가자 / 게시판")
        if st.button("✍️ 편지 쓰기 (참가자)", use_container_width=True,
                     type="primary" if current == "submit" else "secondary"):
            st.query_params["mode"] = "submit"
            st.rerun()
        if st.button("🎙️ 진행자 화면", use_container_width=True,
                     type="primary" if current == "host" else "secondary"):
            st.query_params["mode"] = "host"
            st.rerun()
        if st.button("📮 게시판", use_container_width=True,
                     type="primary" if current == "board" else "secondary"):
            st.query_params["mode"] = "board"
            st.rerun()


def main():
    st.set_page_config(page_title=EVENT_TITLE, page_icon="📮",
                       layout="wide", initial_sidebar_state="collapsed")
    init_db()

    mode = st.query_params.get("mode", "submit")
    _sidebar_nav(mode)

    if mode == "host":
        host_view()
    elif mode == "board":
        board_view()
    else:
        submit_view()


if __name__ == "__main__":
    main()
