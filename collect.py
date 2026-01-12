#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import re
import time
from collections import deque
from pathlib import Path
from typing import Deque, Dict, List, Optional, Tuple

import pandas as pd
from bs4 import BeautifulSoup
from playwright.sync_api import Page, sync_playwright


# ----------------------------
# 文字正規化（全角→半角など）
# ----------------------------
_ZEN2HAN = str.maketrans({
    "０": "0", "１": "1", "２": "2", "３": "3", "４": "4",
    "５": "5", "６": "6", "７": "7", "８": "8", "９": "9",
    "－": "-", "−": "-", "ー": "-", "―": "-", "‐": "-",
    "　": " ",
    # 全角アルファベット→半角
    "Ａ": "A", "Ｂ": "B", "Ｃ": "C", "Ｄ": "D", "Ｅ": "E",
    "Ｆ": "F", "Ｇ": "G", "Ｈ": "H", "Ｉ": "I", "Ｊ": "J",
    "Ｋ": "K", "Ｌ": "L", "Ｍ": "M", "Ｎ": "N", "Ｏ": "O",
    "Ｐ": "P", "Ｑ": "Q", "Ｒ": "R", "Ｓ": "S", "Ｔ": "T",
    "Ｕ": "U", "Ｖ": "V", "Ｗ": "W", "Ｘ": "X", "Ｙ": "Y", "Ｚ": "Z",
    "ａ": "a", "ｂ": "b", "ｃ": "c", "ｄ": "d", "ｅ": "e",
    "ｆ": "f", "ｇ": "g", "ｈ": "h", "ｉ": "i", "ｊ": "j",
    "ｋ": "k", "ｌ": "l", "ｍ": "m", "ｎ": "n", "ｏ": "o",
    "ｐ": "p", "ｑ": "q", "ｒ": "r", "ｓ": "s", "ｔ": "t",
    "ｕ": "u", "ｖ": "v", "ｗ": "w", "ｘ": "x", "ｙ": "y", "ｚ": "z",
})

def norm_text(s: str) -> str:
    return (s or "").translate(_ZEN2HAN).strip()

def norm_room(raw: str) -> str:
    """
    英語シラバスの教室名を正規化:
      "63-3F-G" -> "63PC-G"  (PC room)
      "53-B04" -> "53-B04"   (basement)
      "55S-03-09" -> "55S-03-09"
      "55-2" -> "55-02" (floor only?)
      "52-101" -> "52-01-01" (building-floor-room)
    日本語シラバスも対応:
      "５３-１０３教室" -> "53-01-03"
      "63号館3階末端室Fルーム" -> "63PC-F"
    """
        # Normalize first so we can reliably strip prefixes even if they use full-width
        # digits/colons like "０１：".
    s = norm_text(raw)
    s = re.sub(r"^\s*\d{1,2}\s*[:：]\s*", "", s)  # "01:" / "０１：" など除去
    s = re.sub(r"（.*?）|\(.*?\)", "", s)     # 括弧注記除去

    if "未定" in s or s == "" or "TBD" in s.upper():
        return ""

    # 日本語特殊パターン: "63号館3階末端室Fルーム" -> "63PC-F"
    m_pc_ja = re.match(r"(\d+)号館\d*階?末端室?([A-Za-zＡ-Ｚａ-ｚ])[ルー]*ム?", s)
    if m_pc_ja:
        building = norm_text(m_pc_ja.group(1))
        room_letter = norm_text(m_pc_ja.group(2)).upper()
        return f"{building}PC-{room_letter}"

    # Now apply full normalization
    s = norm_text(s)
    s = s.replace("教室", "").replace("室", "").replace("ルーム", "").replace("ル-ム", "")
    s = s.replace(" ", "")

    if not s:
        return ""

    # Campus-specific aliases (templates/capacity list use these variants).
    if s == "61-102":
        return "61-102B"

    # 英語シラバス PC room: "63-3F-G" -> "63PC-G"
    # Pattern: building-floorF-letter (e.g., 63-3F-A, 63-3F-G)
    m_pc_en = re.match(r"^(\d+)-\d+F-([A-Za-z])$", s)
    if m_pc_en:
        building = m_pc_en.group(1)
        room_letter = m_pc_en.group(2).upper()
        return f"{building}PC-{room_letter}"

    # 既に正しいフォーマット (例: 55S-03-03, 63PC-A, 53-B04) ならそのまま
    if re.match(r"^\d+[A-Za-z]*-\d{2}-\d{2}$", s):
        return s
    if re.match(r"^\d+PC-[A-Za-z]$", s):
        return s
    if re.match(r"^\d+-B\d{2}$", s):  # 53-B04
        return s

    # "55S-03-09" or "55S-3-9" のようなフォーマット（ハイフン2つ）
    m_full = re.match(r"^(\d+[A-Za-z]*)-(\d+)-(\d+)$", s)
    if m_full:
        building = m_full.group(1)
        floor = m_full.group(2).zfill(2)
        room = m_full.group(3).zfill(2)
        return f"{building}-{floor}-{room}"

    # Some syllabus rows include suffixes like "55S-02-01-1".
    # Our CSV templates use the base "55S-02-01" format.
    m_base = re.match(r"^(\d+[A-Za-z]*-\d{2}-\d{2})(?:-\d+)?$", s)
    if m_base:
        return m_base.group(1)

    # Most rooms in this workspace use the short format (e.g. "53-104", "61-302").
    # Building 63 is an exception: template uses "63-02-01" style for "63-201".
    m_short3 = re.match(r"^(\d+)-(\d{3})([A-Za-z])?$", s)
    if m_short3:
        building = m_short3.group(1)
        digits = m_short3.group(2)
        suffix = m_short3.group(3)
        if building == "63":
            floor = digits[0].zfill(2)
            room = digits[1:3]
            return f"{building}-{floor}-{room}"
        # Keep as-is for all other buildings (and drop letter suffix here; we may resolve later)
        return f"{building}-{digits}" + (suffix.upper() if suffix else "")

    # "55-2" のような短い形式 -> "55-02" (floor only, keep as-is with padding)
    m_short = re.match(r"^(\d+)-(\d{1,2})$", s)
    if m_short:
        building = m_short.group(1)
        num = m_short.group(2).zfill(2)
        return f"{building}-{num}"

    # その他はそのまま返す
    return s


