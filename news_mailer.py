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
POLICY_KEYWORDS = ["수수료", "정책", "규제", "법", "인하", "허용", "금지", "의무", "심사", "결제"]

def to_html(all_articles):
    # 핵심 요약 — 정책/수수료 관련 우선, 최대 3개, 개조식
    all_items = []
    for kw, articles in all_articles.items():
        for a in articles:
            src = a.get("summary") or a.get("title", "")
            sent = re.split(r"(?<=[.!?])\s+", src.strip())
            line = re.sub(r"[.。]+$", "", sent[0].strip())[:60] if sent else ""
            if not line:
                continue
            priority = sum(1 for pk in POLICY_KEYWORDS if pk in line or pk in a.get("title",""))
            all_items.append((priority, line))

    # 중복 제거 후 우선순위 정렬
    seen = set()
    summary_items = ""
    count = 0
    for _, line in sorted(all_items, key=lambda x: -x[0]):
        if count >= 3 or line in seen:
            continue
        seen.add(line)
        summary_items += f'<div style="font-size:15px;line-height:26px;color:#334155;">• {line}</div>'
        count += 1

    # 키워드별 고정 색상
    palette = ["#4f46e5","#db2777","#d97706","#059669","#2563eb","#dc2626","#7c3aed","#0891b2"]
    kw_colors = {kw: palette[i % len(palette)] for i, kw in enumerate(all_articles.keys())}

    # 기사 카드
    cards_html = ""
    for kw, articles in all_articles.items():
        color = kw_colors[kw]
        tag_bg = color + "18"
        for a in articles:
            cards_html += f"""
            <tr>
              <td style="padding:0 36px 16px 36px;">
                <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
                       style="border:1px solid #e5e7eb;border-radius:16px;overflow:hidden;">
                  <tr>
                    <td width="6" style="background-color:{color};border-radius:16px 0 0 16px;">&nbsp;</td>
                    <td style="padding:20px 24px;">
                      <div style="margin-bottom:8px;">
                        <span style="display:inline-block;background-color:{tag_bg};color:{color};
                                     font-size:12px;line-height:18px;font-weight:700;
                                     padding:4px 10px;border-radius:999px;">{kw}</span>
                      </div>
                      <div style="font-size:18px;line-height:28px;color:#111827;font-weight:800;
                                  font-family:'Apple SD Gothic Neo','Malgun Gothic',Arial,sans-serif;">
                        {a['title']}
                      </div>
                      <div style="padding-top:8px;font-size:14px;line-height:23px;color:#4b5563;">
                        {a['summary'] or '원문 링크를 확인해주세요.'}
                      </div>
                      <div style="padding-top:12px;font-size:13px;line-height:20px;color:#94a3b8;">
                        {a['date']}{' · ' + a['press'] if a.get('press') else ''}
                      </div>
                      <div style="padding-top:14px;">
                        <a href="{a['link']}" style="display:inline-block;background-color:#111827;
                           color:#ffffff;text-decoration:none;font-size:14px;font-weight:700;
                           padding:10px 16px;border-radius:10px;">🔗 기사보기</a>
                      </div>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>"""

    return f"""
    <html>
    <body style="margin:0;padding:0;background-color:#f3f6fb;
                 font-family:'Apple SD Gothic Neo','Malgun Gothic',Arial,sans-serif;color:#1f2937;">
    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
           style="background-color:#f3f6fb;">
      <tr>
        <td align="center" style="padding:32px 16px;">
          <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
                 style="max-width:760px;background-color:#ffffff;border-radius:20px;overflow:hidden;">

            <!-- 헤더 -->
            <tr>
              <td style="background-color:#16233b;padding:28px 36px;">
                <div style="font-size:14px;line-height:20px;color:#a9c3ff;font-weight:700;letter-spacing:0.4px;">
                  WEEKLY APP MARKET NEWS
                </div>
                <div style="padding-top:8px;font-size:30px;line-height:38px;color:#ffffff;font-weight:800;
                            font-family:'Apple SD Gothic Neo','Malgun Gothic',Arial,sans-serif;">
                  🗞 이번주 앱마켓 동향 기사
                </div>
                <div style="padding-top:10px;font-size:15px;line-height:22px;color:#dbeafe;">
                  검색 범위 : {week_ago} ~ {today}
                </div>
              </td>
            </tr>

            <!-- 인트로 -->
            <tr>
              <td style="padding:24px 36px 8px 36px;font-size:15px;line-height:24px;color:#475569;">
                안녕하세요.<br>
                이번 주 앱마켓 관련 주요 기사와 핵심 이슈를 정리해 공유드립니다.
              </td>
            </tr>

            <!-- 핵심 요약 -->
            <tr>
              <td style="padding:16px 36px 8px 36px;">
                <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
                       style="background-color:#e8f4fd;border:1px solid #bfdbfe;border-radius:16px;">
                  <tr>
                    <td style="padding:20px 24px;">
                      <div style="font-size:17px;line-height:26px;font-weight:800;color:#0f172a;margin-bottom:12px;">
                        🔎 이번주 핵심 요약
                      </div>
                      {summary_items if summary_items else '<div style="font-size:14px;color:#94a3b8;">이번 주 주요 내용을 찾지 못했습니다.</div>'}
                    </td>
                  </tr>
                </table>
              </td>
            </tr>

            <!-- 주요기사 타이틀 -->
            <tr>
              <td style="padding:24px 36px 12px 36px;">
                <div style="font-size:22px;line-height:30px;font-weight:800;color:#0f172a;">
                  📰 주요 기사
                </div>
              </td>
            </tr>

            <!-- 기사 카드들 -->
            {cards_html if cards_html else '<tr><td style="padding:0 36px 24px;color:#94a3b8;">이번 주 관련 기사를 찾지 못했습니다.</td></tr>'}

            <!-- 푸터 -->
            <tr>
              <td style="border-top:1px solid #e5e7eb;padding:20px 36px 28px 36px;
                         font-size:13px;line-height:22px;color:#94a3b8;">
                자동 발송 · {today} · 네이버 뉴스
              </td>
            </tr>

          </table>
        </td>
      </tr>
    </table>
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
