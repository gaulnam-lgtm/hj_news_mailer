import os, json, smtplib, re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.request import urlopen, Request
from urllib.parse import quote
import trafilatura
import requests
from html.parser import HTMLParser

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0",
    "Accept-Language": "ko-KR,ko;q=0.9",
}

# ── og:description 파서 ──────────────────────────────────────
class MetaParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.description = ""

    def handle_starttag(self, tag, attrs):
        if tag == "meta":
            d = dict(attrs)
            if d.get("property") in ("og:description", "twitter:description") \
               or d.get("name") in ("description",):
                val = d.get("content", "").strip()
                if val and len(val) > len(self.description):
                    self.description = val

# ── 설정 ────────────────────────────────────────────────────
GMAIL_ID = os.environ["GMAIL_ID"]
GMAIL_PW = os.environ["GMAIL_APP_PASSWORD"]
MAIL_TO  = os.environ["MAIL_TO"]
KEYWORDS = json.loads(os.environ["KEYWORDS"])

today    = datetime.now(timezone.utc).strftime("%Y년 %m월 %d일")
week_ago = datetime.now(timezone.utc) - timedelta(days=7)

# ── Google 리다이렉트 URL → 실제 기사 URL 추출 ──────────────
def resolve_url(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10, allow_redirects=True)
        print(f"    resolve: {url[:60]} → {resp.url[:60]}")
        return resp.url
    except Exception as e:
        print(f"    resolve 실패: {e}")
        return url

# ── 기사 본문에서 요약 추출 ─────────────────────────────────
def fetch_summary(url):
    # 방법 1: og:description 메타태그
    try:
        real_url = resolve_url(url)
        resp = requests.get(real_url, headers=HEADERS, timeout=10, allow_redirects=True)
        resp.encoding = resp.apparent_encoding
        html = resp.text[:15000]

        parser = MetaParser()
        parser.feed(html)
        print(f"    og:description: {parser.description[:80] if parser.description else '없음'}")
        if parser.description and len(parser.description) > 20:
            return parser.description[:300]
    except Exception as e:
        print(f"    og 추출 실패: {e}")

    # 방법 2: trafilatura 폴백
    try:
        real_url = resolve_url(url)
        downloaded = trafilatura.fetch_url(real_url)
        if downloaded:
            text = trafilatura.extract(downloaded, include_comments=False, include_tables=False)
            if text:
                sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if len(s.strip()) > 20]
                result = " ".join(sentences[:3])[:300]
                print(f"    trafilatura: {result[:80]}")
                return result
    except Exception as e:
        print(f"    trafilatura 실패: {e}")

    return ""

# ── Google News RSS 서치 ────────────────────────────────────
def fetch_articles(keyword):
    url = f"https://news.google.com/rss/search?q={quote(keyword)}&hl=ko&gl=KR&ceid=KR:ko"
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    xml = urlopen(req, timeout=15).read()
    root = ET.fromstring(xml)

    from email.utils import parsedate_to_datetime
    articles = []
    for item in root.findall(".//item"):
        title   = item.findtext("title", "").strip()
        link    = item.findtext("link", "").strip()
        pub_str = item.findtext("pubDate", "")

        # 제목에서 언론사 분리 (예: "제목 - 한스경제" → 제목 / 언론사 따로)
        press_match = re.search(r"\s+-\s+([^-]+)$", title)
        press = press_match.group(1).strip() if press_match else ""
        title = re.sub(r"\s+-\s+[^-]+$", "", title).strip()

        # 날짜 파싱
        try:
            pub_dt = parsedate_to_datetime(pub_str).astimezone(timezone.utc)
        except Exception:
            continue

        # 1주일 이내 기사만
        if pub_dt < week_ago:
            continue

        pub_label = pub_dt.strftime("%Y.%m.%d")

        # 본문 요약 가져오기
        summary = fetch_summary(link)

        articles.append({
            "title":   title,
            "press":   press,
            "link":    link,
            "summary": summary,
            "date":    pub_label,
        })

        if len(articles) >= 3:
            break

    return articles

# ── HTML 변환 ───────────────────────────────────────────────
def to_html(all_articles):
    # 기사 있는 키워드 먼저, 없는 키워드 뒤로 정렬
    sorted_articles = dict(
        sorted(all_articles.items(), key=lambda x: 0 if x[1] else 1)
    )

    sections = []
    for kw, articles in sorted_articles.items():
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
                  <p style="font-size:13px;color:#475569;line-height:1.7;margin:0;">{a['summary'] if a['summary'] else '요약을 가져올 수 없습니다. 원문 링크를 확인해주세요.'}</p>
                  <p style="font-size:11px;color:#94a3b8;margin:6px 0 0;">{a['date']}{' · ' + a['press'] if a.get('press') else ''}</p>
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
