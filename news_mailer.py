import os
import json
import smtplib
import re
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parsedate_to_datetime
from urllib.request import Request, urlopen
from urllib.parse import quote, urlparse
from collections import defaultdict   # ← 중복 제거용 추가


# ── 설정 ────────────────────────────────────────────────────
GMAIL_ID = os.environ["GMAIL_ID"]
GMAIL_PW = os.environ["GMAIL_APP_PASSWORD"]
MAIL_TO = os.environ["MAIL_TO"]
KEYWORDS = json.loads(os.environ["KEYWORDS"])
NAVER_CLIENT_ID = os.environ["NAVER_CLIENT_ID"]
NAVER_CLIENT_SECRET = os.environ["NAVER_CLIENT_SECRET"]

# 기사 점수 하한선: 이 점수 미만은 제외
MIN_ARTICLE_SCORE = int(os.environ.get("MIN_ARTICLE_SCORE", "9"))

# 선택 시크릿: 없으면 기본값 사용
KEYWORDS_PLATFORM = json.loads(
    os.environ.get(
        "KEYWORDS_PLATFORM",
        json.dumps([
            "애플", "앱스토어", "구글", "플레이스토어", "앱마켓",
            "원스토어", "갤럭시스토어", "인앱결제", "외부결제",
            "제3자결제", "수수료", "정책", "규제", "심사", "결제"
        ], ensure_ascii=False)
    )
)

KEYWORDS_EXCLUDE = json.loads(
    os.environ.get(
        "KEYWORDS_EXCLUDE",
        json.dumps([
            "홈페이지", "웹사이트", "블로그", "SEO", "마케팅",
            "외부링크", "백링크", "트래픽", "도메인",
            "검색엔진최적화", "페이지뷰", "유입", "홍보"
        ], ensure_ascii=False)
    )
)

today_dt = datetime.now(timezone.utc)
today = today_dt.strftime("%Y년 %m월 %d일")
week_ago_dt = today_dt - timedelta(days=7)
week_ago = week_ago_dt.strftime("%Y년 %m월 %d일")


# ── 텍스트 유틸 ─────────────────────────────────────────────
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
    return m.group(1) if m else ""


# ── 기사 대표 이미지 자동 추출 (og:image / twitter:image) ─────
def make_absolute_url(base_url: str, img_url: str) -> str:
    """상대경로 이미지를 절대경로로 변환"""
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


def get_article_image(url: str) -> str | None:
    """기사 원문에서 대표 이미지(og:image) 자동 추출"""
    if not url or not url.startswith("http"):
        return None

    try:
        req = Request(url)
        req.add_header(
            "User-Agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
        )
        with urlopen(req, timeout=8) as resp:
            if resp.getcode() != 200:
                return None
            html = resp.read().decode("utf-8", errors="ignore")

        # 1. og:image
        m = re.search(
            r'<meta\s+(?:property|name)=["\']og:image["\']\s+content=["\']([^"\']+)["\']',
            html,
            re.IGNORECASE
        )
        if m:
            return make_absolute_url(url, m.group(1).strip())

        # 2. twitter:image
        m = re.search(
            r'<meta\s+(?:name|property)=["\']twitter:image["\']\s+content=["\']([^"\']+)["\']',
            html,
            re.IGNORECASE
        )
        if m:
            return make_absolute_url(url, m.group(1).strip())

        return None
    except Exception:
        return None


