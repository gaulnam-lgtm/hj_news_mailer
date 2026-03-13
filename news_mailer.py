import os
import json
import smtplib
import re
import base64
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parsedate_to_datetime
from urllib.request import Request, urlopen
from urllib.parse import quote, urlparse
from collections import defaultdict
from xml.etree import ElementTree as ET

# ── 설정 ────────────────────────────────────────────────────
GMAIL_ID = os.environ["GMAIL_ID"]
GMAIL_PW = os.environ["GMAIL_APP_PASSWORD"]
MAIL_TO = os.environ["MAIL_TO"]
KEYWORDS = json.loads(os.environ["KEYWORDS"])
NAVER_CLIENT_ID = os.environ["NAVER_CLIENT_ID"]
NAVER_CLIENT_SECRET = os.environ["NAVER_CLIENT_SECRET"]

MIN_ARTICLE_SCORE = int(os.environ.get("MIN_ARTICLE_SCORE", "7"))

# ── 이미지 Base64 로드 (GitHub 업로드용) ─────────────────────
ICON_PATH = "icon.png"
with open(ICON_PATH, "rb") as f:
    _ext = os.path.splitext(ICON_PATH)[-1].lstrip(".").replace("jpg", "jpeg")
    ICON_BASE64 = f"data:image/{_ext};base64," + base64.b64encode(f.read()).decode()

IMAGE2_PATH = "image2.png"          # ← GitHub에 함께 올릴 파일명
with open(IMAGE2_PATH, "rb") as f:
    _ext2 = os.path.splitext(IMAGE2_PATH)[-1].lstrip(".").replace("jpg", "jpeg")
    IMAGE2_BASE64 = f"data:image/{_ext2};base64," + base64.b64encode(f.read()).decode()

# ── 나머지 설정 ──────────────────────────────────────────────
KEYWORDS_PLATFORM = json.loads(
    os.environ.get("KEYWORDS_PLATFORM", json.dumps([
        "애플", "앱스토어", "구글", "플레이스토어", "앱 마켓",
        "원스토어", "갤럭시스토어", "인앱결제", "외부결제",
        "제3자결제", "수수료", "정책", "규제", "심사", "결제"
    ], ensure_ascii=False))
)

KEYWORDS_EXCLUDE = json.loads(
    os.environ.get("KEYWORDS_EXCLUDE", json.dumps([
        "홈페이지", "웹사이트", "블로그", "SEO", "마케팅",
        "외부링크", "백링크", "트래픽", "도메인",
        "검색엔진최적화", "페이지뷰", "유입", "홍보"
    ], ensure_ascii=False))
)

today_dt = datetime.now(timezone.utc)
today = today_dt.strftime("%Y년 %m월 %d일")
week_ago_dt = today_dt - timedelta(days=7)
week_ago = week_ago_dt.strftime("%Y년 %m월 %d일")

# ── 주차 레이블 ──────────────────────────────────────────────
def get_week_label(dt):
    week_num = (dt.day - 1) // 7 + 1
    korean_nums = ["첫", "둘", "셋", "넷", "다섯"]
    return f"{dt.month}월 {korean_nums[min(week_num-1, 4)]}째주"

week_label = get_week_label(today_dt)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
GOOGLEBOT_UA = ("Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36 "
                "(compatible; Googlebot/2.1; +http://www.google.com/bot.html)")

# ── 봇 차단 도메인 ────────────────────────────────────────────
BOT_BLOCKED_DOMAINS = {
    "v.daum.net", "daum.net",
    "news.nate.com", "nate.com",
    "naver.com",
}

def is_blocked_domain(url: str) -> bool:
    domain = get_domain(url)
    return any(domain == d or domain.endswith("." + d) for d in BOT_BLOCKED_DOMAINS)


# ── 유틸 함수 (이하 동일) ─────────────────────────────────────
def strip_html(text):
    return re.sub(r"<[^>]+>", "", text or "").strip()

def normalize_text(text):
    text = strip_html(text)
    text = re.sub(r"\s+", "", text)
    return text.lower()

def clean_spaces(text):
    return re.sub(r"\s+", " ", (text or "")).strip()

def get_domain(url):
    m = re.search(r"https?://(?:www\.)?([^/]+)", url or "")
    return m.group(1).lower() if m else ""

# PRESS_MAP 생략 (기존과 동일)

def get_press_name(url: str, title: str = "") -> str:
    domain = get_domain(url)
    title = clean_spaces(strip_html(title or ""))
    for key, name in PRESS_MAP.items():
        if domain == key or domain.endswith("." + key) or key in domain:
            return name
    if " - " in title:
        maybe_press = title.rsplit(" - ", 1)[-1].strip()
        if 1 < len(maybe_press) <= 30:
            return maybe_press
    return domain

