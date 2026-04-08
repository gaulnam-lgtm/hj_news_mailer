import os
import sys
import json
import smtplib
import re
import base64
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from email.utils import parsedate_to_datetime, formataddr
from email.header import Header
from urllib.request import Request, urlopen
from urllib.parse import quote, urlparse
import mimetypes
import hashlib
import io
from collections import defaultdict
from xml.etree import ElementTree as ET
from PIL import Image, ImageOps, ImageFile

# ── 설정 ────────────────────────────────────────────────────
GMAIL_ID = os.environ["GMAIL_ID"]
GMAIL_PW = os.environ["GMAIL_APP_PASSWORD"]
mode = "auto"
if "--mode" in sys.argv:
    mode = sys.argv[sys.argv.index("--mode") + 1]

KST = timezone(timedelta(hours=9))
today_dt = datetime.now(KST)

if mode == "auto":
    mode = "weekly" if today_dt.weekday() == 0 else "daily"

if mode == "daily":
    MAIL_TO = os.environ["MAIL_TO_DAILY"]
else:
    MAIL_TO = os.environ["MAIL_TO"]

NAVER_CLIENT_ID = os.environ["NAVER_CLIENT_ID"]
NAVER_CLIENT_SECRET = os.environ["NAVER_CLIENT_SECRET"]
MIN_ARTICLE_SCORE = int(os.environ.get("MIN_ARTICLE_SCORE", "7"))

# ── 키워드 파일 로드 ─────────────────────────────────────────
def _load_keywords(filepath: str) -> list:
    with open(filepath, encoding="utf-8") as f:
        return [
            line.strip()
            for line in f
            if line.strip() and not line.strip().startswith("#")
        ]

KEYWORDS          = _load_keywords("keywords.txt")
KEYWORDS_PLATFORM = _load_keywords("keywords_platform.txt")
KEYWORDS_EXCLUDE  = _load_keywords("keywords_exclude.txt")
print(f"📋 메인 키워드 {len(KEYWORDS)}개: {KEYWORDS}")
print(f"📋 플랫폼 키워드 {len(KEYWORDS_PLATFORM)}개 / 제외 키워드 {len(KEYWORDS_EXCLUDE)}개 로드")

today = today_dt.strftime("%Y년 %m월 %d일")
week_ago_dt = today_dt - timedelta(days=7)
week_ago = week_ago_dt.strftime("%Y년 %m월 %d일")

# ── 주차 레이블 ──────────────────────────────────────────────
def get_week_label(dt):
    week_num = (dt.day - 1) // 7 + 1
    korean_nums = ["첫", "둘", "셋", "넷", "다섯"]
    return f"{dt.month}월 {korean_nums[min(week_num-1, 4)]}째주"

week_label = get_week_label(today_dt)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
GOOGLEBOT_UA = ("Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36 "
                "(compatible; Googlebot/2.1; +http://www.google.com/bot.html)")


ImageFile.LOAD_TRUNCATED_IMAGES = True

# ── 썸네일 최적화 설정 ───────────────────────────────────────
THUMB_DISPLAY_W = int(os.environ.get("THUMB_DISPLAY_W", "120"))
THUMB_DISPLAY_H = int(os.environ.get("THUMB_DISPLAY_H", "90"))
THUMB_MAX_W = int(os.environ.get("THUMB_MAX_W", "160"))
THUMB_MAX_H = int(os.environ.get("THUMB_MAX_H", "120"))
THUMB_TARGET_BYTES = int(os.environ.get("THUMB_TARGET_BYTES", "35000"))
THUMB_MIN_QUALITY = int(os.environ.get("THUMB_MIN_QUALITY", "38"))
THUMB_MAX_QUALITY = int(os.environ.get("THUMB_MAX_QUALITY", "60"))


# ── 이미지 Base64 (GitHub 업로드용) ─────────────────────────
IMAGE1_PATH = "image1.png"
with open(IMAGE1_PATH, "rb") as f:
    _ext1 = os.path.splitext(IMAGE1_PATH)[-1].lstrip(".").replace("jpg", "jpeg")
    IMAGE1_BASE64 = f"data:image/{_ext1};base64," + base64.b64encode(f.read()).decode()

IMAGE2_PATH = "image2.png"
with open(IMAGE2_PATH, "rb") as f:
    _ext2 = os.path.splitext(IMAGE2_PATH)[-1].lstrip(".").replace("jpg", "jpeg")
    IMAGE2_BASE64 = f"data:image/{_ext2};base64," + base64.b64encode(f.read()).decode()

# ── 봇 차단 도메인 ────────────────────────────────────────────
BOT_BLOCKED_DOMAINS = {
    "v.daum.net", "daum.net",
    "news.nate.com", "nate.com",
    "naver.com",
}

def is_blocked_domain(url: str) -> bool:
    domain = get_domain(url)
    return any(domain == d or domain.endswith("." + d) for d in BOT_BLOCKED_DOMAINS)

# ── 유틸 함수 ───────────────────────────────────────────────
def strip_html(text):
    return re.sub(r"<[^>]+>", "", text or "").strip()

def normalize_text(text):
    text = strip_html(text)
    text = re.sub(r"\s+", "", text)
    return text.lower()

def clean_spaces(text):
    return re.sub(r"\s+", " ", (text or "")).strip()


def sanitize_summary_line(text: str) -> str:
    """핵심 요약 문장 앞뒤의 가비지 문자/공백을 정리."""
    text = clean_spaces(text or "")
    if not text:
        return ""

    # BOM/zero-width/제어문자 제거
    text = re.sub(r"[﻿​‌‍⁠ ]", "", text)
    text = re.sub(r"^[\s\-–—•·ㆍ※▶▷◆◇□■]+", "", text)

    # 앞에 잘못 붙은 한 글자 가비지(예: '히 미국은') 제거
    text = re.sub(r'^[가-힣]\s+(?=[가-힣A-Za-z0-9(])', '', text)
    return text.strip()