def dedupe_articles(articles):
    seen = set()
    result = []

    for article in articles:
        key = (
            normalize_text(article.get("title", "")),
            normalize_text(article.get("press", "")),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(article)

    return result


# ── 검색 쿼리 보정 ──────────────────────────────────────────
def build_search_query(keyword):
    query_map = {
        "아웃링크": "아웃링크 앱스토어 | 아웃링크 인앱결제 | 아웃링크 애플 | 아웃링크 구글",
        "웹결제": "웹결제 앱마켓 | 웹결제 인앱결제 | 웹결제 애플 | 웹결제 구글",
        "구독 경제": "구독 경제 앱스토어 | 구독 경제 앱마켓 | 구독 서비스 애플 | 구독 서비스 구글",
        "앱 생태계": "앱 생태계 애플 | 앱 생태계 구글 | 앱마켓 생태계",
        "앱 개발사": "앱 개발사 앱마켓 | 앱 개발사 인앱결제 | 앱 개발사 애플 | 앱 개발사 구글",
    }
    return query_map.get(keyword, keyword)


# ── 관련도 판별 ─────────────────────────────────────────────
STRICT_CONTEXT_KEYWORDS = [
    "아웃링크", "아웃링크결제", "웹결제", "구독경제", "앱생태계", "앱개발사"
]

POLICY_HINTS = [
    "인앱결제", "외부결제", "제3자결제", "안티스티어링", "사이드로딩",
    "수수료", "정책", "규제", "법안", "법", "판결", "소송", "심사",
    "허용", "금지", "강제", "방통위", "공정위", "디지털시장법",
    "dma", "앱스토어", "플레이스토어", "애플", "구글", "원스토어", "갤럭시스토어"
]


def is_relevant_article(keyword, title, desc):
    raw_text = f"{title} {desc}"
    text = normalize_text(raw_text)
    kw = normalize_text(keyword)

    if kw not in text:
        return False

    for bad in KEYWORDS_EXCLUDE:
        if normalize_text(bad) in text:
            return False

    if any(sk in kw for sk in STRICT_CONTEXT_KEYWORDS):
        has_platform_context = any(normalize_text(p) in text for p in KEYWORDS_PLATFORM)
        has_policy_context = any(normalize_text(p) in text for p in POLICY_HINTS)
        if not (has_platform_context or has_policy_context):
            return False

    return True


def score_article(keyword, title, desc):
    text = normalize_text(f"{title} {desc}")
    kw = normalize_text(keyword)
    score = 0

    if kw in normalize_text(title):
        score += 5
    if kw in normalize_text(desc):
        score += 3

    for p in KEYWORDS_PLATFORM:
        if normalize_text(p) in text:
            score += 2

    for p in POLICY_HINTS:
        if normalize_text(p) in text:
            score += 1

    strong_title_words = [
        "인앱결제", "외부결제", "앱스토어", "플레이스토어", "애플", "구글",
        "수수료", "정책", "규제", "소송", "방통위", "공정위", "안티스티어링",
        "사이드로딩", "디지털시장법"
    ]
    title_norm = normalize_text(title)
    for w in strong_title_words:
        if normalize_text(w) in title_norm:
            score += 2

    return score


# ── 네이버 뉴스 검색 ────────────────────────────────────────
def fetch_articles(keyword):
    query = build_search_query(keyword)
    url = f"https://openapi.naver.com/v1/search/news.json?query={quote(query)}&display=10&sort=date"

    req = Request(url)
    req.add_header("X-Naver-Client-Id", NAVER_CLIENT_ID)
    req.add_header("X-Naver-Client-Secret", NAVER_CLIENT_SECRET)

    try:
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"  [ERROR] {keyword} 검색 실패: {e}")
        return []

    articles = []

    for item in data.get("items", []):
        title = clean_spaces(strip_html(item.get("title", "")))
        desc = clean_spaces(strip_html(item.get("description", "")))
        link = item.get("originallink") or item.get("link", "")
        pub_str = item.get("pubDate", "")

        try:
            pub_dt = parsedate_to_datetime(pub_str).astimezone(timezone.utc)
            if pub_dt < week_ago_dt:
                continue
            pub_label = pub_dt.strftime("%Y.%m.%d")
        except Exception:
            continue

        if not title:
            continue

        if not is_relevant_article(keyword, title, desc):
            continue

        press = get_domain(link)
        score = score_article(keyword, title, desc)

        print(f"  [{keyword}] ({score}) {title[:50]} | {desc[:50]}")

        image = get_article_image(link)

        articles.append({
            "title": title,
            "press": press,
            "link": link,
            "summary": desc,
            "date": pub_label,
            "score": score,
            "keyword": keyword,
            "image": image,
        })

    articles.sort(key=lambda x: x["score"], reverse=True)
    articles = dedupe_articles(articles)
    articles = [a for a in articles if a["score"] >= MIN_ARTICLE_SCORE]

    return articles[:3]


