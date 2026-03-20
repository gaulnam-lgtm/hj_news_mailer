import os
import json
import smtplib
import re
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parsedate_to_datetime, formataddr
from email.header import Header
from urllib.request import Request, urlopen
from urllib.parse import quote, urlparse
from collections import defaultdict
from xml.etree import ElementTree as ET

# ── 설정 ────────────────────────────────────────────────────
GMAIL_ID            = os.environ["GMAIL_ID"]
GMAIL_PW            = os.environ["GMAIL_APP_PASSWORD"]
MAIL_TO             = os.environ["MAIL_TO_PERSONAL"]
NAVER_CLIENT_ID     = os.environ["NAVER_CLIENT_ID"]
NAVER_CLIENT_SECRET = os.environ["NAVER_CLIENT_SECRET"]
MIN_ARTICLE_SCORE   = int(os.environ.get("MIN_ARTICLE_SCORE_PERSONAL", "5"))

# ── keywords_personal.txt 로드 ───────────────────────────────
KEYWORDS_FILE = "keywords_personal.txt"
with open(KEYWORDS_FILE, encoding="utf-8") as f:
    KEYWORDS = [
        line.strip()
        for line in f
        if line.strip() and not line.strip().startswith("#")
    ]
print(f"📋 키워드 {len(KEYWORDS)}개 로드: {KEYWORDS}")

# ── 시간 설정 (발송일 기준 3일 전 ~ 오늘) ───────────────────
KST       = timezone(timedelta(hours=9))
today_dt  = datetime.now(KST)
today     = today_dt.strftime("%Y년 %m월 %d일")
since_dt  = today_dt - timedelta(days=3)
since_str = since_dt.strftime("%Y년 %m월 %d일")

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
GOOGLEBOT_UA = (
    "Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36 "
    "(compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
)
BOT_BLOCKED_DOMAINS = {"v.daum.net", "daum.net", "news.nate.com", "nate.com", "naver.com"}

# ── 헤더/푸터 그라디언트 ─────────────────────────────────────
WARM_GRADIENT = (
    "radial-gradient(ellipse at 10% 55%, rgba(84,119,146,0.60) 0%, transparent 48%),"
    "radial-gradient(ellipse at 85% 15%, rgba(255,197,112,0.50) 0%, transparent 45%),"
    "radial-gradient(ellipse at 55% 90%, rgba(239,210,176,0.45) 0%, transparent 42%),"
    "linear-gradient(135deg, rgb(26,50,99) 0%, rgb(84,119,146) 35%, rgb(239,210,176) 70%, rgb(255,197,112) 100%)"
)

# ── 핵심: 공백 무관 부분 일치 ────────────────────────────────
def nospace(text):
    return re.sub(r"\s+", "", text or "").lower()

def keyword_match(keyword, text):
    return nospace(keyword) in nospace(text)

# ── 유틸 ─────────────────────────────────────────────────────
def strip_html(text):
    return re.sub(r"<[^>]+>", "", text or "").strip()

def clean_spaces(text):
    return re.sub(r"\s+", " ", (text or "")).strip()

def normalize_text(text):
    return re.sub(r"\s+", "", strip_html(text or "")).lower()

def get_domain(url):
    m = re.search(r"https?://(?:www\.)?([^/]+)", url or "")
    return m.group(1).lower() if m else ""

def is_blocked_domain(url):
    d = get_domain(url)
    return any(d == b or d.endswith("." + b) for b in BOT_BLOCKED_DOMAINS)

def is_valid_snippet(text):
    if not text or len(text) < 20: return False
    if "google news" in text.lower(): return False
    return True

PRESS_MAP = {
    "yna.co.kr": "연합뉴스", "yonhapnews.co.kr": "연합뉴스", "newsis.com": "뉴시스",
    "news1.kr": "뉴스1", "chosun.com": "조선일보", "biz.chosun.com": "조선비즈",
    "donga.com": "동아일보", "joongang.co.kr": "중앙일보", "hani.co.kr": "한겨레",
    "khan.co.kr": "경향신문", "mk.co.kr": "매일경제", "hankyung.com": "한국경제",
    "sedaily.com": "서울경제", "mt.co.kr": "머니투데이", "edaily.co.kr": "이데일리",
    "etnews.com": "전자신문", "dt.co.kr": "디지털타임스", "ddaily.co.kr": "디지털데일리",
    "zdnet.co.kr": "ZDNet Korea", "bloter.net": "블로터", "inews24.com": "아이뉴스24",
    "it.chosun.com": "IT조선", "boannews.com": "보안뉴스", "byline.network": "바이라인네트워크",
    "kbs.co.kr": "KBS", "mbc.co.kr": "MBC", "sbs.co.kr": "SBS", "ytn.co.kr": "YTN",
    "jtbc.co.kr": "JTBC", "heraldcorp.com": "헤럴드경제", "asiae.co.kr": "아시아경제",
    "fnnews.com": "파이낸셜뉴스", "nocutnews.co.kr": "노컷뉴스", "newspim.com": "뉴스핌",
}

