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

# ── 이미지 Base64 (GitHub 업로드용) ─────────────────────────
ICON_PATH = "icon.png"
with open(ICON_PATH, "rb") as f:
    _ext = os.path.splitext(ICON_PATH)[-1].lstrip(".").replace("jpg", "jpeg")
    ICON_BASE64 = f"data:image/{_ext};base64," + base64.b64encode(f.read()).decode()

IMAGE2_PATH = "image2.png"
with open(IMAGE2_PATH, "rb") as f:
    _ext2 = os.path.splitext(IMAGE2_PATH)[-1].lstrip(".").replace("jpg", "jpeg")
    IMAGE2_BASE64 = f"data:image/{_ext2};base64," + base64.b64encode(f.read()).decode()

# ── 봇 차단 도메인 ────────────────────────────────────────────
BOT_BLOCKED_DOMAINS = {
    "v.daum.net", "daum.net",
    "news.nate.com", "nate.com",
    "naver.com",
}

def is_blocked_domain(url: str) -> bool:
    domain = get_domain(url)
    return any(domain == d or domain.endswith("." + d) for d in BOT_BLOCKED_DOMAINS)


# ── 유틸 함수 ───────────────────────────────────────────────
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

PRESS_MAP = { ... }  # (기존 PRESS_MAP 그대로 유지)

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

def make_absolute_url(base_url: str, img_url: str) -> str:
    if not img_url:
        return ""
    if img_url.startswith(("http://", "https://")):
        return img_url
    if img_url.startswith("//"):
        return "https:" + img_url
    parsed = urlparse(base_url)
    if img_url.startswith("/"):
        return f"{parsed.scheme}://{parsed.netloc}{img_url}"
    base_path = parsed.path.rsplit("/", 1)[0] + "/"
    return f"{parsed.scheme}://{parsed.netloc}{base_path}{img_url.lstrip('./')}"

def is_valid_snippet(text: str) -> bool:
    if not text or len(text) < 20:
        return False
    if "google news" in text.lower():
        return False
    if text.count(",") + text.count("，") >= 8:
        return False
    return True

# get_article_info, dedupe_articles, build_search_query, is_relevant_article, score_article,
# fetch_naver_articles, fetch_google_articles, extract_best_sentence, _trim 함수들은 기존 그대로 유지

# ── 핵심 요약 생성 ────────────────────────────────────────────
POLICY_KEYWORDS = [
    "수수료", "정책", "규제", "법", "인하", "허용", "금지", "의무",
    "심사", "결제", "소송", "방통위", "공정위", "안티스티어링",
    "외부결제", "인앱결제", "제3자결제", "사이드로딩"
]