def get_domain(url):
    m = re.search(r"https?://(?:www\.)?([^/]+)", url or "")
    return m.group(1).lower() if m else ""

PRESS_MAP = {
    "yna.co.kr": "연합뉴스", "yonhapnews.co.kr": "연합뉴스",
    "newsis.com": "뉴시스", "news1.kr": "뉴스1",
    "nocutnews.co.kr": "노컷뉴스", "ohmynews.com": "오마이뉴스",
    "pressian.com": "프레시안", "newspim.com": "뉴스핌",
    "newdaily.co.kr": "뉴데일리", "viewsnnews.com": "뷰스앤뉴스",
    "upinews.kr": "UPI뉴스", "anewsa.com": "아시아뉴스통신",
    "sisajournal.com": "시사저널", "sisain.co.kr": "시사IN",
    "ilyo.co.kr": "일요신문", "kukinews.com": "쿠키뉴스",
    "mediatoday.co.kr": "미디어오늘", "journalist.or.kr": "기자협회보",
    "pdjournal.com": "PD저널",
    "chosun.com": "조선일보", "biz.chosun.com": "조선비즈",
    "donga.com": "동아일보", "joongang.co.kr": "중앙일보",
    "joins.com": "중앙일보", "hani.co.kr": "한겨레",
    "khan.co.kr": "경향신문", "munhwa.com": "문화일보",
    "segye.com": "세계일보", "hankookilbo.com": "한국일보",
    "mk.co.kr": "매일경제", "hankyung.com": "한국경제",
    "sedaily.com": "서울경제", "fnnews.com": "파이낸셜뉴스",
    "mt.co.kr": "머니투데이", "moneytoday.co.kr": "머니투데이",
    "edaily.co.kr": "이데일리", "asiae.co.kr": "아시아경제",
    "ajunews.com": "아주경제", "asiatoday.co.kr": "아시아투데이",
    "heraldcorp.com": "헤럴드경제", "seoulfn.com": "서울파이낸스",
    "dealsite.co.kr": "딜사이트", "thebell.co.kr": "더벨",
    "newsway.co.kr": "뉴스웨이", "cstimes.com": "컨슈머타임스",
    "ccdailynews.com": "소비자가 만드는 신문", "consumernews.co.kr": "컨슈머뉴스",
    "etnews.com": "전자신문", "dt.co.kr": "디지털타임스",
    "ddaily.co.kr": "디지털데일리", "digitaltoday.co.kr": "디지털투데이",
    "zdnet.co.kr": "ZDNet Korea", "zdnet.com": "ZDNet",
    "bloter.net": "블로터", "itworld.co.kr": "ITWorld",
    "inews24.com": "아이뉴스24", "thelec.kr": "디일렉",
    "it.chosun.com": "IT조선", "boannews.com": "보안뉴스",
    "byline.network": "바이라인네트워크", "hellot.net": "헬로티",
    "platum.kr": "플래텀", "venturesquare.net": "벤처스퀘어",
    "beinews.net": "비아이뉴스", "gamevu.co.kr": "게임뷰",
    "inven.co.kr": "인벤", "thisisgame.com": "디스이즈게임",
    "gamefocus.co.kr": "게임포커스", "gameple.co.kr": "게임플",
    "gametoc.hankyung.com": "게임톡", "etoday.co.kr": "이투데이",
    "news.mtn.co.kr": "MTN뉴스", "sentv.co.kr": "서울경제TV",
    "kbs.co.kr": "KBS", "news.kbs.co.kr": "KBS",
    "mbc.co.kr": "MBC", "imbc.com": "MBC",
    "sbs.co.kr": "SBS", "news.sbs.co.kr": "SBS",
    "ytn.co.kr": "YTN", "jtbc.co.kr": "JTBC",
    "tvchosun.com": "TV조선", "ichannela.com": "채널A",
    "mbn.co.kr": "MBN", "obs.co.kr": "OBS",
    "ebs.co.kr": "EBS", "yonhapnewstv.co.kr": "연합뉴스TV",
    "sportsseoul.com": "스포츠서울", "sports.khan.co.kr": "스포츠경향",
    "osen.co.kr": "OSEN", "xportsnews.com": "엑스포츠뉴스",
    "starnews.com": "스타뉴스", "starnewskorea.com": "스타뉴스",
    "tenasia.co.kr": "텐아시아", "sportalkorea.com": "스포탈코리아",
    "lawtimes.co.kr": "법률신문", "lec.co.kr": "법률저널",
    "scourt.go.kr": "대한민국 법원", "labortoday.co.kr": "매일노동뉴스",
    "womennews.co.kr": "여성신문",
    "incheonilbo.com": "인천일보", "kihoilbo.co.kr": "기호일보",
    "kgnews.co.kr": "경기신문", "kyeongin.com": "경인일보",
    "kyeonggi.com": "경기일보", "jeonmae.co.kr": "전국매일신문",
    "kwnews.co.kr": "강원일보", "kado.net": "강원도민일보",
    "cctoday.co.kr": "충청투데이", "ccdn.co.kr": "충청일보",
    "daejonilbo.com": "대전일보", "djtimes.co.kr": "대전일보",
    "ggilbo.com": "금강일보", "jbnews.com": "중부매일",
    "cjb.co.kr": "CJB청주방송",
    "yeongnam.com": "영남일보", "imaeil.com": "매일신문",
    "idaegu.co.kr": "대구신문", "kyongbuk.co.kr": "경북일보",
    "hidomin.com": "경북도민일보", "ksmnews.co.kr": "경상매일신문",
    "knnews.co.kr": "경남신문", "gnnews.co.kr": "경남일보",
    "idomin.com": "경남도민일보", "gndomin.com": "경남도민신문",
    "busan.com": "부산일보", "busanilbo.com": "부산일보",
    "kookje.co.kr": "국제신문", "ulsanpress.net": "울산신문",
    "usm.co.kr": "울산매일", "tbc.co.kr": "TBC", "knn.co.kr": "KNN",
    "jnilbo.com": "전남일보", "namdonews.com": "남도일보",
    "kjdaily.com": "광주매일신문", "mdilbo.com": "무등일보",
    "jjan.kr": "전북일보", "domin.co.kr": "전북도민일보",
    "sjbnews.com": "새전북신문", "ihalla.com": "한라일보",
    "jejunews.com": "제주일보", "headlinejeju.co.kr": "헤드라인제주",
    "kbc.co.kr": "KBC광주방송", "jtv.co.kr": "JTV전주방송",
    "jibs.co.kr": "JIBS",
    "andongilbo.co.kr": "안동일보", "mirae-biz.com": "미래경제",
    "pinetree.news": "파인트리뉴스",
    "srtimes.kr": "SR타임스",
}