# ----------------------------
# 曜日・時限パース
# ----------------------------
DAY_MAP_JA = {"月": "mon", "火": "tue", "水": "wed", "木": "thu", "金": "fri", "土": "sat", "日": "sun"}
DAY_MAP_EN = {
    "Mon": "mon", "Mon.": "mon",
    "Tue": "tue", "Tues": "tue", "Tue.": "tue", "Tues.": "tue",
    "Wed": "wed", "Wed.": "wed",
    "Thu": "thu", "Thur": "thu", "Thu.": "thu", "Thur.": "thu",
    "Fri": "fri", "Fri.": "fri",
    "Sat": "sat", "Sat.": "sat",
    "Sun": "sun", "Sun.": "sun",
}

def parse_day_period(item: str) -> Optional[Tuple[str, int]]:
    """
    入力例:
      "月5時限" / "火３時限" / "01:土３時限"
      "Mon. 5" みたいなのが来ても一応拾う
      "無その他" "無フルOD" などは None
    """
    s = norm_text(item)
    s = re.sub(r"^\s*\d{1,2}\s*[:：]\s*", "", s)  # "01:" / "０１：" など除去
    if s.startswith("無") or "On demand" in s or "OD" in s:
        return None

    # 日本語形式: 月5時限
    m = re.search(r"([月火水木金土日])\s*([1-7])\s*時限", s)
    if m:
        day = DAY_MAP_JA[m.group(1)]
        period = int(m.group(2))
        return day, period

    # 英語っぽい: Mon. 5
    m2 = re.search(r"\b(Mon\.?|Tue\.?|Tues\.?|Wed\.?|Thu\.?|Thur\.?|Fri\.?|Sat\.?|Sun\.?)\b.*?\b([1-7])\b", s)
    if m2:
        day = DAY_MAP_EN[m2.group(1)]
        period = int(m2.group(2))
        return day, period

    return None