# ── 요약 생성 ───────────────────────────────────────────────
POLICY_KEYWORDS = [
    "수수료", "정책", "규제", "법", "인하", "허용", "금지", "의무",
    "심사", "결제", "소송", "방통위", "공정위", "안티스티어링",
    "외부결제", "인앱결제", "제3자결제", "사이드로딩"
]

BAD_STARTS = [
    "이에 ", "이를 ", "이후 ", "이와 ", "한편 ", "또한 ",
    "그러나 ", "하지만 ", "따라서 ", "이같은 ", "이번 ",
    "이런 ", "이같이 "
]


def to_bullet_style(text):
    text = clean_spaces(text)

    for b in BAD_STARTS:
        if text.startswith(b):
            return None

    endings = [
        ("하고 있다", ""), ("되고 있다", ""), ("병행되고 있다", "병행"),
        ("시행 중이다", "시행"), ("논의 중이다", "논의"), ("검토 중이다", "검토"),
        ("인하됐으며", "인하"), ("인하됐다", "인하"), ("인하했다", "인하"),
        ("허용됐다", "허용"), ("허용했다", "허용"), ("도입됐다", "도입"),
        ("발표됐다", "발표"), ("시행됐다", "시행"), ("강화됐다", "강화"),
        ("부각됐다", "부각"), ("이어지고 있다", "이어짐"), ("높아지고 있다", "상승"),
        ("중이다", "중"), ("이다", ""), ("됐다", ""), ("했다", ""),
        ("한다", ""), ("된다", ""), ("있다", ""), ("없다", ""),
        ("밝혔다", ""), ("전했다", ""), ("나타났다", ""), ("보인다", ""),
        ("예정이다", "예정"), ("것이다", ""), ("했으며", ""), ("했고", ""),
    ]

    for old, new in endings:
        if text.endswith(old):
            text = text[:-len(old)] + new
            break

    text = re.sub(r"[.。?！!,]+$", "", text).strip()
    return text if len(text) > 10 else None


def build_summary_html(all_articles):
    all_items = []

    for kw, articles in all_articles.items():
        for a in articles:
            title = a.get("title", "")
            src = a.get("summary") or title
            sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", src) if len(s.strip()) > 15]

            picked = None
            for sent in sents:
                line = to_bullet_style(sent)
                if not line:
                    continue
                if len(line) > 58:
                    cut = line[:58].rfind(" ")
                    line = (line[:cut] + "…") if cut > 25 else (line[:58] + "…")
                picked = line
                break

            if picked:
                priority = sum(1 for pk in POLICY_KEYWORDS if pk in picked or pk in title)
                all_items.append((priority, picked))

    seen = set()
    parts = []
    for _, line in sorted(all_items, key=lambda x: -x[0]):
        if line in seen:
            continue
        seen.add(line)
        parts.append(
            f'<div style="font-size:15px;line-height:26px;color:#334155;margin-bottom:4px;">• {line}</div>'
        )
        if len(parts) >= 3:
            break

    if not parts:
        return '<div style="font-size:14px;color:#94a3b8;">이번 주 주요 내용을 찾지 못했습니다.</div>'

    return "".join(parts)