def get_press_name(url: str, title: str = "") -> str:
    domain = get_domain(url)
    title = clean_spaces(strip_html(title or ""))
    for key, name in PRESS_MAP.items():
        if domain == key or domain.endswith("." + key) or key in domain:
            return name
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

def is_valid_snippet(text: str) -> bool:
    text = clean_spaces(strip_html(text or ""))
    if not text or len(text) < 20:
        return False

    low = text.lower()
    bad_phrases = [
        "google news",
        "원문 링크를 확인해주세요",
        "원문링크를 확인해주세요",
        "기사 원문",
        "자세한 내용은",
        "자세한 내용은 원문",
        "원문에서 확인",
        "기사 전문",
        "본문 내용은",
    ]
    if any(bp in low for bp in bad_phrases):
        return False
    if text.count(",") + text.count("，") >= 8:
        return False
    if len(re.findall(r"[가-힣A-Za-z0-9]", text)) < 20:
        return False
    return True


def extract_candidate_snippets_from_html(html: str) -> list:
    candidates = []

    for cls_pat in [
        r"<p[^>]*class=[\"']?[^\"']*(?:article|news|content|story|article_txt|articleBody|detail|view|editor)[^\"']*[\"']?[^>]*>(.*?)</p>",
        r"<div[^>]*class=[\"']?[^\"']*(?:article|news|content|story|article_txt|articleBody|detail|view|editor)[^\"']*[\"']?[^>]*>(.*?)</div>",
    ]:
        for m in re.finditer(cls_pat, html, re.IGNORECASE | re.DOTALL):
            txt = clean_spaces(strip_html(m.group(1)))
            if is_valid_snippet(txt):
                candidates.append(txt)

    for m in re.finditer(r'<p[^>]*>(.*?)</p>', html, re.IGNORECASE | re.DOTALL):
        txt = clean_spaces(strip_html(m.group(1)))
        if is_valid_snippet(txt):
            candidates.append(txt)

    cleaned = []
    seen = set()
    for txt in candidates:
        norm = normalize_text(txt)
        if norm in seen:
            continue
        seen.add(norm)
        cleaned.append(txt)
    return cleaned