def parse_day_periods(item: str) -> Optional[Tuple[str, List[int]]]:
    """Parse a day + one-or-more periods.

    Supports inputs like:
      - "月5時限", "火３時限", "01:土３時限"
      - "Mon. 5", "Mon.2"
      - "Mon.4-5" (range) -> [4, 5]
      - "月4-5時限" (range) -> [4, 5]
    Returns None for "無...", OD, etc.
    """

    s = norm_text(item)
    s = re.sub(r"^\s*\d{1,2}\s*[:：]\s*", "", s)  # "01:" / "０１：" など除去
    if s.startswith("無") or "On demand" in s or "OD" in s:
        return None

    day: Optional[str] = None
    rest = s

    m_ja = re.search(r"([月火水木金土日])", s)
    if m_ja:
        day = DAY_MAP_JA[m_ja.group(1)]
        rest = s[m_ja.end():]
    else:
        m_en = re.search(
            r"\b(Mon\.?|Tue\.?|Tues\.?|Wed\.?|Thu\.?|Thur\.?|Fri\.?|Sat\.?|Sun\.?)\b",
            s,
        )
        if m_en:
            day = DAY_MAP_EN[m_en.group(1)]
            rest = s[m_en.end():]
        else:
            return None

    rest = rest.replace("時限", " ")
    rest = re.sub(r"\bto\b", "-", rest, flags=re.IGNORECASE)

    # First, try to parse a range like 4-5 (accept various dash chars).
    m_range = re.search(r"([1-7])\s*[-~〜－–]\s*([1-7])", rest)
    if m_range:
        a = int(m_range.group(1))
        b = int(m_range.group(2))
        lo, hi = (a, b) if a <= b else (b, a)
        return day, list(range(lo, hi + 1))

    # Otherwise, parse discrete period numbers.
    nums = [int(x) for x in re.findall(r"\b([1-7])\b", rest)]
    if not nums:
        return None

    seen = set()
    periods: List[int] = []
    for n in nums:
        if n in seen:
            continue
        seen.add(n)
        periods.append(n)

    return day, periods


# ----------------------------
# 複数行セル（01:...<BR>02:...）の処理
# ----------------------------
def split_td_lines(td) -> List[str]:
    """
    <td> の中身を <br> / <BR> 区切りでテキスト化して返す。
    """
    html = td.decode_contents()
    # <br>, <BR>, <br/>, <BR /> など全部対応
    chunks = re.split(r"<\s*br\s*/?\s*>", html, flags=re.IGNORECASE)

    out: List[str] = []
    for ch in chunks:
        txt = BeautifulSoup(ch, "lxml").get_text(" ", strip=True)
        if txt:
            # 念のため改行も分割
            for q in re.split(r"[\r\n]+", txt):
                q = q.strip()
                if q:
                    out.append(q)
    return out


def keyed_lines(lines: List[str]) -> List[Tuple[Optional[str], str]]:
    """
    "01:月5時限" のような prefix があればキーで対応付け。
    prefix が無い場合は None をキーとして扱い、元の順序を保つ。
    """
    entries: List[Tuple[Optional[str], str]] = []
    for s in lines:
        s2 = norm_text(s)
        if not s2:
            continue

        m = re.match(r"^(\d{1,2})\s*[:：]\s*(.*)$", s2)
        if m:
            key = m.group(1).zfill(2)
            value = m.group(2).strip()
        else:
            key = None
            value = s2

        entries.append((key, value))

    return entries

def pair_day_and_room(day_lines: List[str], room_lines: List[str]) -> List[Tuple[str, str]]:
    """
    day_lines と room_lines を 01/02 等で対応付け（無ければ順序で対応）。
    戻り: [(day_period_str, room_str), ...]
    """
    day_entries = keyed_lines(day_lines)
    room_entries = keyed_lines(room_lines)

    room_by_key: Dict[str, Deque[int]] = {}
    for idx, (room_key, _) in enumerate(room_entries):
        if not room_key:
            continue
        room_by_key.setdefault(room_key, deque()).append(idx)

    used_day = set()
    used_room = set()
    pairs: List[Tuple[str, str]] = []

    for day_idx, (day_key, day_value) in enumerate(day_entries):
        if not day_key:
            continue
        queue = room_by_key.get(day_key)
        if not queue:
            continue
        room_idx = queue.popleft()
        used_day.add(day_idx)
        used_room.add(room_idx)
        pairs.append((day_value, room_entries[room_idx][1]))

    day_seq = [day_entries[i][1] for i in range(len(day_entries)) if i not in used_day]
    room_seq = [room_entries[i][1] for i in range(len(room_entries)) if i not in used_room]

    for dp, rm in zip(day_seq, room_seq):
        pairs.append((dp, rm))

    return pairs


