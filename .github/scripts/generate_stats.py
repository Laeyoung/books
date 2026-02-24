#!/usr/bin/env python3
"""
README.md에서 책 목록을 파싱하고, Gemini API로 카테고리를 분류한 뒤,
Mermaid 차트가 포함된 STATS.md를 생성하는 스크립트.
"""

import json
import os
import re
import sys
import time
from pathlib import Path

import google.generativeai as genai


def parse_books_from_readme(readme_path: str) -> dict[str, list[str]]:
    """README.md에서 연도별 책 목록을 파싱한다."""
    content = Path(readme_path).read_text(encoding="utf-8")
    books_by_year: dict[str, list[str]] = {}
    current_year = None

    # "읽고 싶은 책들" 섹션 이전까지만 파싱
    wishlist_marker = "## 읽고 싶은 책들"
    main_content = content.split(wishlist_marker)[0]

    for line in main_content.splitlines():
        # 연도 헤더 매칭: ### 2026년
        year_match = re.match(r"^###\s+(\d{4})년", line)
        if year_match:
            current_year = year_match.group(1)
            books_by_year[current_year] = []
            continue

        # 책 항목 매칭: - 책제목 (부가정보)
        if current_year and re.match(r"^-\s+", line):
            title = line.lstrip("- ").strip()
            # 괄호 안의 부가 정보 제거 (▲, 오디오북, 링크 등)
            # 단, "(만화)", "(사진집)" 같은 형식 정보는 유지
            clean_title = re.sub(r"\s*\[.*?\]\(.*?\)", "", title)  # 링크 제거
            clean_title = re.sub(r"\s*\(▲.*?\)", "", clean_title)  # ▲ 마커 제거
            clean_title = re.sub(
                r"\s*\(빠르게.*?\)", "", clean_title
            )  # 읽기 메모 제거
            clean_title = re.sub(
                r"\s*\(\d+년 전에.*?\)", "", clean_title
            )  # 재독 메모 제거
            clean_title = re.sub(
                r"\s*\(memo:.*?\)", "", clean_title
            )  # memo 제거
            clean_title = re.sub(
                r"\s*\(포기\)", "", clean_title
            )  # 포기 제거
            clean_title = re.sub(
                r"\s*\(오디오북\)", "", clean_title
            )  # 오디오북 제거
            clean_title = re.sub(
                r"\s*\(\s*\)", "", clean_title
            )  # 빈 괄호 제거
            clean_title = clean_title.strip()
            clean_title = clean_title.rstrip(",").strip()
            if clean_title:
                books_by_year[current_year].append(clean_title)

    return books_by_year