def get_press_name(url, title=""):
    domain = get_domain(url)
    title  = clean_spaces(strip_html(title or ""))
    for key, name in PRESS_MAP.items():
        if domain == key or domain.endswith("." + key) or key in domain:
            return name
    if " - " in title:
        maybe = title.rsplit(" - ", 1)[-1].strip()
        if 1 < len(maybe) <= 30: return maybe
    return domain

def make_absolute_url(base_url, img_url):
    if not img_url: return ""
    if img_url.startswith(("http://", "https://")): return img_url
    if img_url.startswith("//"): return "https:" + img_url
    parsed = urlparse(base_url)
    if img_url.startswith("/"): return f"{parsed.scheme}://{parsed.netloc}{img_url}"
    base_path = parsed.path.rsplit("/", 1)[0] + "/"
    return f"{parsed.scheme}://{parsed.netloc}{base_path}{img_url.lstrip('./')}"

# ── 검색 쿼리 빌드 ───────────────────────────────────────────
def build_search_query(keyword):
    kw_nospace = re.sub(r"\s+", "", keyword)
    if kw_nospace != keyword:
        return f"{keyword} | {kw_nospace}"
    return keyword

# ── 관련도 / 점수 ────────────────────────────────────────────
def is_relevant_article(keyword, title, desc):
    return keyword_match(keyword, f"{title} {desc}")

def score_article(keyword, title, desc):
    score = 0
    if keyword_match(keyword, title): score += 6
    if keyword_match(keyword, desc):  score += 5
    return score

# ── 중복 제거 ─────────────────────────────────────────────────
def dedupe_articles(articles):
    seen, result = set(), []
    for a in articles:
        key = (normalize_text(a.get("title", "")), normalize_text(a.get("press", "")))
        if key not in seen:
            seen.add(key)
            result.append(a)
    return result

def select_diverse_articles(articles, max_count=3):
    selected = []
    for a in articles:
        t_a = nospace(a.get("title", ""))
        is_dup = False
        for s in selected:
            t_s = nospace(s.get("title", ""))
            shorter = min(len(t_a), len(t_s))
            if shorter == 0: continue
            if t_a in t_s or t_s in t_a:
                is_dup = True
                break
            common = sum(1 for c in set(t_a) if c in t_s)
            if common / shorter > 0.70:
                is_dup = True
                break
        if not is_dup:
            selected.append(a)
        if len(selected) >= max_count:
            break
    return selected

# ── 기사 본문 정보 추출 ──────────────────────────────────────
def get_article_info(url, depth=0):
    if not url or not url.startswith("http") or depth > 3:
        return None, None
    try:
        req = Request(url)
        req.add_header("User-Agent", GOOGLEBOT_UA)
        req.add_header("Accept", "text/html,application/xhtml+xml,*/*;q=0.8")
        with urlopen(req, timeout=10) as resp:
            current_url = resp.url
            html = resp.read().decode("utf-8", errors="ignore")

        if "news.google.com" in current_url:
            m = re.search(r'data-n-au=["\'](http[^"\']+)["\']', html, re.I)
            if not m:
                m = re.search(r'<a\s+[^>]*href=["\'](http[^"\']+)["\']', html, re.I)
            if m:
                real = m.group(1).replace("&amp;", "&")
                if real != url: return get_article_info(real, depth+1)
            return None, None

        def meta(name):
            p1 = rf'<meta\s+[^>]*?(?:property|name)\s*=\s*["\']{name}["\'][^>]*?content\s*=\s*["\']([^"\']+)["\']'
            m = re.search(p1, html, re.I)
            if m: return m.group(1).strip()
            p2 = rf'<meta\s+[^>]*?content\s*=\s*["\']([^"\']+)["\'][^>]*?(?:property|name)\s*=\s*["\']{name}["\']'
            m = re.search(p2, html, re.I)
            return m.group(1).strip() if m else None

        img_raw = meta("og:image") or meta("twitter:image")
        image = None
        if img_raw:
            final_img = make_absolute_url(current_url, img_raw)
            if "lh3.googleusercontent.com" not in final_img:
                try:
                    ir = Request(final_img, method="HEAD")
                    ir.add_header("User-Agent", USER_AGENT)
                    with urlopen(ir, timeout=5) as ir_resp:
                        if "image" in ir_resp.headers.get("Content-Type", ""):
                            image = final_img
                except: pass

        snippet_raw = meta("og:description") or meta("twitter:description") or meta("description")
        snippet = clean_spaces(snippet_raw) if snippet_raw else None
        if snippet and not is_valid_snippet(snippet): snippet = None
        return image, snippet
    except:
        return None, None