# ── HTML 변환 (기사 카드 좌측에 120×90 이미지 추가) ─────────────
def to_html(all_articles):
    summary_html = build_summary_html(all_articles)

    palette = ["#4f46e5", "#db2777", "#d97706", "#059669", "#2563eb", "#dc2626", "#7c3aed", "#0891b2"]
    kw_colors = {kw: palette[i % len(palette)] for i, kw in enumerate(all_articles.keys())}

    cards_html = ""
    total_count = 0

    for kw, articles in all_articles.items():
        color = kw_colors[kw]
        tag_bg = color + "18"

        for a in articles:
            total_count += 1

            img_url = a.get("image") or ""
            if img_url:
                image_html = f'''
                  <img src="{img_url}" 
                       width="120" 
                       height="90" 
                       style="width:120px;height:90px;object-fit:cover;border-radius:10px;display:block;" 
                       alt="기사 이미지">
                '''
            else:
                image_html = '''
                  <div style="width:120px;height:90px;background-color:#e2e8f0;border-radius:10px;
                              display:flex;align-items:center;justify-content:center;color:#94a3b8;font-size:28px;">
                    📰
                  </div>
                '''

            cards_html += f"""
            <tr>
              <td style="padding:0 36px 8px 36px;">
                <div style="border:1.5px solid #e5e7eb;border-radius:14px;overflow:hidden;">
                  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
                    <tr>
                      <td width="5" style="background-color:{color};">&nbsp;</td>
                      <td width="130" style="padding:11px 0 11px 12px;background-color:#ffffff;vertical-align:top;">
                        {image_html}
                      </td>
                      <td style="padding:11px 18px 11px 8px;background-color:#ffffff;vertical-align:top;">
                        <div style="margin-bottom:5px;">
                          <span style="display:inline-block;background-color:{tag_bg};color:{color};
                                       font-size:11px;line-height:17px;font-weight:700;
                                       padding:2px 9px;border-radius:999px;">{kw}</span>
                        </div>
                        <div style="font-size:17px;line-height:25px;color:#111827;font-weight:800;
                                    font-family:'Apple SD Gothic Neo','Malgun Gothic',Arial,sans-serif;">
                          {a['title']}
                        </div>
                        <div style="padding-top:5px;font-size:13.5px;line-height:21px;color:#4b5563;">
                          {a['summary'] or '원문 링크를 확인해주세요.'}
                        </div>
                        <div style="padding-top:6px;font-size:12px;line-height:18px;color:#94a3b8;">
                          {a['date']}{' · ' + a['press'] if a.get('press') else ''}
                        </div>
                        <div style="padding-top:8px;text-align:right;">
                          <a href="{a['link']}" style="display:inline-block;color:#ffffff;text-decoration:none;
                             font-size:12px;font-weight:700;padding:6px 13px;border-radius:8px;background-color:#374151;">
                             🔗 기사보기
                          </a>
                        </div>
                      </td>
                    </tr>
                  </table>
                </div>
              </td>
            </tr>"""

    empty_html = '<tr><td style="padding:0 36px 24px;color:#94a3b8;">이번 주 관련 기사를 찾지 못했습니다.</td></tr>'

    return f"""
    <html>
    <body style="margin:0;padding:0;background-color:#f3f6fb;
                 font-family:'Apple SD Gothic Neo','Malgun Gothic',Arial,sans-serif;color:#1f2937;">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
             style="background-color:#f3f6fb;">
        <tr>
          <td align="center" style="padding:32px 16px;">
            <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
                   style="max-width:1000px;background-color:#ffffff;border-radius:20px;overflow:hidden;">

              <tr>
                <td style="background:linear-gradient(to right,#0f1f3d 0%,#1a3a6b 50%,#1e4d9b 100%);
                           padding:28px 36px;">
                  <div style="font-size:14px;line-height:20px;color:#a9c3ff;font-weight:700;letter-spacing:0.4px;">
                    WEEKLY APP MARKET NEWS
                  </div>
                  <div style="padding-top:8px;font-size:30px;line-height:38px;color:#ffffff;font-weight:800;
                              font-family:'Apple SD Gothic Neo','Malgun Gothic',Arial,sans-serif;">
                    📊 이번주 앱마켓 동향 기사
                  </div>
                  <div style="padding-top:10px;font-size:15px;line-height:22px;color:#dbeafe;">
                    검색 범위 : {week_ago} ~ {today}
                  </div>
                </td>
              </tr>

              <tr>
                <td style="padding:24px 36px 8px 36px;font-size:15px;line-height:24px;color:#475569;">
                  안녕하세요.<br>
                  이번 주 앱마켓 관련 주요 기사와 핵심 이슈를 정리해 공유드립니다.
                </td>
              </tr>

              <tr>
                <td style="padding:16px 36px 8px 36px;">
                  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
                         style="background-color:#e8f4fd;border-radius:16px;">
                    <tr>
                      <td style="padding:20px 24px;">
                        <div style="font-size:17px;line-height:26px;font-weight:800;color:#0f172a;margin-bottom:12px;">
                          🔎 이번주 핵심 요약
                        </div>
                        {summary_html}
                      </td>
                    </tr>
                  </table>
                </td>
              </tr>

              <tr>
                <td style="padding:24px 36px 12px 36px;">
                  <div style="font-size:22px;line-height:30px;font-weight:800;color:#0f172a;">
                    📰 주요 기사
                  </div>
                </td>
              </tr>

              {cards_html if total_count > 0 else empty_html}

              <tr>
                <td style="border-top:1px solid #e5e7eb;padding:20px 36px 28px 36px;
                           font-size:13px;line-height:22px;color:#94a3b8;">
                  자동 발송 · {today}
                </td>
              </tr>

            </table>
          </td>
        </tr>
      </table>
    </body>
    </html>
    """


