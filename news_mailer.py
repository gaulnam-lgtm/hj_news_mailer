import os, json, smtplib, re
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.request import Request, urlopen
from urllib.parse import quote

# ── 설정 ────────────────────────────────────────────────────
GMAIL_ID          = os.environ["GMAIL_ID"]
GMAIL_PW          = os.environ["GMAIL_APP_PASSWORD"]
MAIL_TO           = os.environ["MAIL_TO"]
KEYWORDS          = json.loads(os.environ["KEYWORDS"])
NAVER_CLIENT_ID   = os.environ["NAVER_CLIENT_ID"]
NAVER_CLIENT_SECRET = os.environ["NAVER_CLIENT_SECRET"]

today    = datetime.now(timezone.utc).strftime("%Y년 %m월 %d일")
week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y년 %m월 %d일")

# ── 네이버 뉴스 검색 ────────────────────────────────────────
def fetch_articles(keyword):
    url = f"https://openapi.naver.com/v1/search/news.json?query={quote(keyword)}&display=5&sort=date"
    req = Request(url)
    req.add_header("X-Naver-Client-Id", NAVER_CLIENT_ID)
    req.add_header("X-Naver-Client-Secret", NAVER_CLIENT_SECRET)

    resp = urlopen(req, timeout=10)
    data = json.loads(resp.read().decode("utf-8"))

    articles = []
    for item in data.get("items", []):
        title   = re.sub(r"<[^>]+>", "", item.get("title", "")).strip()
        link    = item.get("originallink") or item.get("link", "")
        desc    = re.sub(r"<[^>]+>", "", item.get("description", "")).strip()
        pub_str = item.get("pubDate", "")

        # 날짜 파싱
        try:
            from email.utils import parsedate_to_datetime
            pub_dt = parsedate_to_datetime(pub_str)
            pub_label = pub_dt.strftime("%Y.%m.%d")
        except Exception:
            pub_label = ""

        # 언론사 추출 (link 도메인에서)
        press_match = re.search(r"https?://(?:www\.)?([^/]+)", link)
        press = press_match.group(1) if press_match else ""

        print(f"  [{keyword}] {title[:40]} | {desc[:40]}")

        articles.append({
            "title":   title,
            "press":   press,
            "link":    link,
            "summary": desc,
            "date":    pub_label,
        })

    return articles[:3]

# ── HTML 변환 ───────────────────────────────────────────────
def to_html(all_articles):
    sections = []
    for kw, articles in all_articles.items():
        cards = ""
        if not articles:
            cards = '<p style="color:#94a3b8;font-size:13px;">최근 1주일 내 관련 기사를 찾지 못했습니다.</p>'
        else:
            for a in articles:
                summary_html = a['summary'] if a['summary'] else '<span style="color:#94a3b8;">원문 링크를 확인해주세요.</span>'
                cards += f"""
                <div style="background:#fff;border-left:4px solid #3b82f6;border-radius:10px;
                            padding:18px 20px;margin-bottom:12px;box-shadow:0 1px 6px rgba(0,0,0,.07);">
                  <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:6px;">
                    <strong style="font-size:14px;color:#1e293b;line-height:1.5;">{a['title']}</strong>
                    <a href="{a['link']}" style="flex-shrink:0;font-size:12px;padding:4px 10px;
                       background:#eff6ff;color:#2563eb;border-radius:5px;text-decoration:none;
                       white-space:nowrap;">원문 →</a>
                  </div>
                  <p style="font-size:13px;color:#475569;line-height:1.7;margin:0;">{summary_html}</p>
                  <p style="font-size:11px;color:#94a3b8;margin:6px 0 0;">{a['date']}{' · ' + a['press'] if a.get('press') else ''}</p>
                </div>"""

        sections.append(f"""
        <div style="margin-bottom:28px;">
          <h2 style="font-size:15px;color:#1e293b;margin:0 0 12px;padding-bottom:8px;
                     border-bottom:2px solid #e2e8f0;">🔍 {kw}</h2>
          {cards}
        </div>""")

    return f"""
    <html><body style="margin:0;padding:0;background:#f5f7fa;font-family:'Malgun Gothic',sans-serif;">
    <div style="max-width:800px;margin:0 auto;">
      <div style="background:#1e293b;padding:24px 32px;">
        <h1 style="color:#fff;margin:0;font-size:22px;">📰 오늘의 뉴스 서치</h1>
        <p style="color:#94a3b8;margin:6px 0 0;font-size:13px;">
          검색 범위: <strong style="color:#cbd5e1;">{week_ago} ~ {today}</strong>
        </p>
      </div>
      <div style="padding:24px 16px;">
        {''.join(sections)}
      </div>
      <div style="text-align:center;padding:16px;color:#94a3b8;font-size:12px;">
        자동 발송 · {today} · 네이버 뉴스
      </div>
    </div>
    </body></html>"""

# ── 메일 발송 ───────────────────────────────────────────────
def send_mail(html):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[뉴스 서치] {today} 주간 동향"
    msg["From"]    = f"{GMAIL_ID}@gmail.com"
    msg["To"]      = MAIL_TO
    msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(GMAIL_ID, GMAIL_PW)
        s.send_message(msg)
    print(f"✅ 메일 발송 완료 → {MAIL_TO}")

# ── 실행 ────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🔍 네이버 뉴스 탐색 중...")
    all_articles = {}
    for kw in KEYWORDS:
        print(f"  - {kw} 검색 중...")
        all_articles[kw] = fetch_articles(kw)

    # 기사 있는 키워드 먼저
    all_articles = dict(sorted(all_articles.items(), key=lambda x: 0 if x[1] else 1))

    html = to_html(all_articles)
    send_mail(html)
    print("✅ 완료")