# ── 네이버 뉴스 ──────────────────────────────────────────────
def fetch_naver_articles(keyword):
    query = build_search_query(keyword)
    url = f"https://openapi.naver.com/v1/search/news.json?query={quote(query)}&display=20&sort=date"
    req = Request(url)
    req.add_header("X-Naver-Client-Id", NAVER_CLIENT_ID)
    req.add_header("X-Naver-Client-Secret", NAVER_CLIENT_SECRET)
    try:
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"  [ERROR] Naver {keyword}: {e}")
        return []

    articles = []
    for item in data.get("items", []):
        title   = clean_spaces(strip_html(item.get("title", "")))
        desc    = clean_spaces(strip_html(item.get("description", "")))
        link    = item.get("originallink") or item.get("link", "")
        pub_str = item.get("pubDate", "")
        try:
            pub_dt = parsedate_to_datetime(pub_str).astimezone(timezone.utc)
            if pub_dt < since_dt: continue
            pub_label = pub_dt.strftime("%Y.%m.%d")
        except: continue
        if not title or is_blocked_domain(link): continue
        if not is_relevant_article(keyword, title, desc): continue

        score = score_article(keyword, title, desc)
        press = get_press_name(link, title)
        print(f"  [NAVER/{keyword}] ({score}) {title[:50]}")
        image, snippet = get_article_info(link)
        if not desc or normalize_text(desc) == normalize_text(title):
            desc = snippet or ""
        articles.append({
            "title": title, "press": press, "link": link, "summary": desc,
            "date": pub_label, "score": score, "keyword": keyword, "image": image
        })

    articles.sort(key=lambda x: x["score"], reverse=True)
    articles = dedupe_articles(articles)
    articles = [a for a in articles if a["score"] >= MIN_ARTICLE_SCORE]
    return select_diverse_articles(articles, max_count=5)

# ── 구글 뉴스 RSS ─────────────────────────────────────────────
def fetch_google_articles(keyword):
    query   = quote(build_search_query(keyword))
    rss_url = f"https://news.google.com/rss/search?q={query}&hl=ko&gl=KR&ceid=KR:ko"
    req = Request(rss_url)
    req.add_header("User-Agent", USER_AGENT)
    try:
        with urlopen(req, timeout=10) as resp:
            xml_data = resp.read().decode("utf-8", errors="ignore")
        root = ET.fromstring(xml_data)
        articles = []
        for item in root.findall(".//item")[:20]:
            t_el = item.find("title")
            l_el = item.find("link")
            d_el = item.find("description")
            p_el = item.find("pubDate")
            s_el = item.find("source")
            if not (t_el is not None and l_el is not None and t_el.text): continue

            title_raw = clean_spaces(strip_html(t_el.text))
            title     = title_raw.rsplit(" - ", 1)[0].strip() if " - " in title_raw else title_raw
            press     = get_press_name(l_el.text.strip(), title_raw)
            link      = l_el.text.strip()
            real_link = s_el.get("url") if s_el is not None else None
            desc_raw  = clean_spaces(strip_html(d_el.text if d_el is not None else ""))
            pub_str   = p_el.text if p_el is not None else ""
            try:
                pub_dt = parsedate_to_datetime(pub_str).astimezone(timezone.utc)
                if pub_dt < since_dt: continue
                pub_label = pub_dt.strftime("%Y.%m.%d")
            except: continue
            if not title or is_blocked_domain(real_link or link): continue
            if not is_relevant_article(keyword, title, desc_raw): continue

            score = score_article(keyword, title, desc_raw)
            print(f"  [GOOGLE/{keyword}] ({score}) {title[:50]}")
            image, snippet = get_article_info(real_link or link)
            desc = desc_raw
            if not desc or normalize_text(desc).startswith(normalize_text(title)):
                desc = snippet or ""
            articles.append({
                "title": title, "press": press, "link": link, "summary": desc,
                "date": pub_label, "score": score, "keyword": keyword, "image": image
            })

        articles.sort(key=lambda x: x["score"], reverse=True)
        articles = dedupe_articles(articles)
        articles = [a for a in articles if a["score"] >= MIN_ARTICLE_SCORE]
        return select_diverse_articles(articles, max_count=5)
    except Exception as e:
        print(f"  [ERROR] Google {keyword}: {e}")
        return []