# ── 메일 발송 ───────────────────────────────────────────────
def send_mail(html):
    recipients = [x.strip() for x in MAIL_TO.split(",") if x.strip()]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[이번주 앱마켓 동향 기사] {today}"
    msg["From"] = f"{GMAIL_ID}@gmail.com"
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(GMAIL_ID, GMAIL_PW)
        smtp.sendmail(msg["From"], recipients, msg.as_string())

    print(f"✅ 메일 발송 완료 → {', '.join(recipients)}")


# ── 실행 ────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🔍 네이버 뉴스 탐색 중...")

    all_articles = {}
    total_found = 0

    for kw in KEYWORDS:
        print(f"  - {kw} 검색 중...")
        articles = fetch_articles(kw)

        if articles:
            all_articles[kw] = articles
            total_found += len(articles)
        else:
            print(f"    → 제외됨: {kw} (기사 없음 또는 정확도 부족)")

    # ==================== 전역 중복 제거 (링크 기준) ====================
    print("🔄 전역 중복 제거 중... (링크 동일한 기사는 기사가 적은 키워드에 우선 배정)")

    link_to_entries = defaultdict(list)
    for kw, arts in all_articles.items():
        for art in arts:
            link = art.get("link", "")
            if link:
                link_to_entries[link].append((kw, art))

    to_remove = defaultdict(list)   # kw → 제거할 link 리스트
    for link, entries in link_to_entries.items():
        if len(entries) <= 1:
            continue

        # 기사가 적은 키워드 우선 → 같은 개수면 점수 높은 쪽 우선
        entries.sort(key=lambda x: (len(all_articles[x[0]]), -x[1].get("score", 0)))

        # 첫 번째만 유지, 나머지는 제거 대상
        for kw, art in entries[1:]:
            to_remove[kw].append(art["link"])

    # 실제 제거 적용
    removed_count = 0
    for kw, remove_links in to_remove.items():
        if kw not in all_articles:
            continue
        original_len = len(all_articles[kw])
        remove_set = set(remove_links)
        all_articles[kw] = [a for a in all_articles[kw] if a.get("link") not in remove_set]
        removed = original_len - len(all_articles[kw])
        if removed > 0:
            print(f"  [{kw}] 중복 제거: {removed}건")
            removed_count += removed

    # 빈 키워드 정리
    all_articles = {k: v for k, v in all_articles.items() if v}

    total_found = sum(len(arts) for arts in all_articles.values())
    print(f"✅ 중복 제거 완료 (제거된 기사: {removed_count}건, 최종 기사 수: {total_found}건)")
    # =================================================================

    html = to_html(all_articles)
    send_mail(html)

    print(f"✅ 전체 완료 (최종 발송 기사 수: {total_found})")