def categorize_books_with_gemini(
    books: list[str], api_key: str
) -> dict[str, str]:
    """Gemini API를 사용하여 책들을 카테고리별로 분류한다."""
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        "gemini-2.5-flash-lite",
        generation_config={"response_mime_type": "application/json"},
    )

    categories = {
        "소설/문학": "Fiction, Literature, Novel, Short stories, Poetry",
        "SF/판타지": "Science Fiction, Fantasy, Dystopia",
        "에세이/산문": "Essay, Memoir, Personal narrative",
        "경제/경영": "Business, Economics, Management, Marketing, Startup",
        "자기계발": "Self-help, Productivity, Personal growth",
        "역사": "History, Historical narrative",
        "사회/정치": "Society, Politics, Social issues, Feminism",
        "과학": "Science, Biology, Neuroscience, Psychology",
        "개발/IT": "Software development, Programming, IT, AI, ML, System design",
        "예술/사진": "Art, Photography, Art history, Design",
        "여행": "Travel, Travel guide, City guide",
        "요리/음식": "Cooking, Food, Recipe, Cocktail, Coffee",
        "만화": "Comics, Manga, Graphic novel",
        "철학/인문": "Philosophy, Humanities, Ethics, Religion",
        "기타": "Other, Uncategorizable",
    }

    category_desc = "\n".join(
        [f"- {k}: {v}" for k, v in categories.items()]
    )

    all_categorized: dict[str, str] = {}

    # 배치 처리 (한 번에 최대 100권씩 — API 호출 횟수를 최소화)
    batch_size = 100
    total_batches = (len(books) + batch_size - 1) // batch_size
    for batch_idx, i in enumerate(range(0, len(books), batch_size)):
        batch = books[i : i + batch_size]
        book_list = "\n".join([f"{idx+1}. {b}" for idx, b in enumerate(batch)])
        print(
            f"  배치 {batch_idx + 1}/{total_batches} ({len(batch)}권) 처리 중..."
        )

        prompt = f"""다음은 한국어 책 제목 목록입니다. 각 책을 아래 카테고리 중 하나로 분류해주세요.
정확히 하나의 카테고리만 선택해야 합니다.

카테고리:
{category_desc}

책 목록:
{book_list}

응답은 반드시 아래 JSON 형식으로만 답하세요. 키는 책 번호(문자열), 값은 카테고리명입니다:
{{"1": "카테고리명", "2": "카테고리명", ...}}
"""

        max_retries = 5
        for attempt in range(max_retries):
            try:
                response = model.generate_content(prompt)
                text = response.text.strip()
                # JSON 블록 추출
                json_match = re.search(r"\{.*\}", text, re.DOTALL)
                if json_match:
                    result = json.loads(json_match.group())
                    # 번호 기반 매핑
                    for idx, book_title in enumerate(batch):
                        str_idx = str(idx + 1)
                        cat = result.get(str_idx, "기타")
                        if cat in categories:
                            all_categorized[book_title] = cat
                        else:
                            all_categorized[book_title] = "기타"
                    break
                else:
                    print(
                        f"  JSON 파싱 실패 (시도 {attempt + 1}/{max_retries})",
                        file=sys.stderr,
                    )
            except json.JSONDecodeError as e:
                print(
                    f"  JSON 디코딩 오류 (시도 {attempt + 1}/{max_retries}): {e}",
                    file=sys.stderr,
                )
            except Exception as e:
                err_str = str(e).lower()
                # Rate limit / quota 오류 시 더 긴 대기
                if "429" in err_str or "quota" in err_str or "rate" in err_str:
                    wait = min(2 ** (attempt + 2), 60)
                    print(
                        f"  Rate limit 감지, {wait}초 대기 "
                        f"(시도 {attempt + 1}/{max_retries})",
                        file=sys.stderr,
                    )
                    time.sleep(wait)
                    continue
                print(
                    f"  API 오류 (시도 {attempt + 1}/{max_retries}): {e}",
                    file=sys.stderr,
                )

            if attempt < max_retries - 1:
                time.sleep(2 ** (attempt + 1))
        else:
            # 모든 재시도 실패 시 기타로 분류
            for book_title in batch:
                if book_title not in all_categorized:
                    all_categorized[book_title] = "기타"

        # API rate limit 대응 — 배치 간 충분한 간격
        if i + batch_size < len(books):
            time.sleep(2)

    return all_categorized