# ----------------------------
# 学期→どっちのCSVを埋めるか
# ----------------------------
def targets_from_term(term: str) -> List[str]:
    """
    指示通り：
      - 秋学期 => fall & winter
      - 秋クォーター => fallのみ
      - 冬クォーター => winterのみ
    ついでに英語表記っぽいのも拾う（環境によっては英語で出る可能性があるため）
    """
    t = norm_text(term)

    # 日本語
    if "秋学期" in t:
        return ["fall", "winter"]
    if "秋クォーター" in t:
        return ["fall"]
    if "冬クォーター" in t:
        return ["winter"]

    # 英語（保険）
    tl = t.lower()
    if "fall semester" in tl or ("fall" in tl and "semester" in tl):
        return ["fall", "winter"]
    if "fall quarter" in tl:
        return ["fall"]
    if "winter quarter" in tl:
        return ["winter"]

    # それ以外は無視（春学期など）
    return []


# ----------------------------
# CSV ロード/セーブ
# ----------------------------
def load_week_csvs(base_dir: Path, subdir: str) -> Dict[str, pd.DataFrame]:
    """
    data/period_room_fall/mon.csv などを読み込む
    """
    d = {}
    for day in ["mon", "tue", "wed", "thu", "fri"]:
        p = base_dir / "data" / subdir / f"{day}.csv"
        if not p.exists():
            raise FileNotFoundError(f"missing: {p}")
        df = pd.read_csv(p, dtype=str).fillna("")
        # period列名はあなたのテンプレだと "period"
        if "period" not in df.columns:
            raise ValueError(f"{p} must have 'period' column")
        d[day] = df
    return d

def save_week_csvs(base_dir: Path, subdir: str, tables: Dict[str, pd.DataFrame]) -> None:
    for day, df in tables.items():
        p = base_dir / "data" / subdir / f"{day}.csv"
        df.to_csv(p, index=False, encoding="utf-8-sig")


# ----------------------------
# セル埋め
# ----------------------------
def put_cell(df: pd.DataFrame, period: int, room: str, value: str) -> None:
    """
    df: period列がある
    room: 列名として存在するはず
    既存があれば ; で追記（重複は避ける）
    """
    if room not in df.columns:
        return
    if period not in set(df["period"].astype(int)):
        return

    idx = df.index[df["period"].astype(int) == period][0]
    cur = str(df.at[idx, room]).strip()
    if not cur:
        df.at[idx, room] = value
    else:
        # 既に同じ値が入ってたら何もしない
        vals = [x.strip() for x in cur.split(";") if x.strip()]
        if value not in vals:
            df.at[idx, room] = cur + ";" + value


RESULT_TABLE_SELECTOR = "table.ct-vh"
LANGUAGE_TOKEN_PATTERNS = [r"English", r"英語"]
DEFAULT_ACCEPT_LANGUAGE = "en-US,en;q=0.9,ja;q=0.4"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
TERM_REGEX = re.compile(r"fall\s*(?:/|＆|and)?\s*winter", re.IGNORECASE)


def ensure_english_ui(page: Page) -> Page:
    """Click through any language-gate page so we land on the English UI."""
    context = page.context

    def _alive_pages() -> List[Page]:
        return [p for p in context.pages if not p.is_closed()]

    for _ in range(3):
        if page.is_closed():
            alive = _alive_pages()
            if not alive:
                page = context.new_page()
            else:
                page = alive[-1]

        page.wait_for_timeout(500)
        handled = False
        for pat in LANGUAGE_TOKEN_PATTERNS:
            attempts = [
                lambda: page.get_by_role("button", name=re.compile(pat, re.IGNORECASE)),
                lambda: page.get_by_role("link", name=re.compile(pat, re.IGNORECASE)),
                lambda: page.get_by_text(re.compile(pat, re.IGNORECASE)),
            ]
            for factory in attempts:
                try:
                    loc = factory()
                except Exception:
                    continue
                if loc.count() == 0:
                    continue

                existing_ids = {id(p) for p in _alive_pages()}
                try:
                    loc.first.click()
                except Exception:
                    continue

                # Pick newly opened page if the click spawned one, otherwise reuse current.
                updated = _alive_pages()
                new_page = next((p for p in updated if id(p) not in existing_ids), None)
                if new_page:
                    page = new_page
                handled = True
                break

            if handled:
                break

        if not handled:
            return page

        try:
            page.wait_for_load_state("domcontentloaded")
        except Exception:
            pass

    return page


