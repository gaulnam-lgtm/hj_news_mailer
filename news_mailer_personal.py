import os
import json
import smtplib
import re
import base64
import hashlib
import io
import mimetypes
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from email.utils import parsedate_to_datetime, formataddr
from email.header import Header
from urllib.request import Request, urlopen
from urllib.parse import quote, urlparse
from collections import defaultdict
from xml.etree import ElementTree as ET
from PIL import Image, ImageOps, ImageFile

# ── 설정 ────────────────────────────────────────────────────
GMAIL_ID       = os.environ["GMAIL_ID"]
GMAIL_PW       = os.environ["GMAIL_APP_PASSWORD"]
MAIL_TO        = os.environ["MAIL_TO_PERSONAL"]
NAVER_CLIENT_ID     = os.environ["NAVER_CLIENT_ID"]
NAVER_CLIENT_SECRET = os.environ["NAVER_CLIENT_SECRET"]
MIN_ARTICLE_SCORE   = int(os.environ.get("MIN_ARTICLE_SCORE_PERSONAL", "5"))

ImageFile.LOAD_TRUNCATED_IMAGES = True

# ── 썸네일 최적화 설정 ───────────────────────────────────────
THUMB_DISPLAY_W    = int(os.environ.get("THUMB_DISPLAY_W",    "120"))
THUMB_DISPLAY_H    = int(os.environ.get("THUMB_DISPLAY_H",    "90"))
THUMB_MAX_W        = int(os.environ.get("THUMB_MAX_W",        "160"))
THUMB_MAX_H        = int(os.environ.get("THUMB_MAX_H",        "120"))
THUMB_TARGET_BYTES = int(os.environ.get("THUMB_TARGET_BYTES", "35000"))
THUMB_MIN_QUALITY  = int(os.environ.get("THUMB_MIN_QUALITY",  "38"))
THUMB_MAX_QUALITY  = int(os.environ.get("THUMB_MAX_QUALITY",  "60"))

# ── keywords_personal.txt 로드 ───────────────────────────────
KEYWORDS_FILE = "keywords_personal.txt"
with open(KEYWORDS_FILE, encoding="utf-8") as f:
    KEYWORDS = [
        line.strip()
        for line in f
        if line.strip() and not line.strip().startswith("#")
    ]
print(f"📋 키워드 {len(KEYWORDS)}개 로드: {KEYWORDS}")

# ── 시간 설정 ────────────────────────────────────────────────
KST        = timezone(timedelta(hours=9))
today_dt   = datetime.now(KST)
today      = today_dt.strftime("%Y년 %m월 %d일")
since_dt   = today_dt - timedelta(days=1)
since_str  = since_dt.strftime("%Y년 %m월 %d일")

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
GOOGLEBOT_UA = (
    "Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36 "
    "(compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
)

BOT_BLOCKED_DOMAINS = {"v.daum.net", "daum.net", "news.nate.com", "nate.com", "naver.com"}

# ── 이미지 Base64 ─────────────────────────────────────────────
IMAGE1_PATH = "image1.png"
with open(IMAGE1_PATH, "rb") as f:
    _ext1 = os.path.splitext(IMAGE1_PATH)[-1].lstrip(".").replace("jpg", "jpeg")
    IMAGE1_BASE64 = f"data:image/{_ext1};base64," + base64.b64encode(f.read()).decode()