def build_summary_html(all_articles):
    items = []
    seen_texts = set()

    for kw, articles in all_articles.items():
        for a in articles:
            src = a.get("summary") or ""
            line = extract_best_sentence(src, a.get("title", ""))
            if not line or line in seen_texts:
                continue
            seen_texts.add(line)
            priority = sum(1 for pk in POLICY_KEYWORDS if pk in line or pk in a.get("title",""))
            items.append((priority, line))

    items.sort(key=lambda x: -x[0])
    top3 = []
    seen2 = set()
    for _, line in items:
        if line in seen2:
            continue
        seen2.add(line)
        top3.append(line)
        if len(top3) >= 3:
            break

    if not top3:
        return '<div style="font-size:14px;color:#94a3b8;padding:4px 0;">이번 주 주요 내용을 찾지 못했습니다.</div>'

    # 4번 요청: 파란/빨강/초록 → 진한 회색
    accent_colors = ["#475569"] * 3
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
    palette = ["#4f46e5", "#db2777", "#d97706", "#059669", "#2563eb", "#dc2626", "#7c3aed", "#0891b2"]
    kw_colors = {kw: palette[i % len(palette)] for i, kw in enumerate(all_articles.keys())}

    article_count = sum(len(v) for v in all_articles.values())
    issue_count   = len(all_articles)

    cards_html = ""
    total_count = 0
    for kw, articles in all_articles.items():
        color = kw_colors[kw]
        tag_bg = color + "18"
        for a in articles:
            total_count += 1
            img_url = a.get("image") or ""

            if img_url:
                safe_img_url = quote(img_url, safe=":/")
                parsed_img = urlparse(img_url)
                img_referer = quote(f"{parsed_img.scheme}://{parsed_img.netloc}/", safe="")
                proxy_url = (
                    f"https://wsrv.nl/?url={safe_img_url}"
                    f"&w=240&h=180&fit=cover"
                    f"&referer={img_referer}"
                    f"&default=1"
                )
                image_td = f'''
                  <td width="130" style="padding:11px 0 11px 12px;vertical-align:top;">
                    <img src="{proxy_url}" width="120" height="90"
                         style="width:120px;height:90px;border-radius:10px;display:block;background-color:#f8fafc;" alt="">
                  </td>
                '''
                text_pl = "8px"
            else:
                image_td = ""
                text_pl  = "16px"

            summary_text = a.get("summary", "")
            if not summary_text or normalize_text(summary_text) == normalize_text(a["title"]):
                summary_text = "원문 링크를 확인해주세요."

            cards_html += f"""
            <tr>
              <td style="padding:0 32px 10px 32px;">
                <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
                       style="border:1.5px solid #e5e7eb;border-radius:14px;overflow:hidden;background-color:#ffffff;">
                  <tr>
                    <td width="5" style="background-color:{color};font-size:0;">&nbsp;</td>
                    {image_td}
                    <td style="padding:12px 18px 12px {text_pl};vertical-align:top;background-color:#ffffff;">
                      <div style="margin-bottom:6px;">
                        <span style="display:inline-block;background-color:{tag_bg};color:{color};
                                     font-size:11px;font-weight:700;padding:2px 9px;border-radius:999px;">{kw}</span>
                      </div>
                      <div style="font-size:16px;line-height:24px;color:#111827;font-weight:800;
                                  font-family:'Apple SD Gothic Neo','Malgun Gothic',Arial,sans-serif;">
                        {a['title']}
                      </div>
                      <div style="padding-top:5px;font-size:13px;line-height:21px;color:#4b5563;">
                        {summary_text}
                      </div>
                      <div style="padding-top:8px;">
                        <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
                          <tr>
                            <td style="font-size:11px;color:#9ca3af;vertical-align:middle;">
                              {a['date']}{(' &middot; ' + a['press']) if a.get('press') else ''}
                            </td>
                            <td style="text-align:right;vertical-align:middle;">
                              <a href="{a['link']}" style="display:inline-block;color:#ffffff;text-decoration:none;
                                 font-size:12px;font-weight:700;padding:5px 12px;border-radius:7px;
                                 background-color:#374151;">&#128279; 원문보기</a>
                            </td>
                          </tr>
                        </table>
                      </div>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>"""

    empty_html = '<tr><td style="padding:0 32px 24px;color:#94a3b8;">이번 주 관련 기사를 찾지 못했습니다.</td></tr>'

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
    <td style="background:
        radial-gradient(ellipse at 18% 55%, rgba(99,102,241,0.55) 0%, transparent 52%),
        radial-gradient(ellipse at 82% 18%, rgba(0,212,255,0.28) 0%, transparent 46%),
        radial-gradient(ellipse at 52% 95%, rgba(168,85,247,0.38) 0%, transparent 48%),
        linear-gradient(135deg, #0f0c29 0%, #302b63 55%, #1a1a4e 100%);
        padding:0;">
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

          <!-- image2.png (1번 요청) -->
          <td style="padding:18px 20px 18px 8px;vertical-align:middle;text-align:center;width:340px;">
            <img src="{IMAGE2_BASE64}" 
                 style="max-width:320px;height:auto;display:block;border-radius:20px;"
                 alt="App Market Visual">
          </td>
        </tr>
      </table>
    </td>
  </tr>

  <!-- 통계 바 -->
  <tr>
    <td style="background-color:#1e1e42;padding:7px 0;">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
        <tr>
          <td width="33%" style="text-align:center;padding:3px 0;border-right:1px solid rgba(255,255,255,0.1);">
            <div style="font-size:15px;font-weight:700;color:#93c5fd;">{article_count}</div>
            <div style="font-size:10px;color:rgba(180,200,240,0.6);margin-top:1px;">주요 기사</div>
          </td>
          <td width="33%" style="text-align:center;padding:3px 0;border-right:1px solid rgba(255,255,255,0.1);">
            <div style="font-size:15px;font-weight:700;color:#93c5fd;">{issue_count}</div>
            <div style="font-size:10px;color:rgba(180,200,240,0.6);margin-top:1px;">핵심 이슈</div>
          </td>
          <td width="33%" style="text-align:center;padding:3px 0;">
            <div style="font-size:14px;font-weight:700;color:#93c5fd;">{week_label}</div>
            <div style="font-size:10px;color:rgba(180,200,240,0.6);margin-top:1px;">이번 주 호</div>
          </td>
        </tr>
      </table>
    </td>
  </tr>

  <!-- 인사말 -->
  <tr>
    <td style="padding:22px 32px 10px 32px;font-size:14px;line-height:22px;color:#475569;">
      안녕하세요.<br>
      이번 주 앱마켓 관련 주요 기사와 핵심 이슈를 정리해 공유드립니다.
    </td>
  </tr>

  <!-- 핵심 요약 (5번 요청: 라운드 모서리) -->
  <tr>
    <td style="padding:12px 32px 8px 32px;">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
             style="background-color:#f8f7ff;border-radius:18px;border:1px solid #e0d9ff;">
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
  <tr>
    <td style="padding:20px 32px 10px 32px;">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
        <tr>
          <td style="font-size:18px;font-weight:800;color:#0f172a;white-space:nowrap;padding-right:12px;">
            &#128240; 주요 기사
          </td>
          <td width="100%">
            <div style="height:2px;background-color:#e0d9ff;border-radius:2px;"></div>
          </td>
        </tr>
      </table>
    </td>
  </tr>

  <!-- 기사 카드 (3번 요청: 회색 얇은 라운드 테두리 복구) -->
  {cards_html if total_count > 0 else empty_html}

  <!-- 푸터 -->
  <tr>
    <td style="background-color:#1e1e42;padding:18px 32px;text-align:center;border-radius:0 0 20px 20px;">
      <div style="font-size:14px;font-weight:900;color:#ffffff;margin-bottom:6px;">
        &#128241; 앱 마켓 <span style="color:#a78bfa;">뉴스레터</span>
      </div>
      <div style="font-size:11px;color:rgba(180,200,240,0.65);line-height:1.9;">
        매주 월요일 발행 &middot; 구독 문의: hj@kisa.or.kr<br>
        자동 발송 &middot; {today}
      </div>
    </td>
  </tr>

</table>
</td></tr>
</table>
</body></html>"""


# ── 메일 발송 ───────────────────────────────────────────────
def send_mail(html):
    recipients = [x.strip() for x in MAIL_TO.split(",") if x.strip()]
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[앱 마켓 뉴스 레터] {today}"
    msg["From"] = f"{GMAIL_ID}@gmail.com"
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(GMAIL_ID, GMAIL_PW)
        smtp.sendmail(msg["From"], recipients, msg.as_string())
    print(f"✅ 메일 발송 완료 → {', '.join(recipients)}")


# ── 실행 ────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🔍 네이버 + 구글 뉴스 탐색 중...")

    all_articles = {}
    for kw in KEYWORDS:
        print(f"  - {kw} 검색 중... (Naver + Google)")
        naver_arts = fetch_naver_articles(kw)
        google_arts = fetch_google_articles(kw)
        combined = naver_arts + google_arts
        combined.sort(key=lambda x: x.get("score", 0), reverse=True)
        combined = dedupe_articles(combined)
        combined = [a for a in combined if a.get("score", 0) >= MIN_ARTICLE_SCORE]
        if combined:
            all_articles[kw] = combined[:3]
            print(f"    → {len(all_articles[kw])}건 수집")

    print("🔄 전역 중복 제거 중...")
    link_to_entries = defaultdict(list)
    for kw, arts in all_articles.items():
        for art in arts:
            link = art.get("link", "")
            if link:
                link_to_entries[link].append((kw, art))

    to_remove = defaultdict(list)
    for link, entries in link_to_entries.items():
        if len(entries) <= 1:
            continue
        entries.sort(key=lambda x: (len(all_articles[x[0]]), -x[1].get("score", 0)))
        for kw, art in entries[1:]:
            to_remove[kw].append(art["link"])

    removed_count = 0
    for kw, remove_links in to_remove.items():
        if kw not in all_articles:
            continue
        original = len(all_articles[kw])
        all_articles[kw] = [a for a in all_articles[kw] if a.get("link") not in set(remove_links)]
        removed = original - len(all_articles[kw])
        if removed:
            print(f"  [{kw}] 중복 제거: {removed}건")
            removed_count += removed

    all_articles = {k: v for k, v in all_articles.items() if v}
    total_found = sum(len(v) for v in all_articles.values())
    print(f"✅ 중복 제거 완료 (제거: {removed_count}건, 최종: {total_found}건)")

    html = to_html(all_articles)
    send_mail(html)
    print(f"✅ 전체 완료 (발송 기사: {total_found}건)")