def select_fall_winter_term(page: Page) -> None:
    """Ensure the Fall/Winter term is selected, trying labels, selects, and JS fallback."""
    try:
        ctrl = page.get_by_label("Fall/Winter", exact=False)
        if ctrl.count() > 0:
            try:
                ctrl.first.check()
            except Exception:
                ctrl.first.click()
            return
    except Exception:
        pass

    try:
        text_loc = page.get_by_text("Fall/Winter", exact=False)
        if text_loc.count() > 0:
            text_loc.first.click()
            return
    except Exception:
        pass

    selects = page.locator("select")
    try:
        select_count = selects.count()
    except Exception:
        select_count = 0

    for idx in range(select_count):
        sel = selects.nth(idx)
        opt_locator = sel.locator("option")
        try:
            opt_count = opt_locator.count()
        except Exception:
            continue

        for i in range(opt_count):
            opt = opt_locator.nth(i)
            label = (opt.text_content() or "").strip()
            if not label or not TERM_REGEX.search(label):
                continue
            value = (opt.get_attribute("value") or "").strip()
            try:
                if value:
                    sel.select_option(value=value)
                else:
                    sel.select_option(label=label)
                return
            except Exception:
                continue

    success = page.evaluate(
        """
        (() => {
            const termRegex = /fall\s*(?:\/|＆|and)?\s*winter/i;
            const labels = Array.from(document.querySelectorAll('label'));
            for (const label of labels) {
                if (!termRegex.test(label.textContent || '')) continue;
                const control = label.control || label.querySelector('input,select');
                if (!control) continue;
                control.click();
                if ('checked' in control) control.checked = true;
                if ('dispatchEvent' in control) {
                    control.dispatchEvent(new Event('change', { bubbles: true }));
                }
                return true;
            }

            const options = Array.from(document.querySelectorAll('option'));
            for (const option of options) {
                if (!termRegex.test(option.textContent || '')) continue;
                option.selected = true;
                const select = option.parentElement;
                if (select) {
                    select.dispatchEvent(new Event('change', { bubbles: true }));
                }
                return true;
            }

            return false;
        })()
        """
    )

    if not success:
        raise RuntimeError("Failed to select the Fall/Winter term. UI updated?")


def open_fall_winter_listing(page: Page) -> Page:
    """Navigate to the search page, select Fall/Winter, and open the listing."""
    page.goto("https://www.wsl.waseda.jp/syllabus/JAA101.php?pLng=en", wait_until="domcontentloaded")
    page = ensure_english_ui(page)
    select_fall_winter_term(page)

    submitted = False
    btn = page.locator('input[name="btnSubmit"]')
    if btn.count() > 0:
        btn.first.click()
        submitted = True
    else:
        try:
            page.get_by_role("button", name="Search", exact=True).click()
            submitted = True
        except Exception:
            pass

    if not submitted:
        raise RuntimeError("Search button not found on the page.")

    try:
        page.wait_for_selector(RESULT_TABLE_SELECTOR, timeout=15000)
    except Exception as exc:
        raise RuntimeError("Result table not found after executing search.") from exc

    # Reduce the number of pages by switching to 100 items/page when possible.
    # This also tends to make pagination more stable.
    try:
        loc_100 = page.locator("a", has_text=re.compile(r"\b100\s*items\b", re.IGNORECASE))
        loc_50 = page.locator("a", has_text=re.compile(r"\b50\s*items\b", re.IGNORECASE))
        if loc_100.count() > 0:
            before = page.locator("div.c-selectall font").first.inner_text(timeout=2000)
            loc_100.first.click()
            page.wait_for_function(
                "(prev) => (document.querySelector('div.c-selectall font')?.textContent || '') !== prev",
                arg=before,
                timeout=15000,
            )
        elif loc_50.count() > 0:
            before = page.locator("div.c-selectall font").first.inner_text(timeout=2000)
            loc_50.first.click()
            page.wait_for_function(
                "(prev) => (document.querySelector('div.c-selectall font')?.textContent || '') !== prev",
                arg=before,
                timeout=15000,
            )
    except Exception:
        # If the UI changed or the link isn't available, continue with default page size.
        pass

    return page