# ── HTML 생성 ─────────────────────────────────────────────────
def to_html(all_articles):
    palette = ["#4f46e5", "#db2777", "#d97706", "#059669", "#2563eb", "#dc2626", "#7c3aed", "#0891b2"]
    kw_colors     = {kw: palette[i % len(palette)] for i, kw in enumerate(all_articles.keys())}
    article_count = sum(len(v) for v in all_articles.values())

    cards_html  = ""
    total_count = 0
    for kw, articles in all_articles.items():
        color  = kw_colors[kw]
        tag_bg = color + "18"
        for a in articles:
            total_count += 1
            img_url = a.get("image") or ""
            if img_url:
                safe_img   = quote(img_url, safe=":/")
                parsed_img = urlparse(img_url)
                referer    = quote(f"{parsed_img.scheme}://{parsed_img.netloc}/", safe="")
                proxy      = f"https://wsrv.nl/?url={safe_img}&w=240&h=180&fit=cover&referer={referer}&default=1"
                image_td   = (
                    '<td width="130" style="padding:11px 0 11px 12px;vertical-align:top;">'
                    f'<img src="{proxy}" width="120" height="90"'
                    ' style="width:120px;height:90px;border-radius:10px;display:block;" alt="">'
                    '</td>'
                )
                text_pl = "8px"
            else:
                image_td = ""
                text_pl  = "16px"

            summary = a.get("summary", "") or "원문 링크를 확인해주세요."
            press_str = (" &middot; " + a["press"]) if a.get("press") else ""
            cards_html += (
                '<tr><td style="padding:0 32px 10px 32px;">'
                '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"'
                ' style="background-color:#d1d5db;border-radius:14px;overflow:hidden;">'
                '<tr><td style="padding:1px;">'
                '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"'
                ' style="background-color:#ffffff;border-radius:13px;overflow:hidden;">'
                '<tr>'
                f'<td width="5" style="background-color:{color};font-size:0;">&nbsp;</td>'
                f'{image_td}'
                f'<td style="padding:12px 18px 12px {text_pl};vertical-align:top;">'
                '<div style="margin-bottom:6px;">'
                f'<span style="background-color:{tag_bg};color:{color};font-size:11px;font-weight:700;'
                f'padding:2px 9px;border-radius:999px;display:inline-block;">{kw}</span>'
                '</div>'
                '<div style="font-size:15px;line-height:24px;color:#111827;font-weight:800;'
                "font-family:'Apple SD Gothic Neo','Malgun Gothic',Arial,sans-serif;\">"
                f'{a["title"]}'
                '</div>'
                '<div style="padding-top:5px;font-size:13px;line-height:21px;color:#4b5563;">'
                f'{summary}'
                '</div>'
                '<div style="padding-top:8px;">'
                '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"><tr>'
                f'<td style="font-size:11px;color:#9ca3af;">{a["date"]}{press_str}</td>'
                '<td style="text-align:right;">'
                f'<a href="{a["link"]}" style="color:#ffffff;text-decoration:none;font-size:12px;'
                'font-weight:700;padding:5px 12px;border-radius:7px;background-color:#374151;'
                'display:inline-block;">&#128279; 원문보기</a>'
                '</td>'
                '</tr></table>'
                '</div></td></tr></table></td></tr></table></td></tr>'
            )

    empty = '<tr><td style="padding:0 32px 24px;color:#94a3b8;">오늘 관련 기사를 찾지 못했습니다.</td></tr>'

    return (
        '<!DOCTYPE html>'
        '<html lang="ko">'
        '<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>'
        '<body style="margin:0;padding:0;background-color:#eef0f7;'
        "font-family:'Apple SD Gothic Neo','Malgun Gothic',Arial,sans-serif;color:#1f2937;\">"
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"'
        ' style="background-color:#eef0f7;">'
        '<tr><td align="center" style="padding:28px 12px;">'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="810"'
        ' style="max-width:810px;background-color:#ffffff;border-radius:20px;overflow:hidden;'
        'box-shadow:0 4px 20px rgba(0,0,0,0.10);">'

        # 헤더
        '<tr><td style="background:' + WARM_GRADIENT + ';padding:22px 32px 18px;">'
        '<div style="font-size:13px;font-weight:800;letter-spacing:3px;'
        'color:rgba(255,255,255,0.90);margin-bottom:10px;font-family:Arial,sans-serif;">'
        '&#128203;&nbsp;&nbsp;DAILY PERSONAL NEWS'
        '</div>'
        '<div style="margin-bottom:10px;">'
        '<span style="font-size:28px;font-weight:900;color:#ffffff;'
        "font-family:'Apple SD Gothic Neo','Malgun Gothic',Arial,sans-serif;letter-spacing:-0.5px;\">"
        '일간 개인 뉴스레터'
        '</span></div>'
        '<div style="font-size:13px;color:rgba(255,255,255,0.85);font-family:Arial,sans-serif;">'
        f'&#9679; {since_str} ~ {today} &nbsp;·&nbsp; 기사 {article_count}건 &nbsp;·&nbsp; 키워드 {len(all_articles)}개'
        '</div></td></tr>'

        # 인사말
        '<tr><td style="padding:22px 32px 10px;font-size:14px;line-height:22px;color:#475569;">'
        '최근 3일간 키워드별 주요 기사를 정리했습니다.'
        '</td></tr>'

        # 기사 카드
        + (cards_html if total_count > 0 else empty) +

        # 푸터
        '<tr><td style="background:' + WARM_GRADIENT + ';'
        'padding:18px 32px;text-align:center;border-radius:0 0 20px 20px;">'
        '<div style="font-size:11px;color:rgba(255,255,255,0.85);font-family:Arial,sans-serif;">'
        f'개인 뉴스레터 &middot; 자동 발송 &middot; {today}'
        '</div></td></tr>'

        '</table></td></tr></table></body></html>'
    )

