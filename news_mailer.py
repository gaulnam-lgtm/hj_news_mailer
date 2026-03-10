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
    week_ago_dt = datetime.now(timezone.utc) - timedelta(days=7)

    for item in data.get("items", []):
        title   = re.sub(r"<[^>]+>", "", item.get("title", "")).strip()
        link    = item.get("originallink") or item.get("link", "")
        desc    = re.sub(r"<[^>]+>", "", item.get("description", "")).strip()
        pub_str = item.get("pubDate", "")

        # 날짜 파싱 + 1주일 이내 필터링
        try:
            from email.utils import parsedate_to_datetime
            pub_dt = parsedate_to_datetime(pub_str).astimezone(timezone.utc)
            if pub_dt < week_ago_dt:
                continue  # 1주일 이전 기사 제외
            pub_label = pub_dt.strftime("%Y.%m.%d")
        except Exception:
            continue

        # 언론사 추출
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
    # 핵심 요약 — 키워드별 첫 기사 요약 첫 문장 추출 후 최대 4개
    summary_items = ""
    count = 0
    seen = set()
    for kw, articles in all_articles.items():
        if count >= 4:
            break
        for a in articles:
            if count >= 4:
                break
            src = a.get("summary") or a.get("title", "")
            # 첫 문장 추출
            sent = re.split(r"(?<=[.!?])\s+", src.strip())
            line = sent[0][:80] if sent else src[:80]
            if line and line not in seen:
                seen.add(line)
                summary_items += f'<li style="margin-bottom:9px;color:#374151;font-size:14px;line-height:1.7;">{line}</li>'
                count += 1

    # 기사 카드
    cards_html = ""
    border_colors = ["#6366f1","#ec4899","#f59e0b","#10b981","#3b82f6","#ef4444","#8b5cf6","#14b8a6"]
    color_idx = 0
    for kw, articles in all_articles.items():
        for a in articles:
            color = border_colors[color_idx % len(border_colors)]
            cards_html += f"""
            <div style="background:#fff;border-radius:12px;padding:24px 28px;margin-bottom:20px;
                        box-shadow:0 1px 8px rgba(0,0,0,.07);border-left:5px solid {color};">
              <span style="display:inline-block;background:{color}18;color:{color};
                           border-radius:20px;padding:3px 12px;font-size:12px;font-weight:700;
                           margin-bottom:12px;">{kw}</span>
              <h3 style="margin:0 0 10px;font-size:17px;font-weight:800;
                         font-family:'Apple SD Gothic Neo','Malgun Gothic',sans-serif;
                         color:#111827;line-height:1.5;">{a['title']}</h3>
              <p style="margin:0 0 14px;font-size:13.5px;color:#4b5563;line-height:1.8;">{a['summary'] or '원문 링크를 확인해주세요.'}</p>
              <div style="display:flex;justify-content:space-between;align-items:center;">
                <span style="font-size:11.5px;color:#9ca3af;">{a['date']}{' · ' + a['press'] if a.get('press') else ''}</span>
                <a href="{a['link']}" style="display:inline-flex;align-items:center;gap:6px;
                   background:#111827;color:#ffffff;border-radius:8px;padding:7px 16px;
                   font-size:13px;font-weight:600;text-decoration:none;">🔗 기사보기</a>
              </div>
            </div>"""
            color_idx += 1

    return f"""
    <html><body style="margin:0;padding:0;background:#e8f4fd;font-family:'Apple SD Gothic Neo','Malgun Gothic',sans-serif;">
    <div style="max-width:1200px;margin:0 auto;padding:32px 24px;">

      <!-- 헤더 -->
      <div style="background:linear-gradient(to right, #1e3a5f 0%, #2563eb 60%, #60a5fa 100%);
                  border-radius:16px 16px 0 0;padding:28px 40px;margin-bottom:0;">
        <p style="color:#a9c3ff;font-size:14px;font-weight:700;letter-spacing:0.4px;margin:0 0 8px;">
          WEEKLY MARKET NEWS
        </p>
        <h1 style="color:#ffffff;margin:0 0 10px;font-size:30px;font-weight:800;
                   font-family:'Apple SD Gothic Neo','Malgun Gothic',sans-serif;">
          이번주 앱마켓 동향 기사
        </h1>
        <p style="color:#dbeafe;margin:0;font-size:15px;font-weight:400;">
          검색 범위 : <strong style="color:#dbeafe;">{week_ago} ~ {today}</strong>
        </p>
      </div>

      <!-- 인사말 -->
      <p style="margin:20px 4px 20px;color:#374151;font-size:14px;line-height:1.8;">
        안녕하세요.<br>
        이번 주 앱마켓 관련 주요 기사와 핵심 이슈를 정리해 공유드립니다.
      </p>

      <!-- 핵심 요약 -->
      <div style="background:#fff;border-radius:12px;padding:24px 28px;margin-bottom:28px;
                  box-shadow:0 1px 6px rgba(0,0,0,.06);">
        <h2 style="margin:0 0 16px;font-size:16px;color:#1e293b;font-weight:700;">🔍 이번주 핵심 요약</h2>
        <ul style="margin:0;padding-left:20px;line-height:1.9;">
          {summary_items if summary_items else '<li style="color:#9ca3af;">이번 주 주요 내용을 찾지 못했습니다.</li>'}
        </ul>
      </div>

      <!-- 주요 기사 -->
      <h2 style="font-size:18px;color:#1e293b;font-weight:800;margin:0 0 16px;">
        📰 주요 기사
      </h2>
      {cards_html if cards_html else '<p style="color:#94a3b8;">이번 주 관련 기사를 찾지 못했습니다.</p>'}

      <!-- 푸터 -->
      <div style="text-align:center;padding:20px;color:#9ca3af;font-size:12px;">
        자동 발송 · {today} · 네이버 뉴스
      </div>
    </div>
    </body></html>"""

# ── 메일 발송 ───────────────────────────────────────────────
def send_mail(html):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[이번주 앱마켓 동향 기사] {today}"
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

    # 기사 있는 키워드만 포함, 없는 키워드는 제외
    all_articles = {kw: arts for kw, arts in all_articles.items() if arts}

    html = to_html(all_articles)
    send_mail(html)
    print("✅ 완료")