# ── 핵심: 공백 무관 부분 일치 ────────────────────────────────
def nospace(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()

def keyword_match(keyword: str, text: str) -> bool:
    return nospace(keyword) in nospace(text)

# ── 유틸 ──────────────────────────────────────────────────────
def strip_html(text):
    return re.sub(r"<[^>]+>", "", text or "").strip()

def clean_spaces(text):
    return re.sub(r"\s+", " ", (text or "")).strip()

def get_domain(url):
    m = re.search(r"https?://(?:www\.)?([^/]+)", url or "")
    return m.group(1).lower() if m else ""

def is_blocked_domain(url):
    d = get_domain(url)
    return any(d == b or d.endswith("." + b) for b in BOT_BLOCKED_DOMAINS)

def is_valid_snippet(text):
    if not text or len(text) < 20:
        return False
    if "google news" in text.lower():
        return False
    return True

PRESS_MAP = {
    "yna.co.kr":"연합뉴스","yonhapnews.co.kr":"연합뉴스","newsis.com":"뉴시스",
    "news1.kr":"뉴스1","chosun.com":"조선일보","biz.chosun.com":"조선비즈",
    "donga.com":"동아일보","joongang.co.kr":"중앙일보","hani.co.kr":"한겨레",
    "khan.co.kr":"경향신문","mk.co.kr":"매일경제","hankyung.com":"한국경제",
    "sedaily.com":"서울경제","mt.co.kr":"머니투데이","edaily.co.kr":"이데일리",
    "etnews.com":"전자신문","dt.co.kr":"디지털타임스","ddaily.co.kr":"디지털데일리",
    "zdnet.co.kr":"ZDNet Korea","bloter.net":"블로터","inews24.com":"아이뉴스24",
    "it.chosun.com":"IT조선","boannews.com":"보안뉴스","byline.network":"바이라인네트워크",
    "kbs.co.kr":"KBS","mbc.co.kr":"MBC","sbs.co.kr":"SBS","ytn.co.kr":"YTN",
    "jtbc.co.kr":"JTBC","heraldcorp.com":"헤럴드경제","asiae.co.kr":"아시아경제",
    "fnnews.com":"파이낸셜뉴스","nocutnews.co.kr":"노컷뉴스","newspim.com":"뉴스핌",
}

def get_press_name(url, title=""):
    domain = get_domain(url)
    title  = clean_spaces(strip_html(title or ""))
    for key, name in PRESS_MAP.items():
        if domain == key or domain.endswith("." + key) or key in domain:
            return name
    if " - " in title:
        maybe = title.rsplit(" - ", 1)[-1].strip()
        if 1 < len(maybe) <= 30:
            return maybe
    return domain

def make_absolute_url(base_url, img_url):
    if not img_url: return ""
    if img_url.startswith(("http://","https://")): return img_url
    if img_url.startswith("//"): return "https:" + img_url
    parsed = urlparse(base_url)
    if img_url.startswith("/"): return f"{parsed.scheme}://{parsed.netloc}{img_url}"
    base_path = parsed.path.rsplit("/",1)[0] + "/"
    return f"{parsed.scheme}://{parsed.netloc}{base_path}{img_url.lstrip('./')}"

# ── 검색 쿼리 빌드 ──────────────────────────────────────────
def build_search_query(keyword: str) -> str:
    kw_nospace = re.sub(r"\s+", "", keyword)
    if kw_nospace != keyword:
        return f"{keyword} | {kw_nospace}"
    return keyword

# ── 관련도 / 점수 ──────────────────────────────────────────────
def is_relevant_article(keyword, title, desc):
    combined = f"{title} {desc}"
    return keyword_match(keyword, combined)

def score_article(keyword, title, desc):
    score = 0
    if keyword_match(keyword, title): score += 6
    if keyword_match(keyword, desc):  score += 3
    return score

# ── 중복 제거 ──────────────────────────────────────────────────
def normalize_text(text):
    return re.sub(r"\s+", "", strip_html(text or "")).lower()

def dedupe_articles(articles):
    seen, result = set(), []
    for a in articles:
        key = (normalize_text(a.get("title","")), normalize_text(a.get("press","")))
        if key not in seen:
            seen.add(key)
            result.append(a)
    return result

# ── 이미지 URL 유효성 판단 ─────────────────────────────────────
def looks_like_image_url(candidate: str) -> bool:
    if not candidate:
        return False
    low = candidate.lower()
    if low.startswith("data:image/"):
        return True
    if any(tok in low for tok in ["sprite", "icon", "logo", "favicon", "blank.", "placeholder"]):
        return False
    return any(x in low for x in [
        ".jpg", ".jpeg", ".png", ".webp", ".gif",
        "image", "thumb", "photo", "upload", "cdn", "media"
    ])

# ── 기사 본문 정보 추출 (강화) ────────────────────────────────
def get_article_info(url, depth=0):
    if not url or not url.startswith("http") or depth > 3:
        return None, None
    try:
        req = Request(url)
        req.add_header("User-Agent", GOOGLEBOT_UA)
        req.add_header("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
        with urlopen(req, timeout=10) as resp:
            current_url = resp.url
            html = resp.read().decode("utf-8", errors="ignore")

        # 구글 뉴스 리다이렉트 처리
        if "news.google.com" in current_url or "news.url.google.com" in current_url:
            m = re.search(r'data-n-au=["\'](http[^"\']+)["\']', html, re.I)
            if not m:
                m = re.search(
                    r'<meta\s+http-equiv=["\']refresh["\']\s+content=["\'][^;]+;\s*url=([^"\']+)["\']',
                    html, re.I
                )
            if not m:
                m = re.search(r'<a\s+[^>]*href=["\'](http[^"\']+)["\']', html, re.I)
            if m:
                real = m.group(1).replace("&amp;", "&")
                if real != url:
                    return get_article_info(real, depth + 1)
            return None, None

        def extract_meta(html_text, meta_name):
            p1 = rf'<meta\s+[^>]*?(?:property|name)\s*=\s*["\']{meta_name}["\'][^>]*?content\s*=\s*["\']([^"\']+)["\']'
            m = re.search(p1, html_text, re.I)
            if m: return m.group(1).strip()
            p2 = rf'<meta\s+[^>]*?content\s*=\s*["\']([^"\']+)["\'][^>]*?(?:property|name)\s*=\s*["\']{meta_name}["\']'
            m = re.search(p2, html_text, re.I)
            return m.group(1).strip() if m else None

        # ── 이미지 후보 수집 (다단계) ──
        image_candidates = []

        # 1) 메타 태그 4종
        for tag in ["og:image", "og:image:url", "twitter:image", "twitter:image:src"]:
            raw = extract_meta(html, tag)
            if raw:
                image_candidates.append(raw)

        # 2) JSON-LD 구조 데이터
        for m in re.finditer(
            r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html, re.I | re.DOTALL
        ):
            block = m.group(1)
            for pat in [r'"image"\s*:\s*"([^"]+)"', r'"thumbnailUrl"\s*:\s*"([^"]+)"']:
                mm = re.search(pat, block, re.I | re.DOTALL)
                if mm:
                    image_candidates.append(mm.group(1))
            for arr in re.finditer(r'"image"\s*:\s*\[(.*?)\]', block, re.I | re.DOTALL):
                for u in re.findall(r'"(https?:\\/\\/[^"\\]+|\\/[^"\\]+)"', arr.group(1)):
                    image_candidates.append(u)

        # 3) img 태그 (data-src / srcset / src)
        img_patterns = [
            r'<img[^>]+(?:data-src|data-original|data-lazy-src|data-srcset|srcset|src)=["\']([^"\']+)["\'][^>]*(?:class|id|itemprop)=["\'][^"\']*(?:thumb|thumbnail|image|photo|figure|article|news|hero|lead|main)[^"\']*["\']',
            r'<img[^>]*(?:class|id|itemprop)=["\'][^"\']*(?:thumb|thumbnail|image|photo|figure|article|news|hero|lead|main)[^"\']*["\'][^>]+(?:data-src|data-original|data-lazy-src|data-srcset|srcset|src)=["\']([^"\']+)["\']',
            r'<img[^>]+(?:data-src|data-original|data-lazy-src|data-srcset|srcset|src)=["\']([^"\']+)["\']',
        ]
        for pat in img_patterns:
            for mm in re.finditer(pat, html, re.I | re.DOTALL):
                cand = mm.group(1)
                if looks_like_image_url(cand):
                    image_candidates.append(cand)

        # 후보 → 최종 이미지 선택
        image = None
        seen_imgs = set()
        for raw in image_candidates:
            raw = (raw or "").replace("\\/", "/").strip()
            if "," in raw and " " in raw:          # srcset → 첫 번째 URL만
                raw = raw.split(",")[0].strip().split()[0]
            final_img = make_absolute_url(current_url, raw)
            if not final_img or final_img in seen_imgs:
                continue
            seen_imgs.add(final_img)
            low = final_img.lower()
            if "lh3.googleusercontent.com" in low or "news.google.com" in low:
                continue
            if any(tok in low for tok in ["sprite", "icon", "logo", "favicon", "/ads/"]):
                continue
            image = final_img
            break

        # ── 스니펫 추출 ──
        snippet = None
        for tag in ["og:description", "twitter:description", "description"]:
            raw = extract_meta(html, tag)
            cand = clean_spaces(raw) if raw else None
            if cand and is_valid_snippet(cand):
                snippet = cand
                break

        return image, snippet
    except Exception:
        return None, None

# ── 이미지 다운로드 ───────────────────────────────────────────
def download_image_bytes(url: str, referer_url: str = "", timeout: int = 10):
    if not url or not url.startswith("http"):
        return None, None

    parsed = urlparse(url)
    referer_candidates = []
    if referer_url:
        referer_candidates.append(referer_url)
        try:
            rp = urlparse(referer_url)
            referer_candidates.append(f"{rp.scheme}://{rp.netloc}/")
        except Exception:
            pass
    referer_candidates.append(f"{parsed.scheme}://{parsed.netloc}/")

    for ua in [USER_AGENT, GOOGLEBOT_UA]:
        for referer in referer_candidates:
            for accept in [
                "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                "image/webp,image/apng,image/*,*/*;q=0.8",
                "*/*",
            ]:
                try:
                    req = Request(url)
                    req.add_header("User-Agent", ua)
                    req.add_header("Accept", accept)
                    req.add_header("Accept-Language", "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7")
                    req.add_header("Referer", referer)
                    with urlopen(req, timeout=timeout) as resp:
                        raw_type = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                        data = resp.read(2_000_000)
                    if not data:
                        continue
                    if not raw_type.startswith("image/"):
                        guessed, _ = mimetypes.guess_type(url)
                        raw_type = (guessed or "").lower()
                    if not raw_type.startswith("image/"):
                        continue
                    subtype = raw_type.split("/", 1)[1]
                    if subtype == "jpg":
                        subtype = "jpeg"
                    if subtype in {"svg+xml", "svg", "bmp", "tiff", "x-icon", "vnd.microsoft.icon"}:
                        continue
                    return data, subtype
                except Exception:
                    continue
    return None, None

# ── 썸네일 최적화 (JPEG 재압축) ───────────────────────────────
def optimize_thumbnail_bytes(
    data: bytes, subtype: str,
    display_w: int = THUMB_DISPLAY_W, display_h: int = THUMB_DISPLAY_H,
    max_w: int = THUMB_MAX_W, max_h: int = THUMB_MAX_H,
    target_bytes: int = THUMB_TARGET_BYTES
):
    if not data:
        return data, subtype
    try:
        with Image.open(io.BytesIO(data)) as im:
            im = ImageOps.exif_transpose(im)
            if getattr(im, "n_frames", 1) > 1:
                try: im.seek(0)
                except Exception: pass

            if im.mode not in ("RGB", "RGBA", "L", "LA", "P"):
                im = im.convert("RGB")

            thumb = im.copy()
            thumb.thumbnail((max_w, max_h), Image.Resampling.LANCZOS)

            has_alpha = thumb.mode in ("RGBA", "LA") or (
                thumb.mode == "P" and "transparency" in thumb.info
            )
            if has_alpha:
                bg = Image.new("RGB", thumb.size, (255, 255, 255))
                alpha = thumb.convert("RGBA")
                bg.paste(alpha, mask=alpha.getchannel("A"))
                thumb = bg
            elif thumb.mode != "RGB":
                thumb = thumb.convert("RGB")

            best = None
            current = thumb
            for size_step in range(4):
                for quality in (THUMB_MAX_QUALITY, 54, 48, 44, THUMB_MIN_QUALITY):
                    buf = io.BytesIO()
                    current.save(
                        buf, format="JPEG", quality=quality,
                        optimize=True, progressive=True, subsampling="4:2:0"
                    )
                    payload = buf.getvalue()
                    best = payload if best is None or len(payload) < len(best) else best
                    if len(payload) <= target_bytes:
                        return payload, "jpeg"
                if size_step < 3:
                    next_w = max(display_w, int(current.width * 0.88))
                    next_h = max(display_h, int(current.height * 0.88))
                    if (next_w, next_h) == current.size:
                        break
                    current = current.resize((next_w, next_h), Image.Resampling.LANCZOS)

            return best, "jpeg"
    except Exception:
        return data, subtype

# ── 인라인 이미지 준비 ────────────────────────────────────────
def prepare_inline_images(all_articles):
    inline_images = {}
    for kw, articles in all_articles.items():
        for idx, article in enumerate(articles, start=1):
            cid = f"thumb-{hashlib.md5((article.get('link','') + str(idx)).encode()).hexdigest()[:16]}"
            img_url = article.get("image") or ""
            data, subtype = (
                download_image_bytes(img_url, article.get("link", ""))
                if img_url else (None, None)
            )
            if not data or not subtype:
                article["inline_cid"] = ""
                continue
            data, subtype = optimize_thumbnail_bytes(data, subtype)
            if not data:
                article["inline_cid"] = ""
                continue
            inline_images[cid] = {"data": data, "subtype": subtype, "filename": f"{cid}.{subtype}"}
            article["inline_cid"] = cid
    return inline_images

# ── 네이버 뉴스 ────────────────────────────────────────────────
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
        print(f"  [ERROR] Naver {keyword}: {e}")
        return []

    articles = []
    for item in data.get("items", []):
        title   = clean_spaces(strip_html(item.get("title","")))
        desc    = clean_spaces(strip_html(item.get("description","")))
        link    = item.get("originallink") or item.get("link","")
        pub_str = item.get("pubDate","")
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
            "title":title,"press":press,"link":link,"summary":desc,
            "date":pub_label,"score":score,"keyword":keyword,"image":image
        })

    articles.sort(key=lambda x: x["score"], reverse=True)
    articles = dedupe_articles(articles)
    return [a for a in articles if a["score"] >= MIN_ARTICLE_SCORE][:3]