# ── 메일 발송 ─────────────────────────────────────────────────
def send_mail(html):
    recipients = [x.strip() for x in MAIL_TO.split(",") if x.strip()]
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(GMAIL_ID, GMAIL_PW)
        for r in recipients:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"[개인 뉴스레터] {today}"
            msg["From"]    = formataddr((str(Header("개인 뉴스", "utf-8")), f"{GMAIL_ID}@gmail.com"))
            msg["To"]      = r
            msg.attach(MIMEText(html, "html", "utf-8"))
            smtp.sendmail(msg["From"], [r], msg.as_string())
            print(f"  → {r} 발송 완료")
    print(f"✅ 발송 완료 ({len(recipients)}명)")

# ── 실행 ─────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🔍 뉴스 탐색 중 (최근 3일)...")
    all_articles = {}
    for kw in KEYWORDS:
        print(f"  - [{kw}] 검색 중...")
        naver  = fetch_naver_articles(kw)
        google = fetch_google_articles(kw)
        combined = naver + google
        combined.sort(key=lambda x: x.get("score", 0), reverse=True)
        combined = dedupe_articles(combined)
        combined = [a for a in combined if a.get("score", 0) >= MIN_ARTICLE_SCORE]
        combined = select_diverse_articles(combined, max_count=3)
        if combined:
            all_articles[kw] = combined
            print(f"    → {len(combined)}건")

    # 전역 링크 중복 제거
    link_seen = set()
    for kw in list(all_articles.keys()):
        deduped = [a for a in all_articles[kw] if a["link"] not in link_seen]
        for a in deduped: link_seen.add(a["link"])
        if deduped: all_articles[kw] = deduped
        else: del all_articles[kw]

    total = sum(len(v) for v in all_articles.values())
    print(f"✅ 최종 {total}건 수집")
    html = to_html(all_articles)
    send_mail(html)
