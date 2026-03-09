import os, json, smtplib, anthropic
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# ── 설정 ────────────────────────────────────────────────────
API_KEY     = os.environ["ANTHROPIC_API_KEY"]
NAVER_ID    = os.environ["NAVER_ID"]
NAVER_PW    = os.environ["NAVER_PASSWORD"]
MAIL_TO     = os.environ["MAIL_TO"]
KEYWORDS    = json.loads(os.environ["KEYWORDS"])

today    = datetime.now().strftime("%Y년 %m월 %d일")
week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y년 %m월 %d일")

# ── 뉴스 서치 ───────────────────────────────────────────────
def fetch_news():
    client = anthropic.Anthropic(api_key=API_KEY)
    prompt = f"""{week_ago}부터 {today}까지 최근 1주일 이내에 발행된 뉴스 기사를 아래 키워드별로 2~3개씩 웹에서 검색해줘.

키워드: {", ".join(KEYWORDS)}

검색 시 주의사항:
- 키워드와 정확히 일치하지 않아도 돼. 띄어쓰기 차이는 무시하고, 의미가 유사하거나 관련 있는 주제의 기사도 포함해줘.
- 각 키워드의 상위 개념, 하위 개념, 연관 이슈까지 폭넓게 탐색해줘.

각 기사는 반드시 아래 형식으로 출력해줘:

**[기사 제목]**
- URL: (실제 기사 원문 링크, 없으면 생략)
- 요약: (핵심 내용 2~3문장, 한국어로)

1주일 이내 기사가 없는 키워드는 "관련 최신 동향"으로 대체해줘."""

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4000,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[{"role": "user", "content": prompt}]
    )
    return "\n".join(b.text for b in response.content if b.type == "text")

# ── 텍스트 → HTML 변환 ─────────────────────────────────────
def to_html(text):
    cards = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        lines = block.split("\n")
        title = lines[0].replace("**", "").strip().lstrip("0123456789. ")
        url, summary = "", ""
        for line in lines[1:]:
            line = line.strip().lstrip("- ")
            if line.lower().startswith("url:"):
                url = line[4:].strip()
            elif line.startswith("요약:"):
                summary = line[3:].strip()
        if not title or len(title) < 5:
            continue
        link_btn = f'<a href="{url}" style="font-size:12px;padding:4px 10px;background:#eff6ff;color:#2563eb;border-radius:5px;text-decoration:none;white-space:nowrap;">원문 →</a>' if url else ""
        cards.append(f"""
        <div style="background:#fff;border-left:4px solid #3b82f6;border-radius:10px;
                    padding:18px 20px;margin-bottom:14px;box-shadow:0 1px 6px rgba(0,0,0,.07);">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:8px;">
            <strong style="font-size:15px;color:#1e293b;line-height:1.5;">{title}</strong>
            {link_btn}
          </div>
          <p style="font-size:13.5px;color:#475569;line-height:1.75;margin:0;">{summary}</p>
          {f'<p style="font-size:11px;color:#94a3b8;margin-top:8px;word-break:break-all;">{url}</p>' if url else ""}
        </div>""")

    kw_tags = "".join(
        f'<span style="background:#eff6ff;color:#2563eb;border-radius:20px;padding:4px 12px;font-size:13px;font-weight:600;margin:3px;">{k}</span>'
        for k in KEYWORDS
    )
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
        <div style="margin-bottom:20px;">{kw_tags}</div>
        {''.join(cards) if cards else '<p style="color:#64748b;">검색 결과를 찾지 못했습니다.</p>'}
      </div>
      <div style="text-align:center;padding:16px;color:#94a3b8;font-size:12px;">
        자동 발송 · {today}
      </div>
    </div>
    </body></html>"""

# ── 메일 발송 ───────────────────────────────────────────────
def send_mail(html):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[뉴스 서치] {today} 주간 동향"
    msg["From"]    = f"{NAVER_ID}@naver.com"
    msg["To"]      = MAIL_TO
    msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.naver.com", 465) as s:
        s.login(NAVER_ID, NAVER_PW)
        s.send_message(msg)
    print(f"✅ 메일 발송 완료 → {MAIL_TO}")

# ── 실행 ────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🔍 뉴스 탐색 중...")
    raw = fetch_news()
    html = to_html(raw)
    send_mail(html)
