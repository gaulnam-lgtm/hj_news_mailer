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
from xml.etree import ElementTree as ET   # ← Google RSS 파싱용 추가


# ── 설정 ────────────────────────────────────────────────────
GMAIL_ID = os.environ["GMAIL_ID"]
GMAIL_PW = os.environ["GMAIL_APP_PASSWORD"]
MAIL_TO = os.environ["MAIL_TO"]
KEYWORDS = json.loads(os.environ["KEYWORDS"])
NAVER_CLIENT_ID = os.environ["NAVER_CLIENT_ID"]
NAVER_CLIENT_SECRET = os.environ["NAVER_CLIENT_SECRET"]

MIN_ARTICLE_SCORE = int(os.environ.get("MIN_ARTICLE_SCORE", "9"))

# ... (KEYWORDS_PLATFORM, KEYWORDS_EXCLUDE, today, week_ago 등 기존 그대로) ...

today_dt = datetime.now(timezone.utc)
today = today_dt.strftime("%Y년 %m월 %d일")
week_ago_dt = today_dt - timedelta(days=7)
week_ago = week_ago_dt.strftime("%Y년 %m월 %d일")

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"


# ── 기존 유틸 함수들 (strip_html, normalize_text, clean_spaces, get_domain, make_absolute_url, get_article_image, dedupe_articles) 그대로 유지 ──
# (코드 길이 때문에 생략했지만, 이전 버전과 100% 동일하게 복사해서 사용하세요)


# ── 검색 쿼리 보정 ──────────────────────────────────────────
def build_search_query(keyword):
    query_map = { ... }  # 기존 그대로
    return query_map.get(keyword, keyword)


# ── 관련도 판별, score_article 함수들 그대로 유지 ──


# ── 네이버 뉴스 검색 (기존 함수 이름만 변경) ─────────────────────
def fetch_naver_articles(keyword):
    # 기존 fetch_articles 함수 내용 그대로 복사 (변경 없음)
    # ... (이전 버전 그대로) ...
    # 마지막에 return articles[:3] 대신 아래처럼 변경
    articles = [...]  # 기존 로직
    articles.sort(key=lambda x: x["score"], reverse=True)
    articles = dedupe_articles(articles)
    articles = [a for a in articles if a["score"] >= MIN_ARTICLE_SCORE]
    return articles[:5]  # 구글과 합치기 위해 5개까지 여유롭게


# ── 구글 뉴스 RSS 추가 (새 함수) ───────────────────────────────
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

            if not (title_el and link_el and title_el.text):
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

            press = get_domain(link)
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
                "image": image,
            })

        articles.sort(key=lambda x: x["score"], reverse=True)
        articles = dedupe_articles(articles)
        articles = [a for a in articles if a["score"] >= MIN_ARTICLE_SCORE]
        return articles[:5]   # 네이버와 합치기 위해 5개까지

    except Exception as e:
        print(f"  [ERROR] Google RSS {keyword} 실패: {e}")
        return []


# ── 요약 생성, to_html 함수는 기존 그대로 유지 ─────────────────────


# ── 메일 발송 함수 그대로 ───────────────────────────────────────


# ── 실행 (Naver + Google 동시에 수집) ───────────────────────────
if __name__ == "__main__":
    print("🔍 네이버 + 구글 뉴스 탐색 중...")

    all_articles = {}
    total_found = 0

    for kw in KEYWORDS:
        print(f"  - {kw} 검색 중... (Naver + Google)")
        
        naver_arts = fetch_naver_articles(kw)
        google_arts = fetch_google_articles(kw)
        
        combined = naver_arts + google_arts
        combined.sort(key=lambda x: x["score"], reverse=True)
        combined = dedupe_articles(combined)  # 키워드 내 1차 중복 제거
        combined = [a for a in combined if a["score"] >= MIN_ARTICLE_SCORE]
        
        if combined:
            # 구글 추가로 인해 여유롭게 5개까지 보관 → 글로벌 중복 제거에서 최종 정리
            all_articles[kw] = combined[:5]
            total_found += len(all_articles[kw])
        else:
            print(f"    → 제외됨: {kw} (기사 없음)")

    # ==================== 기존 전역 중복 제거 로직 그대로 ====================
    print("🔄 전역 중복 제거 중... (네이버+구글 기사 통합 정리)")

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
        original_len = len(all_articles[kw])
        remove_set = set(remove_links)
        all_articles[kw] = [a for a in all_articles[kw] if a.get("link") not in remove_set]
        removed = original_len - len(all_articles[kw])
        if removed > 0:
            print(f"  [{kw}] 중복 제거: {removed}건")
            removed_count += removed

    all_articles = {k: v for k, v in all_articles.items() if v}
    total_found = sum(len(arts) for arts in all_articles.values())
    print(f"✅ 중복 제거 완료 (제거된 기사: {removed_count}건, 최종 기사 수: {total_found}건)")
    # =================================================================

    html = to_html(all_articles)
    send_mail(html)

    print(f"✅ 전체 완료 (최종 발송 기사 수: {total_found})")