# make_absolute_url, is_valid_snippet, get_article_info 등 기존 함수 모두 동일 (생략)

# fetch_naver_articles, fetch_google_articles, extract_best_sentence 등 기존 함수 모두 동일 (생략)

# ── 핵심 요약 생성 ────────────────────────────────────────────
POLICY_KEYWORDS = [ ... ]  # 기존과 동일

def build_summary_html(all_articles):
    # ... 기존 로직 ...

    accent_colors = ["#475569"] * 3   # ← 5번 요청: 파란/빨강/초록 → 일괄 진한 회색

    rows = ""
    for i, line in enumerate(top3):
        color = accent_colors[i % len(accent_colors)]
        rows += f"""
        <tr>
          <td style="padding:0 0 10px 0;">
            <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
              <tr>
                <td width="4" style="background-color:{color};border-radius:3px;">&nbsp;</td>
                <td style="padding:10px 14px;background-color:#ffffff;border-radius:0 8px 8px 0;
                            font-size:14px;line-height:22px;color:#1e293b;border:1px solid #e5e7eb;border-left:none;">
                  {line}
                </td>
              </tr>
            </table>
          </td>
        </tr>"""

    return f"""<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">{rows}</table>"""


# ── HTML 생성 ────────────────────────────────────────────────
def to_html(all_articles):
    summary_html = build_summary_html(all_articles)
    # ... 기존 변수들 ...

    return f"""<!DOCTYPE html>
<html lang="ko">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="margin:0;padding:0;background-color:#eef0f7;font-family:'Apple SD Gothic Neo','Malgun Gothic',Arial,sans-serif;color:#1f2937;">
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background-color:#eef0f7;">
<tr><td align="center" style="padding:28px 12px;">
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="900"
       style="max-width:900px;background-color:#ffffff;border-radius:20px;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,0.10);">

  <!-- 헤더 -->
  <tr>
    <td style="background: radial-gradient(...) ; padding:0;">  <!-- 기존 그라데이션 그대로 -->
      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
        <tr>
          <!-- 좌측 텍스트 -->
          <td style="padding:28px 0 24px 32px;vertical-align:middle;">
            <div style="font-size:13px;font-weight:800;letter-spacing:3px;color:rgba(147,197,253,0.85);margin-bottom:14px;font-family:Arial,sans-serif;">
              &#128225;&nbsp;&nbsp;WEEKLY APP MARKET NEWS
            </div>
            <!-- 제목: 90% 크기 -->
            <div style="margin-bottom:13px;line-height:1.1;font-family:'Apple SD Gothic Neo','Malgun Gothic',Arial,sans-serif;white-space:nowrap;">
              <span style="font-size:36px;font-weight:900;color:#ffffff;">주간&nbsp;</span>
              <span style="font-size:36px;font-weight:900;color:#93c5fd;">앱 마켓&nbsp;</span>
              <span style="font-size:36px;font-weight:900;color:#a78bfa;">뉴</span>
              <span style="font-size:36px;font-weight:900;color:#c084fc;">스</span>
              <span style="font-size:36px;font-weight:900;color:#f0abfc;">레</span>
              <span style="font-size:36px;font-weight:900;color:#f0abfc;">터</span>
            </div>
            <div style="font-size:14px;color:rgba(180,215,255,0.75);font-family:Arial,sans-serif;">
              &#9679; 검색 범위 : {week_ago} ~ {today}
            </div>
          </td>

          <!-- 1번 요청: 기존 폰 목업 완전 제거 → image2.png 삽입 -->
          <td style="padding:18px 20px 18px 8px;vertical-align:middle;text-align:center;width:340px;">
            <img src="{IMAGE2_BASE64}" 
                 style="max-width:320px;height:auto;display:block;border-radius:20px;"
                 alt="App Market Visual">
          </td>
        </tr>
      </table>
    </td>
  </tr>

  <!-- 통계 바 (기존 그대로) -->

  <!-- 인사말 -->

  <!-- 핵심 요약 (5번 요청: 연한 파란 배경 라운드 모서리 강화) -->
  <tr>
    <td style="padding:12px 32px 8px 32px;">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
             style="background-color:#f8f7ff;border-radius:18px;border:1px solid #e0d9ff;">  <!-- 라운드 강화 -->
        <tr>
          <td style="padding:18px 20px 10px 20px;">
            <div style="font-size:16px;font-weight:800;color:#1e1b4b;margin-bottom:14px;">&#128269; 핵심 요약</div>
            {summary_html}
          </td>
        </tr>
      </table>
    </td>
  </tr>

  <!-- 주요 기사 헤더 -->

  <!-- 기사 카드 (3번 요청: 회색 얇은 라운드 테두리 복구) -->
  {cards_html if total_count > 0 else empty_html}

  <!-- 푸터 -->

</table>
</td></tr>
</table>
</body></html>"""