# ── 구글 뉴스 RSS ──────────────────────────────────────────────
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
            t_el  = item.find("title")
            l_el  = item.find("link")
            d_el  = item.find("description")
            p_el  = item.find("pubDate")
            s_el  = item.find("source")
            if not (t_el is not None and l_el is not None and t_el.text): continue

            title_raw = clean_spaces(strip_html(t_el.text))
            title = title_raw.rsplit(" - ",1)[0].strip() if " - " in title_raw else title_raw
            press = get_press_name(l_el.text.strip(), title_raw)
            link  = l_el.text.strip()
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
                "title":title,"press":press,"link":link,"summary":desc,
                "date":pub_label,"score":score,"keyword":keyword,"image":image
            })

        articles.sort(key=lambda x: x["score"], reverse=True)
        articles = dedupe_articles(articles)
        return [a for a in articles if a["score"] >= MIN_ARTICLE_SCORE][:3]
    except Exception as e:
        print(f"  [ERROR] Google {keyword}: {e}")
        return []

# ── HTML 생성 ──────────────────────────────────────────────────
def to_html(all_articles):
    palette = ["#4f46e5","#db2777","#d97706","#059669","#2563eb","#dc2626","#7c3aed","#0891b2"]
    kw_colors = {kw: palette[i % len(palette)] for i, kw in enumerate(all_articles.keys())}
    article_count = sum(len(v) for v in all_articles.values())

    cards_html = ""
    total_count = 0
    for kw, articles in all_articles.items():
        color  = kw_colors[kw]
        tag_bg = color + "18"
        for a in articles:
            total_count += 1
            cid = a.get("inline_cid", "")

            if cid:
                image_td = f'''<td width="130" style="padding:11px 0 11px 12px;vertical-align:top;">
                  <img src="cid:{cid}" width="{THUMB_DISPLAY_W}" height="{THUMB_DISPLAY_H}"
                       style="width:{THUMB_DISPLAY_W}px;height:{THUMB_DISPLAY_H}px;border-radius:10px;display:block;
                              background-color:#f8fafc;object-fit:cover;" alt="">
                </td>'''
                text_pl = "8px"
            else:
                image_td = ""
                text_pl  = "16px"

            summary = a.get("summary","") or "원문 링크를 확인해주세요."
            cards_html += f"""
            <tr><td style="padding:0 32px 10px 32px;">
              <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
                     style="background-color:#d1d5db;border-radius:14px;overflow:hidden;">
                <tr><td style="padding:1px;">
                  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
                         style="background-color:#ffffff;border-radius:13px;overflow:hidden;">
                    <tr>
                      <td width="5" style="background-color:{color};font-size:0;">&nbsp;</td>
                      {image_td}
                      <td style="padding:12px 18px 12px {text_pl};vertical-align:top;">
                        <div style="margin-bottom:6px;">
                          <span style="background-color:{tag_bg};color:{color};
                                       font-size:11px;font-weight:700;padding:2px 9px;
                                       border-radius:999px;display:inline-block;">{kw}</span>
                        </div>
                        <div style="font-size:15px;line-height:24px;color:#111827;font-weight:800;
                                    font-family:'Apple SD Gothic Neo','Malgun Gothic',Arial,sans-serif;">
                          {a['title']}
                        </div>
                        <div style="padding-top:5px;font-size:13px;line-height:21px;color:#4b5563;">
                          {summary}
                        </div>
                        <div style="padding-top:8px;">
                          <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
                            <tr>
                              <td style="font-size:11px;color:#9ca3af;">
                                {a['date']}{(' &middot; ' + a['press']) if a.get('press') else ''}
                              </td>
                              <td style="text-align:right;">
                                <a href="{a['link']}" style="color:#ffffff;text-decoration:none;
                                   font-size:12px;font-weight:700;padding:5px 12px;border-radius:7px;
                                   background-color:#374151;display:inline-block;">&#128279; 원문보기</a>
                              </td>
                            </tr>
                          </table>
                        </div>
                      </td>
                    </tr>
                  </table>
                </td></tr>
              </table>
            </td></tr>"""

    empty = '<tr><td style="padding:0 32px 24px;color:#94a3b8;">오늘 관련 기사를 찾지 못했습니다.</td></tr>'

    return f"""<!DOCTYPE html>
<html lang="ko">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="margin:0;padding:0;background-color:#eef0f7;
             font-family:'Apple SD Gothic Neo','Malgun Gothic',Arial,sans-serif;color:#1f2937;">
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
       style="background-color:#eef0f7;">
<tr><td align="center" style="padding:28px 12px;">
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="810"
       style="max-width:810px;background-color:#ffffff;border-radius:20px;overflow:hidden;
              box-shadow:0 4px 20px rgba(0,0,0,0.10);">

  <!-- 헤더 -->
  <tr>
    <td style="background:
        linear-gradient(135deg,rgb(26,50,99) 0%,rgb(84,119,146) 33%,rgb(239,210,176) 66%,rgb(255,197,112) 100%);
        padding:22px 32px 18px;">
      <div style="font-size:13px;font-weight:800;letter-spacing:3px;
                  color:rgba(147,197,253,0.85);margin-bottom:10px;font-family:Arial,sans-serif;">
        &#128203;&nbsp;&nbsp;DAILY PERSONAL NEWS
      </div>
      <div style="margin-bottom:10px;">
        <img src="{IMAGE1_BASE64}" height="56"
             style="height:56px;width:auto;display:block;" alt="개인 뉴스레터">
      </div>
      <div style="font-size:13px;color:rgba(180,215,255,0.75);font-family:Arial,sans-serif;">
        &#9679; {since_str} ~ {today} &nbsp;·&nbsp; 기사 {article_count}건 &nbsp;·&nbsp; 키워드 {len(all_articles)}개
      </div>
    </td>
  </tr>

  <!-- 인사말 -->
  <tr>
    <td style="padding:22px 32px 10px;font-size:14px;line-height:22px;color:#475569;">
      최근 3일간 키워드별 주요 기사를 정리했습니다.
    </td>
  </tr>

  <!-- 기사 카드 -->
  {cards_html if total_count > 0 else empty}

  <!-- 푸터 -->
  <tr>
    <td style="background:linear-gradient(135deg,rgb(26,50,99) 0%,rgb(84,119,146) 33%,rgb(239,210,176) 66%,rgb(255,197,112) 100%);
               padding:18px 32px;text-align:center;border-radius:0 0 20px 20px;">
      <div style="font-size:11px;color:rgba(186,230,253,0.75);font-family:Arial,sans-serif;">
        개인 뉴스레터 &middot; 자동 발송 &middot; {today}
      </div>
    </td>
  </tr>

</table>
</td></tr>
</table>
</body></html>"""

