import os, json, smtplib
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.request import urlopen, Request
from urllib.parse import quote

# ── 설정 ────────────────────────────────────────────────────
GMAIL_ID = os.environ["GMAIL_ID"]
GMAIL_PW = os.environ["GMAIL_APP_PASSWORD"]
MAIL_TO  = os.environ["MAIL_TO"]
KEYWORDS = json.loads(os.environ["KEYWORDS"])

today    = datetime.now(timezone.utc).strftime("%Y년 %m월 %d일")
week_ago = datetime.now(timezone.utc) - timedelta(days=7)

# ── Google News RSS 서치 ────────────────────────────────────
def fetch_articles(keyword):
    url = f"https://news.google.com/rss/search?q={quote(keyword)}&hl=ko&gl=KR&ceid=KR:ko"
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    xml = urlopen(req, timeout=15).read()
    root = ET.fromstring(xml)

    articles = []
    for item in root.findall(".//item"):
        title   = item.findtext("title", "").strip()
        link    = item.findtext("link", "").strip()
        pub_str = item.findtext("pubDate", "")
        desc    = item.findtext("description", "").strip()

        # 날짜 파싱
        try:
            from email.utils import parsedate_to_datetime
            pub_dt = parsedate_to_datetime(pub_str).astimezone(timezone.utc)
        except Exception:
            continue

        # 1주일 이내 기사만
        if pub_dt < week_ago:
            continue

        # 태그 제거
        import re
        desc = re.sub(r"<[^>]+>", "", desc).strip()
        pub_label = pub_dt.strftime("%Y.%m.%d")

        articles.append({
            "title":   title,
            "link":    link,
            "summary": desc[:200] if desc else "",
            "date":    pub_label,
        })

    return articles[:3]  # 키워드당 최대 3개

# ── HTML 변환 ───────────────────────────────────────────────
def to_html(all_articles):
    kw_tags = "".join(
        f'<span style="background:#eff6ff;color:#2563eb;border-radius:20px;'
        f'padding:4px 12px;font-size:13px;font-weight:600;margin:3px;">{k}</span>'
        for k in KEYWORDS
    )

    sections = []
    for kw, articles in all_articles.items():
        cards = ""
        if not articles:
            cards = '<p style="color:#94a3b8;font-size:13px;">최근 1주일 내 관련 기사를 찾지 못했습니다.</p>'
        else:
            for a in articles:
                cards += f"""
                <div style="background:#fff;border-left:4px solid #3b82f6;border-radius:10px;
                            padding:18px 20px;margin-bottom:12px;box-shadow:0 1px 6px rgba(0,0,0,.07);">
                  <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:6px;">
                    <strong style="font-size:14px;color:#1e293b;line-height:1.5;">{a['title']}</strong>
                    <a href="{a['link']}" style="flex-shrink:0;font-size:12px;padding:4px 10px;
                       background:#eff6ff;color:#2563eb;border-radius:5px;text-decoration:none;
                       white-space:nowrap;">원문 →</a>
                  </div>
                  <p style="font-size:13px;color:#475569;line-height:1.7;margin:0;">{a['summary']}</p>
                  <p style="font-size:11px;color:#94a3b8;margin:6px 0 0;">{a['date']}</p>
                </div>"""

        sections.append(f"""
        <div style="margin-bottom:28px;">
          <h2 style="font-size:15px;color:#1e293b;margin:0 0 12px;padding-bottom:8px;
                     border-bottom:2px solid #e2e8f0;">🔍 {kw}</h2>
          {cards}
        </div>""")

    week_ago_str = week_ago.strftime("%Y년 %m월 %d일")
    return f"""
    <html><body style="margin:0;padding:0;background:#f5f7fa;font-family:'Malgun Gothic',sans-serif;">
    <div style="max-width:800px;margin:0 auto;">
      <div style="background:#1e293b;padding:24px 32px;">
        <h1 style="color:#fff;margin:0;font-size:22px;">📰 오늘의 뉴스 서치</h1>
        <p style="color:#94a3b8;margin:6px 0 0;font-size:13px;">
          검색 범위: <strong style="color:#cbd5e1;">{week_ago_str} ~ {today}</strong>
        </p>
      </div>
      <div style="padding:24px 16px;">
        <div style="margin-bottom:24px;">{kw_tags}</div>
        {''.join(sections)}
      </div>
      <div style="text-align:center;padding:16px;color:#94a3b8;font-size:12px;">
        자동 발송 · {today} · Google News
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
    print("🔍 Google News 탐색 중...")
    all_articles = {}
    for kw in KEYWORDS:
        print(f"  - {kw} 검색 중...")
        all_articles[kw] = fetch_articles(kw)
    html = to_html(all_articles)
    send_mail(html)
    print("✅ 완료")