# ── 기사 정보 추출 (기존 그대로) ─────────────────────────────
def get_article_info(url: str, depth=0) -> tuple:
    if not url or not url.startswith("http") or depth > 3:
        return None, None
    try:
        req = Request(url)
        req.add_header("User-Agent", GOOGLEBOT_UA)
        req.add_header("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")

        with urlopen(req, timeout=10) as resp:
            current_url = resp.url
            html = resp.read().decode("utf-8", errors="ignore")

        if "news.google.com" in current_url or "news.url.google.com" in current_url:
            m = re.search(r"data-n-au=[\"'](http[^\"']+)[\"']", html, re.IGNORECASE)
            if not m:
                m = re.search(r"<meta\s+http-equiv=[\"']refresh[\"']\s+content=[\"'][^;]+;\s*url=([^\"']+)[\"']", html, re.IGNORECASE)
            if not m:
                m = re.search(r"<a\s+[^>]*href=[\"'](http[^\"']+)[\"'][^>]*>", html, re.IGNORECASE)
            if m:
                real_url = m.group(1).replace("&amp;", "&")
                if real_url and real_url != url:
                    return get_article_info(real_url, depth=depth + 1)
            return None, None

        def extract_meta(html_text, meta_name):
            pat1 = rf"<meta\s+[^>]*?(?:property|name)\s*=\s*[\"']{meta_name}[\"'][^>]*?content\s*=\s*[\"']([^\"']+)[\"']"
            m = re.search(pat1, html_text, re.IGNORECASE)
            if m:
                return m.group(1).strip()
            pat2 = rf"<meta\s+[^>]*?content\s*=\s*[\"']([^\"']+)[\"'][^>]*?(?:property|name)\s*=\s*[\"']{meta_name}[\"']"
            m = re.search(pat2, html_text, re.IGNORECASE)
            if m:
                return m.group(1).strip()
            return None

        def looks_like_image_url(candidate: str) -> bool:
            if not candidate:
                return False
            low = candidate.lower()
            if low.startswith("data:image/"):
                return True
            if any(tok in low for tok in ["sprite", "icon", "logo", "favicon", "blank.", "placeholder"]):
                return False
            return any(x in low for x in [".jpg", ".jpeg", ".png", ".webp", ".gif", "image", "thumb", "photo", "upload", "cdn", "media"])

        image = None
        image_candidates = []
        for raw in [
            extract_meta(html, "og:image"),
            extract_meta(html, "og:image:url"),
            extract_meta(html, "twitter:image"),
            extract_meta(html, "twitter:image:src"),
        ]:
            if raw:
                image_candidates.append(raw)

        for m in re.finditer(r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", html, re.IGNORECASE | re.DOTALL):
            block = m.group(1)
            for pat in [r'"image"\s*:\s*"([^"]+)"', r'"thumbnailUrl"\s*:\s*"([^"]+)"']:
                mm = re.search(pat, block, re.IGNORECASE | re.DOTALL)
                if mm:
                    image_candidates.append(mm.group(1))
            for arr in re.finditer(r'"image"\s*:\s*\[(.*?)\]', block, re.IGNORECASE | re.DOTALL):
                for u in re.findall(r'"(https?:\\/\\/[^"\\]+|\\/[^"\\]+)"', arr.group(1)):
                    image_candidates.append(u)

        img_patterns = [
            r'<img[^>]+(?:data-src|data-original|data-lazy-src|data-srcset|srcset|src)=["\']([^"\']+)["\'][^>]*(?:class|id|itemprop)=["\'][^"\']*(?:thumb|thumbnail|image|photo|figure|article|news|hero|lead|main)[^"\']*["\']',
            r'<img[^>]*(?:class|id|itemprop)=["\'][^"\']*(?:thumb|thumbnail|image|photo|figure|article|news|hero|lead|main)[^"\']*["\'][^>]+(?:data-src|data-original|data-lazy-src|data-srcset|srcset|src)=["\']([^"\']+)["\']',
            r'<img[^>]+(?:data-src|data-original|data-lazy-src|data-srcset|srcset|src)=["\']([^"\']+)["\']',
        ]
        for pat in img_patterns:
            for mm in re.finditer(pat, html, re.IGNORECASE | re.DOTALL):
                cand = mm.group(1)
                if looks_like_image_url(cand):
                    image_candidates.append(cand)

        seen = set()
        for raw in image_candidates:
            raw = (raw or '').replace('/', '/').strip()
            if ',' in raw and ' ' in raw:
                raw = raw.split(',')[0].strip().split()[0]
            final_img = make_absolute_url(current_url, raw)
            if not final_img or final_img in seen:
                continue
            seen.add(final_img)
            low = final_img.lower()
            if "lh3.googleusercontent.com" in low or "news.google.com" in low:
                continue
            if any(tok in low for tok in ["sprite", "icon", "logo", "favicon", "/ads/"]):
                continue
            image = final_img
            break

        snippet = None
        meta_candidates = [
            extract_meta(html, "og:description"),
            extract_meta(html, "twitter:description"),
            extract_meta(html, "description"),
        ]
        for snippet_raw in meta_candidates:
            cand = clean_spaces(snippet_raw) if snippet_raw else None
            if cand and is_valid_snippet(cand):
                snippet = cand
                break

        if not snippet:
            html_candidates = extract_candidate_snippets_from_html(html)
            if html_candidates:
                snippet = html_candidates[0]

        return image, snippet

    except Exception:
        return None, None

def optimize_thumbnail_bytes(data: bytes, subtype: str, display_w: int = THUMB_DISPLAY_W, display_h: int = THUMB_DISPLAY_H,
                             max_w: int = THUMB_MAX_W, max_h: int = THUMB_MAX_H, target_bytes: int = THUMB_TARGET_BYTES):
    """이메일 첨부용 썸네일을 작게 리사이즈/압축.
    - 노출 크기(120x90)에 맞춰 충분한 해상도만 유지
    - 사진류는 JPEG로 재인코딩하여 용량 절감
    - 목표 용량을 넘으면 품질/해상도를 추가로 낮춤
    """
    if not data:
        return data, subtype

    try:
        with Image.open(io.BytesIO(data)) as im:
            im = ImageOps.exif_transpose(im)
            frame = getattr(im, "n_frames", 1)
            if frame > 1:
                try:
                    im.seek(0)
                except Exception:
                    pass

            if im.mode not in ("RGB", "RGBA", "L", "LA", "P"):
                im = im.convert("RGB")

            # 실제 표시보다 약간만 큰 크기로 제한
            thumb = im.copy()
            thumb.thumbnail((max_w, max_h), Image.Resampling.LANCZOS)

            # 너무 작은 이미지는 확대하지 않음
            has_alpha = thumb.mode in ("RGBA", "LA") or (thumb.mode == "P" and 'transparency' in thumb.info)
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
                    current.save(buf, format="JPEG", quality=quality, optimize=True, progressive=True, subsampling="4:2:0")
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

    user_agents = [USER_AGENT, GOOGLEBOT_UA]
    accepts = [
        "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "image/webp,image/apng,image/*,*/*;q=0.8",
        "*/*",
    ]

    for ua in user_agents:
        for referer in referer_candidates:
            for accept in accepts:
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


def prepare_inline_images(all_articles):
    inline_images = {}

    for kw, articles in all_articles.items():
        for idx, article in enumerate(articles, start=1):
            cid = f"thumb-{hashlib.md5((article.get('link', '') + str(idx)).encode('utf-8')).hexdigest()[:16]}"
            img_url = article.get("image") or ""
            data, subtype = download_image_bytes(img_url, article.get("link", "")) if img_url else (None, None)
            if not data or not subtype:
                article["inline_cid"] = ""
                continue

            data, subtype = optimize_thumbnail_bytes(data, subtype)
            if not data or not subtype:
                article["inline_cid"] = ""
                continue

            inline_images[cid] = {"data": data, "subtype": subtype, "filename": f"{cid}.{subtype}"}
            article["inline_cid"] = cid

    return inline_images

# ── 중복 제거, 검색 쿼리, 관련도, 점수, 네이버/구글 검색 함수 (원본 그대로) ─────
def dedupe_articles(articles):
    seen = set()
    result = []
    for article in articles:
        key = (normalize_text(article.get("title", "")), normalize_text(article.get("press", "")))
        if key in seen:
            continue
        seen.add(key)
        result.append(article)
    return result

def build_search_query(keyword):
    query_map = {
        "아웃링크": "아웃링크 앱스토어 | 아웃링크 인앱결제 | 아웃링크 애플 | 아웃링크 구글",
        "웹결제": "웹결제 앱 마켓 | 웹결제 인앱결제 | 웹결제 애플 | 웹결제 구글",
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
    text = normalize_text(f"{title} {desc}")
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
    if kw in normalize_text(title): score += 5
    if kw in normalize_text(desc):  score += 3
    for p in KEYWORDS_PLATFORM:
        if normalize_text(p) in text: score += 2
    for p in POLICY_HINTS:
        if normalize_text(p) in text: score += 1
    strong = [
        "인앱결제", "외부결제", "앱스토어", "플레이스토어", "애플", "구글",
        "수수료", "정책", "규제", "소송", "방통위", "공정위",
        "안티스티어링", "사이드로딩", "디지털시장법"
    ]
    title_norm = normalize_text(title)
    for w in strong:
        if normalize_text(w) in title_norm: score += 2
    return score


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
        title   = clean_spaces(strip_html(item.get("title", "")))
        desc    = clean_spaces(strip_html(item.get("description", "")))
        link    = item.get("originallink") or item.get("link", "")
        pub_str = item.get("pubDate", "")

        try:
            pub_dt = parsedate_to_datetime(pub_str).astimezone(timezone.utc)
            if pub_dt < week_ago_dt:
                continue
            pub_label = pub_dt.strftime("%Y.%m.%d")
        except:
            continue

        if not title:
            continue
        if is_blocked_domain(link):
            continue
        if not is_relevant_article(keyword, title, desc):
            continue

        press = get_press_name(link, title)
        score = score_article(keyword, title, desc)
        print(f"  [NAVER/{keyword}] ({score}) {title[:50]}...")

        image, snippet = get_article_info(link)

        if not desc or normalize_text(desc) == normalize_text(title):
            desc = snippet or ""

        articles.append({
            "title": title, "press": press, "link": link,
            "summary": desc, "date": pub_label,
            "score": score, "keyword": keyword, "image": image
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
            title_el  = item.find("title")
            link_el   = item.find("link")
            desc_el   = item.find("description")
            pub_el    = item.find("pubDate")
            source_el = item.find("source")

            if not (title_el is not None and link_el is not None and title_el.text):
                continue

            title_raw = clean_spaces(strip_html(title_el.text))
            title = title_raw.rsplit(" - ", 1)[0].strip() if " - " in title_raw else title_raw
            press = get_press_name(link_el.text.strip(), title_raw)

            link = link_el.text.strip()
            real_link = source_el.get("url") if source_el is not None else None

            desc_raw = clean_spaces(strip_html(desc_el.text if desc_el is not None else ""))
            pub_str  = pub_el.text if pub_el is not None else ""

            try:
                pub_dt = parsedate_to_datetime(pub_str).astimezone(timezone.utc)
                if pub_dt < week_ago_dt:
                    continue
                pub_label = pub_dt.strftime("%Y.%m.%d")
            except:
                continue

            if not title:
                continue
            if is_blocked_domain(real_link or link):
                continue
            if not is_relevant_article(keyword, title, desc_raw):
                continue

            score = score_article(keyword, title, desc_raw)
            print(f"  [GOOGLE/{keyword}] ({score}) {title[:50]}...")

            image, snippet = get_article_info(real_link or link)

            desc = desc_raw
            if not desc or normalize_text(desc).startswith(normalize_text(title)):
                desc = snippet or ""

            articles.append({
                "title": title, "press": press, "link": link,
                "summary": desc, "date": pub_label,
                "score": score, "keyword": keyword, "image": image
            })

        articles.sort(key=lambda x: x["score"], reverse=True)
        articles = dedupe_articles(articles)
        articles = [a for a in articles if a["score"] >= MIN_ARTICLE_SCORE]
        return articles[:3]

    except Exception as e:
        print(f"  [ERROR] Google {keyword} 실패: {e}")
        return []

# ── 해외 규제기관 공식 RSS 수집 ──────────────────────────────
# 구글 뉴스(2차 보도) 대신 규제기관 1차 발표문을 직접 수집
REGULATORY_SOURCES = [
    {
        "label": "FTC",
        "url": "https://www.ftc.gov/feeds/press-release.xml",
        "lang": "en",
    },
    {
        "label": "CMA",
        "url": "https://www.gov.uk/government/organisations/competition-and-markets-authority.atom",
        "lang": "en",
    },
    {
        "label": "EU Commission",
        "url": "https://ec.europa.eu/commission/presscorner/api/rss",
        "lang": "en",
    },
    {
        "label": "Google Play Blog",
        "url": "https://blog.google/products/google-play/rss/",
        "lang": "en",
    },
    {
        "label": "Apple Newsroom",
        "url": "https://www.apple.com/newsroom/rss-feed.rss",
        "lang": "en",
    },
]

# 규제기관 기사 관련성 판단용 키워드
REGULATORY_FILTER_KEYWORDS = [
    "app store", "app market", "google play", "apple", "in-app purchase",
    "in-app payment", "digital markets act", "dma", "sideloading",
    "anti-steering", "commission", "antitrust", "app developer",
    "앱스토어", "앱마켓", "인앱결제", "디지털시장법", "수수료", "규제",
]

def fetch_regulatory_articles():
    """해외 규제기관 공식 RSS에서 앱마켓 관련 기사를 직접 수집."""
    results = {}  # label → [article, ...]

    for src in REGULATORY_SOURCES:
        label = src["label"]
        rss_url = src["url"]
        articles = []

        try:
            req = Request(rss_url)
            req.add_header("User-Agent", USER_AGENT)
            req.add_header("Accept", "application/rss+xml,application/xml,text/xml,*/*")
            with urlopen(req, timeout=15) as resp:
                xml_data = resp.read().decode("utf-8", errors="ignore")

            root = ET.fromstring(xml_data)
            # RSS 2.0: .//item  /  Atom: .//entry
            items = root.findall(".//{http://www.w3.org/2005/Atom}entry") or root.findall(".//item")

            for item in items[:20]:
                # 제목
                title_el = (
                    item.find("{http://www.w3.org/2005/Atom}title") or
                    item.find("title")
                )
                title_raw = clean_spaces(strip_html(title_el.text if title_el is not None else ""))
                if not title_raw:
                    continue

                # 링크
                link_el = (
                    item.find("{http://www.w3.org/2005/Atom}link") or
                    item.find("link")
                )
                if link_el is not None:
                    link = link_el.get("href") or (link_el.text or "").strip()
                else:
                    link = ""

                # 날짜
                date_el = (
                    item.find("{http://www.w3.org/2005/Atom}updated") or
                    item.find("{http://www.w3.org/2005/Atom}published") or
                    item.find("pubDate")
                )
                date_text = date_el.text if date_el is not None else ""
                try:
                    pub_dt = parsedate_to_datetime(date_text).astimezone(timezone.utc)
                except Exception:
                    try:
                        from datetime import datetime as _dt
                        pub_dt = _dt.fromisoformat(date_text.replace("Z", "+00:00"))
                    except Exception:
                        continue
                if pub_dt < week_ago_dt:
                    continue
                pub_label = pub_dt.strftime("%Y.%m.%d")

                # 설명
                desc_el = (
                    item.find("{http://www.w3.org/2005/Atom}summary") or
                    item.find("{http://www.w3.org/2005/Atom}content") or
                    item.find("description")
                )
                desc_raw = clean_spaces(strip_html(desc_el.text if desc_el is not None else ""))

                # 관련성 필터: 제목+설명에 앱마켓 관련 키워드가 하나라도 있어야 수집
                combined = (title_raw + " " + desc_raw).lower()
                if not any(kw.lower() in combined for kw in REGULATORY_FILTER_KEYWORDS):
                    continue

                print(f"  [REGULATORY/{label}] {title_raw[:60]}...")

                image, snippet = get_article_info(link) if link else (None, None)
                summary = snippet or desc_raw

                articles.append({
                    "title":   title_raw,
                    "press":   label,
                    "link":    link,
                    "summary": summary,
                    "date":    pub_label,
                    "score":   15,          # 1차 발표문이므로 높은 점수 고정
                    "keyword": f"[규제기관] {label}",
                    "image":   image,
                    "is_regulatory": True,  # 규제기관 발표 표시 플래그
                })

            if articles:
                articles.sort(key=lambda x: x["date"], reverse=True)
                results[f"[규제기관] {label}"] = articles[:2]  # 기관당 최신 2건
                print(f"    → {label}: {len(results[f'[규제기관] {label}'])}건 수집")

        except Exception as e:
            print(f"  [ERROR] {label} RSS 실패: {e}")

    return results

# ── 핵심 요약 ────────────────────────────────────────────────
POLICY_KEYWORDS = [
    "수수료", "정책", "규제", "법", "인하", "허용", "금지", "의무",
    "심사", "결제", "소송", "방통위", "공정위", "안티스티어링",
    "외부결제", "인앱결제", "제3자결제", "사이드로딩"
]

def extract_best_sentence(text: str, title: str = "") -> str:
    """summary에서 가장 핵심적인 1문장을 추출. 없으면 title 사용."""
    text = clean_spaces(text)
    if not text:
        return _trim(title, 60) if title else ""

    # 문장 분리 (마침표, 느낌표, 물음표 기준)
    sents = re.split(r"(?<=[다요죠까네\.!?])\s+", text)
    sents = [s.strip() for s in sents if len(s.strip()) >= 20]

    # 연결어로 시작하는 문장 제거
    bad_starts = ("이에 ", "이를 ", "이후 ", "이와 ", "한편 ", "또한 ",
                  "그러나 ", "하지만 ", "따라서 ", "이같은 ", "이런 ", "이같이 ",
                  "특히 이", "이 같은", "이어 ")
    filtered = [s for s in sents if not any(s.startswith(b) for b in bad_starts)]
    if not filtered:
        filtered = sents

    if not filtered:
        return _trim(title, 60)

    # 정책 키워드 포함 문장 우선
    scored = []
    for s in filtered:
        sc = sum(1 for pk in POLICY_KEYWORDS if pk in s)
        scored.append((sc, s))
    scored.sort(key=lambda x: -x[0])

    best = sanitize_summary_line(scored[0][1])
    return _trim(best, 70)


def _trim(text: str, max_len: int) -> str:
    """자연스러운 위치에서 말줄임표 처리."""
    if len(text) <= max_len:
        return text
    # 조사/어미 단위로 자르기
    cut = text[:max_len]
    for sep in (" ", ",", "，"):
        idx = cut.rfind(sep)
        if idx > max_len * 0.6:
            cut = cut[:idx]
            break
    return cut.rstrip(" ,，.。") + "…"
def build_summary_html(all_articles):
    items = []
    seen_texts = set()

    for kw, articles in all_articles.items():
        for a in articles:
            src = a.get("summary") or ""
            line = extract_best_sentence(src, a.get("title", ""))
            if not line or line in seen_texts:
                continue
            seen_texts.add(line)
            priority = sum(1 for pk in POLICY_KEYWORDS if pk in line or pk in a.get("title",""))
            items.append((priority, line))

    items.sort(key=lambda x: -x[0])
    top3 = []
    seen2 = set()
    for _, line in items:
        line = sanitize_summary_line(line)
        if not line or line in seen2:
            continue
        seen2.add(line)
        top3.append(line)
        if len(top3) >= 3:
            break

    if not top3:
        return '<div style="font-size:14px;color:#94a3b8;padding:4px 0;">이번 주 주요 내용을 찾지 못했습니다.</div>'

    accent_colors = ["#475569"] * 3
    rows = ""
    for i, line in enumerate(top3):
        color = accent_colors[i % len(accent_colors)]
        rows += f"""
        <tr>
          <td style="padding:0 0 10px 0;">
            <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
              <tr>
                <td width="4" style="background-color:{color};border-radius:3px;">&nbsp;</td>
                <td style="padding:10px 14px;background-color:#ffffff;border-radius:0 8px 8px 0;
                            font-size:14px;line-height:22px;color:#1e293b;border:1px solid #e5e7eb;border-left:none;">
                  {line}
                </td>
              </tr>
            </table>
          </td>
        </tr>"""

    return f"""<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">{rows}</table>"""

# ── HTML 생성 (5가지 요청 모두 적용) ────────────────────────
def to_html(all_articles):
    palette = ["#4f46e5", "#db2777", "#d97706", "#059669", "#2563eb", "#dc2626", "#7c3aed", "#0891b2"]
    kw_colors = {kw: palette[i % len(palette)] for i, kw in enumerate(all_articles.keys())}

    article_count = sum(len(v) for v in all_articles.values())
    issue_count   = len(all_articles)

    cards_html = ""
    total_count = 0
    for kw, articles in all_articles.items():
        color = kw_colors[kw]
        tag_bg = color + "18"
        for a in articles:
            total_count += 1
            cid = a.get("inline_cid", "")
            image_td = ""
            text_pl = "18px"
            if cid:
                image_td = f'''
              <td width="130" style="padding:11px 0 11px 12px;vertical-align:top;">
                <img src="cid:{cid}" width="{THUMB_DISPLAY_W}" height="{THUMB_DISPLAY_H}"
                     style="width:{THUMB_DISPLAY_W}px;height:{THUMB_DISPLAY_H}px;border-radius:10px;display:block;background-color:#f8fafc;object-fit:cover;" alt="">
              </td>
            '''
                text_pl = "8px"

            summary_text = clean_spaces(a.get("summary", ""))
            if summary_text:
                summary_text = extract_best_sentence(summary_text, a["title"])
            if not summary_text or normalize_text(summary_text) == normalize_text(a["title"]):
                summary_text = _trim(a["title"], 90)

            # 규제기관 발표 카드는 왼쪽 강조선 색상을 파란색으로, 공식발표 배지 추가
            is_reg = a.get("is_regulatory", False)
            border_color = "#0369a1" if is_reg else color
            reg_badge = (
                '<span style="display:inline-block;background-color:#e0f2fe;color:#0369a1;'
                'font-size:10px;font-weight:700;padding:1px 7px;border-radius:999px;'
                'margin-left:6px;">📢 공식 발표</span>'
            ) if is_reg else ""

            cards_html += f"""
            <tr>
              <td style="padding:0 32px 10px 32px;">
                <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
                       style="background-color:#d1d5db;border-radius:14px;overflow:hidden;">
                  <tr>
                    <td style="padding:1px;">
                      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
                             style="background-color:#ffffff;border-radius:13px;overflow:hidden;">
                        <tr>
                          <td width="5" style="background-color:{border_color};font-size:0;">&nbsp;</td>
                          {image_td}
                          <td style="padding:12px 18px 12px {text_pl};vertical-align:top;background-color:#ffffff;">
                            <div style="margin-bottom:6px;">
                              <span style="display:inline-block;background-color:{tag_bg};color:{color};
                                           font-size:11px;font-weight:700;padding:2px 9px;border-radius:999px;">{kw}</span>{reg_badge}
                            </div>
                            <div style="font-size:16px;line-height:24px;color:#111827;font-weight:800;
                                        font-family:'Apple SD Gothic Neo','Malgun Gothic',Arial,sans-serif;">
                              {a['title']}
                            </div>
                            <div style="padding-top:5px;font-size:13px;line-height:21px;color:#4b5563;">
                              {summary_text}
                            </div>
                            <div style="padding-top:8px;">
                              <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
                                <tr>
                                  <td style="font-size:11px;color:#9ca3af;vertical-align:middle;">
                                    {a['date']}{(' &middot; ' + a['press']) if a.get('press') else ''}
                                  </td>
                                  <td style="text-align:right;vertical-align:middle;">
                                    <a href="{a['link']}" style="display:inline-block;color:#ffffff;text-decoration:none;
                                       font-size:12px;font-weight:700;padding:5px 12px;border-radius:7px;
                                       background-color:#374151;">&#128279; 원문보기</a>
                                  </td>
                                </tr>
                              </table>
                            </div>
                          </td>
                        </tr>
                      </table>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>"""

    empty_html = '<tr><td style="padding:0 32px 24px;color:#94a3b8;">이번 주 관련 기사를 찾지 못했습니다.</td></tr>'

    return f"""<!DOCTYPE html>
<html lang="ko">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="margin:0;padding:0;background-color:#eef0f7;font-family:'Apple SD Gothic Neo','Malgun Gothic',Arial,sans-serif;color:#1f2937;">
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background-color:#eef0f7;">
<tr><td align="center" style="padding:28px 12px;">
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="810"
       style="max-width:810px;background-color:#ffffff;border-radius:20px;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,0.10);">

<!-- ══ 헤더 + 통계 바 통합 ══ -->
  <tr>
    <td style="background:
        radial-gradient(ellipse at 25% 60%, rgba(110,120,247,0.55) 0%, transparent 52%),
        radial-gradient(ellipse at 82% 18%, rgba(0,212,255,0.25) 0%, transparent 46%),
        radial-gradient(ellipse at 52% 95%, rgba(168,85,247,0.30) 0%, transparent 48%),
        linear-gradient(135deg, #0f0c29 0%, #302b63 55%, #1a1a4e 100%);
        padding:0;">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
        <tr>
          <td style="padding:22px 0 13px 62px;vertical-align:middle;">
            <div style="font-size:13px;font-weight:800;letter-spacing:3px;
                        color:rgba(147,197,253,0.85);margin-bottom:11px;
                        font-family:Arial,sans-serif;">
              &#128225;&nbsp;&nbsp;WEEKLY APP MARKET NEWS
            </div>
            <div style="margin-bottom:11px;">
              <img src="{IMAGE1_BASE64}"
                   height="56"
                   style="height:62px;width:auto;display:block;" alt="주간 앱 마켓 뉴스레터">
            </div>
            <div style="font-size:14px;color:rgba(180,215,255,0.75);font-family:Arial,sans-serif;">
              &#9679; 검색 범위 : {week_ago} ~ {today}
            </div>
          </td>
          <td style="padding:14px 0px 14px 70px;vertical-align:middle;
                     text-align:center;width:340px;">
            <img src="{IMAGE2_BASE64}"
                 style="max-width:250px;height:140px;display:block;border-radius:20px;"
                 alt="App Market Visual">
          </td>
        </tr>
        <tr>
          <td colspan="2" style="padding:0 0 5px 0;
                        border-top:1px solid rgba(255,255,255,0.08);">
<table role="presentation" cellpadding="0" cellspacing="0"
       border="0" width="100%">
  <tr>
    <td width="33%" style="text-align:center;padding:4px 0;border-right:1px solid rgba(255,255,255,0.1);">
      <div style="font-size:14px;font-weight:700;color:#ffffff;">{week_label}</div>
      <div style="font-size:10px;color:rgba(180,200,240,0.6);margin-top:1px;">월주차</div>
    </td>
    <td width="33%" style="text-align:center;padding:4px 0;border-right:1px solid rgba(255,255,255,0.1);">
      <div style="font-size:15px;font-weight:700;color:#ffffff;">{article_count}</div>
      <div style="font-size:10px;color:rgba(180,200,240,0.6);margin-top:1px;">기사 개수</div>
    </td>
    <td width="33%" style="text-align:center;padding:4px 0;">
      <div style="font-size:15px;font-weight:700;color:#ffffff;">{issue_count}</div>
      <div style="font-size:10px;color:rgba(180,200,240,0.6);margin-top:1px;">검색 키워드</div>
    </td>
  </tr>
</table>
          </td>
        </tr>
      </table>
    </td>
  </tr>


  <!-- 인사말 -->
<tr>
  <td style="padding:22px 32px 10px 32px;font-size:14px;line-height:22px;color:#1d497c;">
    <b>안녕하세요.<br>
    최근 일주일간 키워드별 주요 기사를 정리해 공유드립니다.</b>
  </td>
</tr>

  <!-- 주요 기사 헤더 -->
  <tr>
    <td style="padding:20px 32px 10px 32px;">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
        <tr>
          <td style="font-size:18px;font-weight:800;color:#0f172a;white-space:nowrap;padding-right:12px;">
            &#128240; 주요 기사<br>
          </td>
          <td width="100%">
            <div style="height:2px;background-color:#e0d9ff;border-radius:2px;"></div>
          </td>
        </tr>
      </table>
    </td>
  </tr>

  <!-- 기사 카드 -->
  {cards_html if total_count > 0 else empty_html}


<!-- ══ 푸터 ══ -->
  <tr>
    <td style="background:
        radial-gradient(ellipse at 15% 50%, rgba(99,102,241,0.55) 0%, transparent 52%),
        radial-gradient(ellipse at 85% 20%, rgba(0,212,255,0.28) 0%, transparent 46%),
        radial-gradient(ellipse at 50% 100%, rgba(168,85,247,0.38) 0%, transparent 48%),
        linear-gradient(135deg, #0f0c29 0%, #302b63 55%, #1a1a4e 100%);
        padding:18px 32px;text-align:center;border-radius:0 0 20px 20px;">
      <div style="font-size:15px;font-weight:900;margin-bottom:6px;font-family:'Apple SD Gothic Neo','Malgun Gothic',Arial,sans-serif;">
        &#128241;
        <span style="color:#ffffff;">주간 앱 마켓&nbsp;</span><span style="color:#38bdf8;">뉴</span><span style="color:#60a5fa;">스</span><span style="color:#818cf8;">레</span><span style="color:#a78bfa;">터</span>
      </div>
      <div style="font-size:11px;color:rgba(186,230,253,0.75);line-height:1.9;font-family:Arial,sans-serif;">
        매주 월요일 발행 &middot; 구독 문의:
        <span style="color:#67e8f9;">hj@kisa.or.kr</span><br>
        자동 발송 &middot; {today}
      </div>
    </td>
  </tr>

</table>
</td></tr>
</table>
</body></html>"""

# ── 메일 발송 ───────────────────────────────────────────────
def send_mail(html, inline_images):
    recipients = [x.strip() for x in MAIL_TO.split(",") if x.strip()]

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(GMAIL_ID, GMAIL_PW)
        for recipient in recipients:
            msg = MIMEMultipart("related")
            msg["Subject"] = f"[앱 마켓 뉴스 레터] {today}"
            msg["From"] = formataddr((str(Header("KISA(김형진)", "utf-8")), f"{GMAIL_ID}@gmail.com"))
            msg["To"] = recipient

            alt = MIMEMultipart("alternative")
            alt.attach(MIMEText(html, "html", "utf-8"))
            msg.attach(alt)

            for cid, payload in inline_images.items():
                img_part = MIMEImage(payload["data"], _subtype=payload["subtype"])
                img_part.add_header("Content-ID", f"<{cid}>")
                img_part.add_header("Content-Disposition", "inline", filename=payload["filename"])
                msg.attach(img_part)

            smtp.sendmail(msg["From"], [recipient], msg.as_string())
            print(f"  → {recipient} 발송 완료")

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
            top3 = combined[:3]                                      # score 기준 상위 3개 선발
            top3.sort(key=lambda x: x.get("date", ""), reverse=True)  # 그 안에서 날짜 내림차순
            all_articles[kw] = top3
            print(f"    → {len(all_articles[kw])}건 수집")

    # ── 해외 규제기관 공식 RSS 수집 (Naver/Google과 별도) ──────
    print("🌐 해외 규제기관 공식 RSS 수집 중... (FTC / CMA / EU / Google Play Blog / Apple Newsroom)")
    regulatory_articles = fetch_regulatory_articles()
    if regulatory_articles:
        all_articles.update(regulatory_articles)
        reg_count = sum(len(v) for v in regulatory_articles.values())
        print(f"  → 규제기관 기사 {reg_count}건 추가")
    else:
        print("  → 이번 주 관련 규제기관 발표 없음")

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
    for v in all_articles.values():
        v.sort(key=lambda x: x.get("date", ""), reverse=True)
    total_found = sum(len(v) for v in all_articles.values())
    print(f"✅ 중복 제거 완료 (제거: {removed_count}건, 최종: {total_found}건)")

    inline_images = prepare_inline_images(all_articles)
    html = to_html(all_articles)
    send_mail(html, inline_images)
    print(f"✅ 전체 완료 (발송 기사: {total_found}건)")