def _parse_range_indicator(text: str) -> Optional[Tuple[int, int, int]]:
    """Parse strings like '8201～8300／16687' into (start, end, total)."""
    s = norm_text(text)
    if not s:
        return None
    m = re.search(r"(\d+)\s*[～\-]\s*(\d+)\s*[／/]\s*(\d+)", s)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def go_to_next_page(page, current_page_no: int, after_next_wait_sec: float = 0.8) -> bool:
    """Click the next pagination link if present.

    Uses the page-range indicator (e.g. "1～10／16687") to confirm the page actually advanced.
    Prints diagnostics when it cannot find a suitable next control.
    """

    def _range_text() -> str:
        try:
            return norm_text(page.locator("div.c-selectall font").first.inner_text(timeout=2000))
        except Exception:
            return ""

    # Give the site a moment to finish rendering pager controls.
    try:
        page.wait_for_timeout(200)
    except Exception:
        pass

    before = _range_text()
    info = _parse_range_indicator(before)
    if info:
        _start, _end, total = info
        # Infer page-size from the indicator itself.
        per_page = max(1, _end - _start + 1)
        total_pages = (total + per_page - 1) // per_page
        if current_page_no >= total_pages:
            return False

    # IMPORTANT: Do NOT search for a generic link containing "Next".
    # Course titles can include the word "Next" (e.g. "Introduction to Energy Next"),
    # which would navigate away from the result list and make the scraper "stop".
    # The real pager uses onclick="page_turning(...)".

    target_page = current_page_no + 1

    # Fallback 1 (preferred): directly invoke the site's JS pager.
    invoked = False
    try:
        invoked = bool(
            page.evaluate(
                """
                ({target}) => {
                    try {
                        if (typeof page_turning === 'function') {
                            page_turning('JAA103SubCon', String(target));
                            return true;
                        }
                    } catch (e) {}
                    return false;
                }
                """,
                {"target": target_page},
            )
        )
    except Exception:
        invoked = False

    if invoked:
        try:
            page.wait_for_function(
                "(prev) => (document.querySelector('div.c-selectall font')?.textContent || '') !== prev",
                arg=before,
                timeout=30000,
            )
            if after_next_wait_sec > 0:
                page.wait_for_timeout(int(after_next_wait_sec * 1000))
            return True
        except Exception:
            pass

    # Fallback 2: set hidden p_page and submit the form.
    submitted = False
    try:
        submitted = bool(
            page.evaluate(
                """
                ({target}) => {
                    const form = document.querySelector('form#cForm, form[name=cForm]');
                    if (!form) return false;
                    // Ensure required hidden inputs exist even if the page footer got truncated.
                    let p = form.querySelector('input[name=p_page]');
                    if (!p) {
                        p = document.createElement('input');
                        p.type = 'hidden';
                        p.name = 'p_page';
                        form.appendChild(p);
                    }
                    p.value = String(target);

                    let n = form.querySelector('input[name=p_number]');
                    if (!n) {
                        n = document.createElement('input');
                        n.type = 'hidden';
                        n.name = 'p_number';
                        n.value = '100';
                        form.appendChild(n);
                    }
                    form.submit();
                    return true;
                }
                """,
                {"target": target_page},
            )
        )
    except Exception:
        submitted = False

    if submitted:
        try:
            page.wait_for_function(
                "(prev) => (document.querySelector('div.c-selectall font')?.textContent || '') !== prev",
                arg=before,
                timeout=30000,
            )
            if after_next_wait_sec > 0:
                page.wait_for_timeout(int(after_next_wait_sec * 1000))
            return True
        except Exception:
            pass

    # Fallback 3: click the pager's "Next" link (must be page_turning).
    clicked = False
    try:
        next_loc = page.locator(
            "div.l-btn-c a[onclick*='page_turning'], a[onclick*='page_turning']",
            has_text=re.compile(r"Next|次へ", re.IGNORECASE),
        )
        if next_loc.count() > 0:
            next_loc.first.click()
            clicked = True
    except Exception:
        clicked = False

    if not clicked:
        # Fallback 1: directly invoke the site's JS pager (common on this site).
        # The second argument is the target page number.
        # Diagnostics: show pagination link texts we can see + dump html.
        try:
            pager_texts = page.locator("a").all_inner_texts()
            pager_texts = [norm_text(t) for t in pager_texts if norm_text(t)]
        except Exception:
            pager_texts = []
        try:
            html = page.content()
            Path("debug_no_next.html").write_text(html, encoding="utf-8")
        except Exception:
            pass
        print(
            f"[scrape] no-next-link found. range={before!r} "
            f"tried_js_page_turning={invoked} tried_form_submit={submitted} "
            f"example_link_texts={pager_texts[:30]} (saved: debug_no_next.html)"
        )
        return False

    # Confirm we actually advanced (range text changed).
    try:
        page.wait_for_function(
            "(prev) => (document.querySelector('div.c-selectall font')?.textContent || '') !== prev",
            arg=before,
            timeout=30000,
        )
    except Exception:
        # If range didn't change, still try waiting for the table, then verify again.
        try:
            page.wait_for_selector(RESULT_TABLE_SELECTOR, timeout=30000)
        except Exception:
            pass
        after = _range_text()
        if after == before:
            print(f"[scrape] next-click did not advance. range={before!r}")
            return False

    if after_next_wait_sec > 0:
        try:
            page.wait_for_timeout(int(after_next_wait_sec * 1000))
        except Exception:
            pass

    return True