# ── 메일 발송 (인라인 이미지 첨부) ────────────────────────────
def send_mail(html, inline_images):
    recipients = [x.strip() for x in MAIL_TO.split(",") if x.strip()]
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(GMAIL_ID, GMAIL_PW)
        for r in recipients:
            msg = MIMEMultipart("related")
            msg["Subject"] = f"[개인 뉴스레터] {today}"
            msg["From"]    = formataddr((str(Header("개인 뉴스", "utf-8")), f"{GMAIL_ID}@gmail.com"))
            msg["To"]      = r

            alt = MIMEMultipart("alternative")
            alt.attach(MIMEText(html, "html", "utf-8"))
            msg.attach(alt)

            for cid, payload in inline_images.items():
                img_part = MIMEImage(payload["data"], _subtype=payload["subtype"])
                img_part.add_header("Content-ID", f"<{cid}>")
                img_part.add_header("Content-Disposition", "inline", filename=payload["filename"])
                msg.attach(img_part)

            smtp.sendmail(msg["From"], [r], msg.as_string())
            print(f"  → {r} 발송 완료")
    print(f"✅ 발송 완료 ({len(recipients)}명)")

# ── 실행 ───────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🔍 뉴스 탐색 중...")
    all_articles = {}
    for kw in KEYWORDS:
        print(f"  - [{kw}] 검색 중...")
        naver  = fetch_naver_articles(kw)
        google = fetch_google_articles(kw)
        combined = naver + google
        combined.sort(key=lambda x: x.get("score",0), reverse=True)
        combined = dedupe_articles(combined)
        combined = [a for a in combined if a.get("score",0) >= MIN_ARTICLE_SCORE]
        if combined:
            all_articles[kw] = combined[:3]
            print(f"    → {len(all_articles[kw])}건")

    # 전역 중복 제거
    link_seen = set()
    for kw in list(all_articles.keys()):
        deduped = []
        for a in all_articles[kw]:
            if a["link"] not in link_seen:
                link_seen.add(a["link"])
                deduped.append(a)
        if deduped:
            all_articles[kw] = deduped
        else:
            del all_articles[kw]

    total = sum(len(v) for v in all_articles.values())
    print(f"✅ 최종 {total}건 수집")

    print("🖼️ 이미지 다운로드 및 최적화 중...")
    inline_images = prepare_inline_images(all_articles)
    print(f"  → 이미지 {len(inline_images)}개 준비 완료")

    html = to_html(all_articles)
    send_mail(html, inline_images)