def load_cache(cache_path: str) -> dict[str, str]:
    """이전에 분류된 결과 캐시를 로드한다."""
    if os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_cache(cache_path: str, data: dict[str, str]) -> None:
    """분류 결과를 캐시에 저장한다."""
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def generate_stats_md(
    books_by_year: dict[str, list[str]],
    categorized: dict[str, str],
    output_path: str,
) -> None:
    """STATS.md 파일을 생성한다."""
    # 카테고리별 집계
    category_counts: dict[str, int] = {}
    category_books: dict[str, list[str]] = {}
    for book, cat in categorized.items():
        category_counts[cat] = category_counts.get(cat, 0) + 1
        if cat not in category_books:
            category_books[cat] = []
        category_books[cat].append(book)

    # 정렬 (많은 순)
    sorted_categories = sorted(
        category_counts.items(), key=lambda x: x[1], reverse=True
    )

    total_books = sum(category_counts.values())

    # 연도별 집계
    year_counts = {
        year: len(books)
        for year, books in sorted(books_by_year.items())
        if books
    }

    # STATS.md 생성
    lines: list[str] = []
    lines.append("# 📚 읽은 책 통계\n")
    lines.append(
        f"> 이 파일은 GitHub Actions에 의해 자동으로 생성됩니다. "
        f"README.md가 업데이트될 때마다 갱신됩니다.\n"
    )
    lines.append(f"**총 {total_books}권**의 책을 읽었습니다.\n")

    # 카테고리별 파이 차트 (Mermaid)
    lines.append("## 카테고리별 분포\n")
    lines.append("```mermaid")
    lines.append("pie showData")
    lines.append('    title 카테고리별 읽은 책 수')
    for cat, count in sorted_categories:
        lines.append(f'    "{cat}" : {count}')
    lines.append("```\n")

    # 연도별 바 차트 (Mermaid)
    lines.append("## 연도별 독서량\n")
    lines.append("```mermaid")
    lines.append("xychart-beta")
    lines.append('    title "연도별 읽은 책 수"')
    lines.append("    x-axis [" + ", ".join(year_counts.keys()) + "]")
    lines.append(
        "    y-axis \"권수\" 0 --> "
        + str(max(year_counts.values()) + 5)
    )
    lines.append(
        "    bar [" + ", ".join(str(v) for v in year_counts.values()) + "]"
    )
    lines.append("```\n")

    # 카테고리별 상세 목록
    lines.append("## 카테고리별 책 목록\n")
    for cat, count in sorted_categories:
        lines.append(f"### {cat} ({count}권)\n")
        books_in_cat = sorted(category_books[cat])
        for book in books_in_cat:
            lines.append(f"- {book}")
        lines.append("")

    # 연도별 통계 테이블
    lines.append("## 연도별 통계\n")
    lines.append("| 연도 | 권수 | 그래프 |")
    lines.append("|------|------|--------|")
    max_count = max(year_counts.values()) if year_counts else 1
    for year in sorted(year_counts.keys(), reverse=True):
        count = year_counts[year]
        bar_len = int((count / max_count) * 20)
        bar = "█" * bar_len
        lines.append(f"| {year} | {count} | {bar} |")
    lines.append("")

    Path(output_path).write_text("\n".join(lines), encoding="utf-8")
    print(f"STATS.md 생성 완료: {output_path}")


def main():
    readme_path = os.environ.get("README_PATH", "README.md")
    output_path = os.environ.get("STATS_OUTPUT", "STATS.md")
    cache_path = os.environ.get(
        "CACHE_PATH", ".github/scripts/category_cache.json"
    )
    api_key = os.environ.get("GEMINI_API_KEY")

    if not api_key:
        print("오류: GEMINI_API_KEY 환경변수가 설정되지 않았습니다.", file=sys.stderr)
        sys.exit(1)

    print("📖 README.md에서 책 목록 파싱 중...")
    books_by_year = parse_books_from_readme(readme_path)

    all_books = []
    for year_books in books_by_year.values():
        all_books.extend(year_books)
    print(f"   총 {len(all_books)}권 발견\n")

    # 캐시에서 이전 분류 결과 로드
    cache = load_cache(cache_path)
    new_books = [b for b in all_books if b not in cache]
    print(f"🔍 캐시된 분류: {len(all_books) - len(new_books)}권")
    print(f"🆕 새로 분류할 책: {len(new_books)}권\n")

    if new_books:
        print("🤖 Gemini API로 카테고리 분류 중...")
        new_categorized = categorize_books_with_gemini(new_books, api_key)
        cache.update(new_categorized)
        save_cache(cache_path, cache)
        print(f"   {len(new_categorized)}권 분류 완료\n")

    # 현재 README에 있는 책만 필터링
    categorized = {b: cache[b] for b in all_books if b in cache}

    print("📊 STATS.md 생성 중...")
    generate_stats_md(books_by_year, categorized, output_path)
    print("✅ 완료!")


if __name__ == "__main__":
    main()