def harvest_result_pages(
    page,
    fall_tables: Dict[str, pd.DataFrame],
    winter_tables: Dict[str, pd.DataFrame],
    room_set: set,
    throttle_sec: float,
    after_next_wait_sec: float,
) -> Tuple[int, int]:
    def _infer_col_indexes(table) -> Dict[str, int]:
        """Infer important column indexes from the result table header.

        The syllabus site occasionally adds/removes/reorders columns (e.g. an extra description column),
        so relying on fixed tds[5]/tds[6]/tds[7] can silently break.
        """

        def _cell_text(cell) -> str:
            return norm_text(cell.get_text(" ", strip=True)).lower()

        header_tr = table.find("tr")
        if not header_tr:
            return {}

        header_cells = header_tr.find_all(["th", "td"])
        if not header_cells:
            return {}

        headers = [_cell_text(c) for c in header_cells]

        def _find_idx(patterns: List[re.Pattern]) -> Optional[int]:
            for i, h in enumerate(headers):
                for pat in patterns:
                    if pat.search(h):
                        return i
            return None

        idx_year = _find_idx([re.compile(r"\byear\b"), re.compile(r"年度")])
        idx_code = _find_idx([re.compile(r"course\s*code"), re.compile(r"\bcode\b"), re.compile(r"科目\s*コード")])
        idx_name = _find_idx([
            re.compile(r"course\s*(title|name)"),
            re.compile(r"\btitle\b"),
            re.compile(r"科目\s*名"),
        ])
        idx_term = _find_idx([re.compile(r"\bterm\b"), re.compile(r"semester"), re.compile(r"学期"), re.compile(r"クォーター")])
        idx_day = _find_idx([
            re.compile(r"day\s*/\s*period"),
            re.compile(r"day\s*and\s*period"),
            re.compile(r"\bday\b"),
            re.compile(r"曜日"),
        ])
        idx_room = _find_idx([re.compile(r"class\s*room"), re.compile(r"\bclassroom\b"), re.compile(r"\broom\b"), re.compile(r"教室")])

        out: Dict[str, int] = {}
        for key, idx in [
            ("year", idx_year),
            ("code", idx_code),
            ("name", idx_name),
            ("term", idx_term),
            ("day", idx_day),
            ("room", idx_room),
        ]:
            if isinstance(idx, int):
                out[key] = idx
        return out

    total_rows = 0
    filled = 0
    page_no = 0

    while True:
        page_no += 1
        html = page.content()
        soup = BeautifulSoup(html, "lxml")

        # Print which page/range we're scraping (helps debugging pagination).
        range_text = ""
        try:
            sel = soup.select_one("div.c-selectall font")
            if sel:
                range_text = norm_text(sel.get_text(" ", strip=True))
        except Exception:
            range_text = ""
        if range_text:
            print(f"[scrape] page={page_no} range={range_text}")
        else:
            print(f"[scrape] page={page_no}")

        table = soup.find("table", class_=re.compile(r"ct-vh"))
        if not table:
            break

        col_idx = _infer_col_indexes(table)
        # Fallback to the original fixed layout if header inference fails.
        idx_code = col_idx.get("code", 1)
        idx_name = col_idx.get("name", 2)
        idx_term = col_idx.get("term", 5)
        idx_day = col_idx.get("day", 6)
        idx_room = col_idx.get("room", 7)

        rows = table.find_all("tr")
        for tr in rows[1:]:
            tds = tr.find_all("td")
            need = max(idx_code, idx_name, idx_term, idx_day, idx_room) + 1
            if len(tds) < need:
                continue

            course_code = norm_text(tds[idx_code].get_text(" ", strip=True))
            course_name = norm_text(tds[idx_name].get_text(" ", strip=True))
            term = norm_text(tds[idx_term].get_text(" ", strip=True))

            day_lines = split_td_lines(tds[idx_day])
            room_lines = split_td_lines(tds[idx_room])

            targets = targets_from_term(term)
            if not targets:
                continue

            pairs = pair_day_and_room(day_lines, room_lines)
            for day_str, room_str in pairs:
                dp = parse_day_periods(day_str)
                room = norm_room(room_str)
                if not dp or not room:
                    continue

                day, periods = dp
                if day not in ["mon", "tue", "wed", "thu", "fri"]:
                    continue
                if room not in room_set:
                    continue

                value = f"{course_code}:{course_name}"

                for period in periods:
                    if not (1 <= period <= 6):
                        continue

                    if "fall" in targets:
                        put_cell(fall_tables[day], period, room, value)
                        filled += 1
                    if "winter" in targets:
                        put_cell(winter_tables[day], period, room, value)
                        filled += 1

            total_rows += 1

        time.sleep(throttle_sec)

        if not go_to_next_page(page, current_page_no=page_no, after_next_wait_sec=after_next_wait_sec):
            break

    print(f"[scrape] reached_last_page={page_no}")
    return total_rows, filled


