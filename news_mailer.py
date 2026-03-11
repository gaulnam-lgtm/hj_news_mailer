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
from collections import defaultdict
from xml.etree import ElementTree as ET

# ── 설정 ────────────────────────────────────────────────────
GMAIL_ID = os.environ["GMAIL_ID"]
GMAIL_PW = os.environ["GMAIL_APP_PASSWORD"]
MAIL_TO = os.environ["MAIL_TO"]
KEYWORDS = json.loads(os.environ["KEYWORDS"])
NAVER_CLIENT_ID = os.environ["NAVER_CLIENT_ID"]
NAVER_CLIENT_SECRET = os.environ["NAVER_CLIENT_SECRET"]

MIN_ARTICLE_SCORE = int(os.environ.get("MIN_ARTICLE_SCORE", "9"))

KEYWORDS_PLATFORM = json.loads(
    os.environ.get("KEYWORDS_PLATFORM", json.dumps([
        "애플", "앱스토어", "구글", "플레이스토어", "앱마켓",
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

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"


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

PRESS_MAP = {
    # 통신 / 종합
    "yna.co.kr": "연합뉴스",
    "yonhapnews.co.kr": "연합뉴스",
    "newsis.com": "뉴시스",
    "news1.kr": "뉴스1",
    "nocutnews.co.kr": "노컷뉴스",
    "ohmynews.com": "오마이뉴스",
    "pressian.com": "프레시안",
    "newspim.com": "뉴스핌",
    "newdaily.co.kr": "뉴데일리",
    "viewsnnews.com": "뷰스앤뉴스",
    "upinews.kr": "UPI뉴스",
    "anewsa.com": "아시아뉴스통신",
    "sisajournal.com": "시사저널",
    "sisain.co.kr": "시사IN",
    "ilyo.co.kr": "일요신문",
    "kukinews.com": "쿠키뉴스",
    "mediatoday.co.kr": "미디어오늘",
    "journalist.or.kr": "기자협회보",
    "pdjournal.com": "PD저널",

    # 전국 일간지 / 경제지
    "chosun.com": "조선일보",
    "biz.chosun.com": "조선비즈",
    "donga.com": "동아일보",
    "joongang.co.kr": "중앙일보",
    "joins.com": "중앙일보",
    "hani.co.kr": "한겨레",
    "khan.co.kr": "경향신문",
    "munhwa.com": "문화일보",
    "segye.com": "세계일보",
    "hankookilbo.com": "한국일보",
    "mk.co.kr": "매일경제",
    "hankyung.com": "한국경제",
    "sedaily.com": "서울경제",
    "fnnews.com": "파이낸셜뉴스",
    "mt.co.kr": "머니투데이",
    "moneytoday.co.kr": "머니투데이",
    "edaily.co.kr": "이데일리",
    "asiae.co.kr": "아시아경제",
    "ajunews.com": "아주경제",
    "asiatoday.co.kr": "아시아투데이",
    "heraldcorp.com": "헤럴드경제",
    "seoulfn.com": "서울파이낸스",
    "dealsite.co.kr": "딜사이트",
    "thebell.co.kr": "더벨",
    "newsway.co.kr": "뉴스웨이",
    "cstimes.com": "컨슈머타임스",
    "ccdailynews.com": "소비자가 만드는 신문",
    "consumernews.co.kr": "컨슈머뉴스",

    # IT / 산업 / 스타트업 / 게임
    "etnews.com": "전자신문",
    "dt.co.kr": "디지털타임스",
    "ddaily.co.kr": "디지털데일리",
    "digitaltoday.co.kr": "디지털투데이",
    "zdnet.co.kr": "ZDNet Korea",
    "zdnet.com": "ZDNet",
    "bloter.net": "블로터",
    "itworld.co.kr": "ITWorld",
    "inews24.com": "아이뉴스24",
    "thelec.kr": "디일렉",
    "it.chosun.com": "IT조선",
    "boannews.com": "보안뉴스",
    "byline.network": "바이라인네트워크",
    "hellot.net": "헬로티",
    "platum.kr": "플래텀",
    "venturesquare.net": "벤처스퀘어",
    "beinews.net": "비아이뉴스",
    "gamevu.co.kr": "게임뷰",
    "inven.co.kr": "인벤",
    "thisisgame.com": "디스이즈게임",
    "gamefocus.co.kr": "게임포커스",
    "gameple.co.kr": "게임플",
    "gametoc.hankyung.com": "게임톡",

    # 방송사
    "kbs.co.kr": "KBS",
    "news.kbs.co.kr": "KBS",
    "mbc.co.kr": "MBC",
    "imbc.com": "MBC",
    "sbs.co.kr": "SBS",
    "news.sbs.co.kr": "SBS",
    "ytn.co.kr": "YTN",
    "jtbc.co.kr": "JTBC",
    "tvchosun.com": "TV조선",
    "ichannela.com": "채널A",
    "mbn.co.kr": "MBN",
    "obs.co.kr": "OBS",
    "ebs.co.kr": "EBS",
    "yonhapnewstv.co.kr": "연합뉴스TV",

    # 스포츠 / 연예
    "sportsseoul.com": "스포츠서울",
    "sports.khan.co.kr": "스포츠경향",
    "osen.co.kr": "OSEN",
    "xportsnews.com": "엑스포츠뉴스",
    "starnews.com": "스타뉴스",
    "starnewskorea.com": "스타뉴스",
    "tenasia.co.kr": "텐아시아",
    "sportalkorea.com": "스포탈코리아",

    # 법률 / 공공 / 노동 / 여성
    "lawtimes.co.kr": "법률신문",
    "lec.co.kr": "법률저널",
    "scourt.go.kr": "대한민국 법원",
    "labortoday.co.kr": "매일노동뉴스",
    "womennews.co.kr": "여성신문",

    # 수도권 / 강원
    "incheonilbo.com": "인천일보",
    "kihoilbo.co.kr": "기호일보",
    "kgnews.co.kr": "경기신문",
    "kyeongin.com": "경인일보",
    "kyeonggi.com": "경기일보",
    "jeonmae.co.kr": "전국매일신문",
    "kwnews.co.kr": "강원일보",
    "kado.net": "강원도민일보",

    # 충청
    "cctoday.co.kr": "충청투데이",
    "ccdn.co.kr": "충청일보",
    "ccdailynews.com": "충청데일리",
    "daejonilbo.com": "대전일보",
    "djtimes.co.kr": "대전일보",
    "ggilbo.com": "금강일보",
    "jbnews.com": "중부매일",
    "cjb.co.kr": "CJB청주방송",

    # 영남
    "yeongnam.com": "영남일보",
    "imaeil.com": "매일신문",
    "idaegu.co.kr": "대구신문",
    "kyongbuk.co.kr": "경북일보",
    "hidomin.com": "경북도민일보",
    "ksmnews.co.kr": "경상매일신문",
    "knnews.co.kr": "경남신문",
    "gnnews.co.kr": "경남일보",
    "idomin.com": "경남도민일보",
    "gndomin.com": "경남도민신문",
    "busan.com": "부산일보",
    "busanilbo.com": "부산일보",
    "kookje.co.kr": "국제신문",
    "ulsanpress.net": "울산신문",
    "usm.co.kr": "울산매일",
    "tbc.co.kr": "TBC",
    "knn.co.kr": "KNN",

    # 호남 / 제주
    "jnilbo.com": "전남일보",
    "namdonews.com": "남도일보",
    "kjdaily.com": "광주매일신문",
    "mdilbo.com": "무등일보",
    "jjan.kr": "전북일보",
    "domin.co.kr": "전북도민일보",
    "sjbnews.com": "새전북신문",
    "ihalla.com": "한라일보",
    "jejunews.com": "제주일보",
    "headlinejeju.co.kr": "헤드라인제주",
    "kbc.co.kr": "KBC광주방송",
    "jtv.co.kr": "JTV전주방송",
    "jibs.co.kr": "JIBS",

    # 기타
    "andongilbo.co.kr": "안동일보",
    "mirae-biz.com": "미래경제",
    "pinetree.news": "파인트리뉴스",
}

def get_press_name(url: str, title: str = "") -> str:
    domain = get_domain(url)
    title = clean_spaces(strip_html(title or ""))

    for key, name in PRESS_MAP.items():
        if domain == key or domain.endswith("." + key) or key in domain:
            return name

    # 구글 뉴스 RSS 제목: "기사 제목 - 언론사명"
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

def get_article_image(url: str) -> str | None:
    if not url or not url.startswith("http"):
        return None
    try:
        req = Request(url)
        req.add_header("User-Agent", USER_AGENT)
        with urlopen(req, timeout=8) as resp:
            if resp.getcode() != 200:
                return None
            html = resp.read().decode("utf-8", errors="ignore")

        m = re.search(r'<meta\s+(?:property|name)=["\']og:image["\']\s+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
        if m:
            return make_absolute_url(url, m.group(1).strip())

        m = re.search(r'<meta\s+(?:name|property)=["\']twitter:image["\']\s+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
        if m:
            return make_absolute_url(url, m.group(1).strip())

        return None
    except:
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


# ── 검색 쿼리 / 관련도 / 점수 ─────────────────────────────────
def build_search_query(keyword):
    query_map = {
        "아웃링크": "아웃링크 앱스토어 | 아웃링크 인앱결제 | 아웃링크 애플 | 아웃링크 구글",
        "웹결제": "웹결제 앱마켓 | 웹결제 인앱결제 | 웹결제 애플 | 웹결제 구글",
        "구독 경제": "구독 경제 앱스토어 | 구독 경제 앱마켓 | 구독 서비스 애플 | 구독 서비스 구글",
        "앱 생태계": "앱 생태계 애플 | 앱 생태계 구글 | 앱마켓 생태계",
        "앱 개발사": "앱 개발사 앱마켓 | 앱 개발사 인앱결제 | 앱 개발사 애플 | 앱 개발사 구글",
    }
    return query_map.get(keyword, keyword)

STRICT_CONTEXT_KEYWORDS = ["아웃링크", "아웃링크결제", "웹결제", "구독경제", "앱생태계", "앱개발사"]
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
        has_platform = any(normalize_text(p) in text for p in KEYWORDS_PLATFORM)
        has_policy = any(normalize_text(p) in text for p in POLICY_HINTS)
        if not (has_platform or has_policy):
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

    strong = [
        "인앱결제", "외부결제", "앱스토어", "플레이스토어", "애플", "구글",
        "수수료", "정책", "규제", "소송", "방통위", "공정위",
        "안티스티어링", "사이드로딩", "디지털시장법"
    ]
    title_norm = normalize_text(title)
    for w in strong:
        if normalize_text(w) in title_norm:
            score += 2

    return score


# ── 네이버 뉴스 검색 ────────────────────────────────────────
def fetch_naver_articles(keyword):
    query = build_search_query(keyword)
    url = f"https://openapi.naver.com/v1/search/news.json?query={quote(query)}&display=10&sort=date"
    req = Request(url)
    req.add_header("X-Naver-Client-Id", NAVER_CLIENT_ID)
    req.add_header("X-Naver-Client-Secret", NAVER_CLIENT_SECRET)

    try:
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"  [ERROR] Naver {keyword} 실패: {e}")
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
        except:
            continue

        if not title or not is_relevant_article(keyword, title, desc):
            continue

        press = get_press_name(link, title)
        score = score_article(keyword, title, desc)
        print(f"  [NAVER/{keyword}] ({score}) {title[:50]}...")

        image = get_article_image(link)

        articles.append({
            "title": title,
            "press": press,
            "link": link,
            "summary": desc,
            "date": pub_label,
            "score": score,
            "keyword": keyword,
            "image": image
        })

    articles.sort(key=lambda x: x["score"], reverse=True)
    articles = dedupe_articles(articles)
    articles = [a for a in articles if a["score"] >= MIN_ARTICLE_SCORE]
    return articles[:3]


# ── 구글 뉴스 RSS 검색 ───────────────────────────────────────
def fetch_google_articles(keyword):
    query = quote(build_search_query(keyword))
    rss_url = f"https://news.google.com/rss/search?q={query}&hl=ko&gl=KR&ceid=KR:ko"

    req = Request(rss_url)
    req.add_header("User-Agent", USER_AGENT)

    try:
        with urlopen(req, timeout=10) as resp:
            xml_data = resp.read().decode("utf-8", errors="ignore")
        root = ET.fromstring(xml_data)
        articles = []

        for item in root.findall(".//item")[:10]:
            title_el = item.find("title")
            link_el = item.find("link")
            desc_el = item.find("description")
            pub_el = item.find("pubDate")

            if not (title_el is not None and link_el is not None and title_el.text):
                continue

            title = clean_spaces(strip_html(title_el.text))
            link = link_el.text.strip()
            desc = clean_spaces(strip_html(desc_el.text if desc_el is not None else ""))
            pub_str = pub_el.text if pub_el is not None else ""

            try:
                pub_dt = parsedate_to_datetime(pub_str).astimezone(timezone.utc)
                if pub_dt < week_ago_dt:
                    continue
                pub_label = pub_dt.strftime("%Y.%m.%d")
            except:
                continue

            if not title or not is_relevant_article(keyword, title, desc):
                continue

            press = get_press_name(link, title)
            score = score_article(keyword, title, desc)
            print(f"  [GOOGLE/{keyword}] ({score}) {title[:50]}...")

            image = get_article_image(link)

            articles.append({
                "title": title,
                "press": press,
                "link": link,
                "summary": desc,
                "date": pub_label,
                "score": score,
                "keyword": keyword,
                "image": image
            })

        articles.sort(key=lambda x: x["score"], reverse=True)
        articles = dedupe_articles(articles)
        articles = [a for a in articles if a["score"] >= MIN_ARTICLE_SCORE]
        return articles[:3]

    except Exception as e:
        print(f"  [ERROR] Google {keyword} 실패: {e}")
        return []


# ── 요약 생성 ───────────────────────────────────────────────
POLICY_KEYWORDS = [
    "수수료", "정책", "규제", "법", "인하", "허용", "금지", "의무",
    "심사", "결제", "소송", "방통위", "공정위", "안티스티어링",
    "외부결제", "인앱결제", "제3자결제", "사이드로딩"
]
BAD_STARTS = ["이에 ", "이를 ", "이후 ", "이와 ", "한편 ", "또한 ", "그러나 ", "하지만 ", "따라서 ", "이같은 ", "이번 ", "이런 ", "이같이 "]

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
        ("예정이다", "예정"), ("것이다", ""), ("했으며", ""), ("했고", "")
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
            src = a.get("summary") or a.get("title", "")
            sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", src) if len(s.strip()) > 15]
            for sent in sents:
                line = to_bullet_style(sent)
                if not line:
                    continue
                if len(line) > 58:
                    cut = line[:58].rfind(" ")
                    line = (line[:cut] + "…") if cut > 25 else (line[:58] + "…")
                priority = sum(1 for pk in POLICY_KEYWORDS if pk in line or pk in a.get("title", ""))
                all_items.append((priority, line))
                break

    seen = set()
    parts = []
    for _, line in sorted(all_items, key=lambda x: -x[0]):
        if line in seen:
            continue
        seen.add(line)
        parts.append(f'<div style="font-size:15px;line-height:26px;color:#334155;margin-bottom:4px;">• {line}</div>')
        if len(parts) >= 3:
            break

    return "".join(parts) or '<div style="font-size:14px;color:#94a3b8;">이번 주 주요 내용을 찾지 못했습니다.</div>'


# ── HTML 변환 (이미지 포함) ───────────────────────────────────
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
            image_html = f'''
              <img src="{img_url}" width="120" height="90"
                   style="width:120px;height:90px;object-fit:cover;border-radius:10px;display:block;" alt="기사 이미지">
            ''' if img_url else '''
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
                                       font-size:11px;line-height:17px;font-weight:700;padding:2px 9px;border-radius:999px;">{kw}</span>
                        </div>
                        <div style="font-size:17px;line-height:25px;color:#111827;font-weight:800;font-family:'Apple SD Gothic Neo','Malgun Gothic',Arial,sans-serif;">
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
                             원문보기
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
    <html><body style="margin:0;padding:0;background-color:#f3f6fb;font-family:'Apple SD Gothic Neo','Malgun Gothic',Arial,sans-serif;color:#1f2937;">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background-color:#f3f6fb;">
        <tr><td align="center" style="padding:32px 16px;">
          <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="max-width:1000px;background-color:#ffffff;border-radius:20px;overflow:hidden;">
            <tr><td style="background:linear-gradient(to right,#0f1f3d 0%,#1a3a6b 50%,#1e4d9b 100%);padding:28px 36px;">
              <div style="font-size:14px;line-height:20px;color:#a9c3ff;font-weight:700;letter-spacing:0.4px;">WEEKLY APP MARKET NEWS</div>
              <div style="padding-top:8px;font-size:30px;line-height:38px;color:#ffffff;font-weight:800;font-family:'Apple SD Gothic Neo','Malgun Gothic',Arial,sans-serif;">📊 이번주 앱마켓 동향 기사</div>
              <div style="padding-top:10px;font-size:15px;line-height:22px;color:#dbeafe;">검색 범위 : {week_ago} ~ {today}</div>
            </td></tr>

            <tr><td style="padding:24px 36px 8px 36px;font-size:15px;line-height:24px;color:#475569;">안녕하세요.<br>이번 주 앱마켓 관련 주요 기사와 핵심 이슈를 정리해 공유드립니다.</td></tr>

            <tr><td style="padding:16px 36px 8px 36px;">
              <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background-color:#e8f4fd;border-radius:16px;">
                <tr><td style="padding:20px 24px;">
                  <div style="font-size:17px;line-height:26px;font-weight:800;color:#0f172a;margin-bottom:12px;">🔎 이번주 핵심 요약</div>
                  {summary_html}
                </td></tr>
              </table>
            </td></tr>

            <tr><td style="padding:24px 36px 12px 36px;">
              <div style="font-size:22px;line-height:30px;font-weight:800;color:#0f172a;">📰 주요 기사</div>
            </td></tr>

            {cards_html if total_count > 0 else empty_html}

            <tr><td style="border-top:1px solid #e5e7eb;padding:20px 36px 28px 36px;font-size:13px;line-height:22px;color:#94a3b8;">자동 발송 · {today}</td></tr>
          </table>
        </td></tr>
      </table>
    </body></html>
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