# ----------------------------
# メイン: シラバス巡回（Playwright）
# ----------------------------
def scrape_and_fill(
    year: int,
    base_dir: Path,
    headless: bool,
    throttle_sec: float,
    after_next_wait_sec: float,
) -> None:
    room_cap_path = base_dir / "data" / "room_capacity.csv"
    if not room_cap_path.exists():
        raise FileNotFoundError(f"missing: {room_cap_path}")

    room_df = pd.read_csv(room_cap_path, dtype=str).fillna("")
    room_set = set(room_df["classroom"].map(norm_room).tolist())

    fall_tables = load_week_csvs(base_dir, "period_room_fall")
    winter_tables = load_week_csvs(base_dir, "period_room_winter")

    with sync_playwright() as p:
        launch_args = ["--lang=en-US"]
        browser = p.chromium.launch(headless=headless, args=launch_args)
        context = None
        try:
            context = browser.new_context(
                locale="en-US",
                user_agent=DEFAULT_USER_AGENT,
                extra_http_headers={"Accept-Language": DEFAULT_ACCEPT_LANGUAGE},
            )
            page = context.new_page()
            page = open_fall_winter_listing(page)
            total_rows, filled = harvest_result_pages(
                page=page,
                fall_tables=fall_tables,
                winter_tables=winter_tables,
                room_set=room_set,
                throttle_sec=throttle_sec,
                after_next_wait_sec=after_next_wait_sec,
            )
        finally:
            if context is not None:
                context.close()
            browser.close()


    # 保存
    save_week_csvs(base_dir, "period_room_fall", fall_tables)
    save_week_csvs(base_dir, "period_room_winter", winter_tables)

    print(f"[done] scanned_rows={total_rows}, filled_cells={filled}")
    print(f"  updated: {base_dir/'data'/'period_room_fall'}/*.csv")
    print(f"  updated: {base_dir/'data'/'period_room_winter'}/*.csv")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2025)
    ap.add_argument("--base-dir", type=str, default=".")
    ap.add_argument("--headless", action="store_true", help="Run browser in headless mode")
    ap.add_argument("--throttle", type=float, default=0.2, help="sleep seconds between pages")
    ap.add_argument(
        "--after-next-wait",
        type=float,
        default=2.0,
        help="extra wait seconds after moving to the next results page",
    )
    args = ap.parse_args()

    base_dir = Path(args.base_dir).resolve()
    scrape_and_fill(
        year=args.year,
        base_dir=base_dir,
        headless=args.headless,
        throttle_sec=args.throttle,
        after_next_wait_sec=args.after_next_wait,
    )

if __name__ == "__main__":
    main()
