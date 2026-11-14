#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VOX Cinemas Egypt — Early Booking & Seat Availability Watcher (v2)
Target Movie: Avengers: Doomsday (Standard & MAX / Infinity Vision)
Target Cinema: City Centre Almaza Only
Includes:
- Proper relative URL resolution (?d=YYYYMMDD) across all early-booking dates
- Guest checkout step navigation (/booking/... -> Continue As Guest -> /seats#guest)
- Anti-bot headed browser support (via Xvfb on GitHub Actions)
- Diagnostic status output (last_run_status.json) for easy verification
"""

import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup, Tag

try:
    from playwright.sync_api import sync_playwright, Page, Response
except ImportError:
    sync_playwright = None
    Page = Any
    Response = Any

# ==============================================================================
# 1. USER CONFIGURATION
# ==============================================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "PUT_YOUR_BOT_TOKEN_HERE")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "PUT_YOUR_CHAT_ID_HERE")

# Movie URLs on VOX Cinemas Egypt
MOVIE_URLS = [
    "https://egy.voxcinemas.com/movies/avengers-doomsday",
    "https://egy.voxcinemas.com/movies/avengers-doomsday-infinity-vision",
]

# Strictly monitor City Centre Almaza only
TARGET_CINEMAS = [
    "City Centre Almaza",
]

# Allowed experiences (excludes GOLD, KIDS, 4DX)
TARGET_EXPERIENCES = [
    "MAX",
    "STANDARD",
    "IMAX",
]

# Early-booking dates to scan automatically (plus any ?d= links discovered on the page)
DEFAULT_EARLY_BOOKING_DATES = [
    "20261216",
    "20261217",
    "20261218",
    "20261219",
    "20261220",
    "20261221",
    "20261222",
    "20261223",
]

# Optional filters (leave empty [] to monitor all dates and times)
TARGET_DATES: List[str] = []
TARGET_TIMES: List[str] = []

# ==============================================================================
# 2. IDEAL SEAT SELECTION RULES (MIDDLE & BACK ROWS ONLY)
# ==============================================================================

EXCLUDED_FRONT_ROWS = {"A", "B", "C", "D"}
PREFERRED_ROWS = {"E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O", "P"}
SWEET_SPOT_ROWS = {"F", "G", "H", "I", "J", "K", "L"}

CENTER_SEAT_MIN_RATIO = 0.20
CENTER_SEAT_MAX_RATIO = 0.80

MIN_SEATS_NEEDED = int(os.getenv("MIN_SEATS_NEEDED", "1"))

STATE_FILE = "notified_seats.json"
STATUS_FILE = "last_run_status.json"
CAIRO_TZ = timezone(timedelta(hours=2))


# ==============================================================================
# 3. STATE MANAGEMENT & TELEGRAM API
# ==============================================================================

def load_notified_state() -> Set[str]:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return set(data.get("notified_keys", []))
        except Exception as exc:
            print(f"[WARN] Could not read {STATE_FILE}: {exc}")
    return set()


def save_notified_state(notified_keys: Set[str]) -> None:
    trimmed = sorted(list(notified_keys))[-5000:]
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "updated_at": datetime.now(CAIRO_TZ).isoformat(),
                    "notified_keys": trimmed,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
    except Exception as exc:
        print(f"[WARN] Could not save {STATE_FILE}: {exc}")


def save_run_status(status_data: Dict[str, Any]) -> None:
    try:
        with open(STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump(status_data, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        print(f"[WARN] Could not save {STATUS_FILE}: {exc}")


def send_telegram_message(text: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or "PUT_YOUR" in TELEGRAM_BOT_TOKEN:
        print("[ERROR] TELEGRAM_BOT_TOKEN is not configured.")
        return False
    if not TELEGRAM_CHAT_ID or "PUT_YOUR" in TELEGRAM_CHAT_ID:
        print("[ERROR] TELEGRAM_CHAT_ID is not configured.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        if resp.status_code == 200:
            print("[OK] Telegram notification sent successfully.")
            return True
        print(f"[ERROR] Telegram API failed ({resp.status_code}): {resp.text}")
    except Exception as exc:
        print(f"[ERROR] Exception sending Telegram message: {exc}")
    return False


# ==============================================================================
# 4. SEAT PARSING & QUALITY FILTERING
# ==============================================================================

def parse_seat_code(raw_code: str) -> Tuple[str, Optional[int]]:
    cleaned = raw_code.strip().upper()
    match = re.search(r"\b([A-Z]{1,2})\s*[-:]?\s*(\d{1,2})\b", cleaned)
    if match:
        return match.group(1), int(match.group(2))
    return cleaned, None


def evaluate_seat_quality(
    row_label: str,
    seat_num: Optional[int],
    max_seats_in_row: int = 18,
) -> Tuple[bool, str]:
    row_clean = row_label.strip().upper()
    if row_clean in EXCLUDED_FRONT_ROWS:
        return False, "صف أمامي (مستبعد)"

    is_center_horizontal = True
    if seat_num is not None and max_seats_in_row >= 8:
        min_center = max(2, int(round(max_seats_in_row * CENTER_SEAT_MIN_RATIO)))
        max_center = min(
            max_seats_in_row - 1,
            int(round(max_seats_in_row * CENTER_SEAT_MAX_RATIO)),
        )
        is_center_horizontal = min_center <= seat_num <= max_center

    if row_clean in SWEET_SPOT_ROWS:
        if is_center_horizontal:
            return True, "🌟 مثالي (منتصف القاعة - Sweet Spot)"
        return True, "✅ جيد جداً (صف متوسط - قريب من المنتصف)"

    if row_clean in PREFERRED_ROWS or (len(row_clean) == 1 and row_clean > "D"):
        if is_center_horizontal:
            return True, "🌟 ممتاز (الصفوف الخلفية - منتصف)"
        return True, "✅ جيد (الصفوف الخلفية)"

    return False, "غير مطابق لمعايير الصفوف"


def filter_and_rank_seats(available_seats: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    row_max_seat: Dict[str, int] = {}
    for s in available_seats:
        r_label = str(s.get("row", "")).upper()
        s_num = s.get("number")
        if r_label and isinstance(s_num, int):
            row_max_seat[r_label] = max(row_max_seat.get(r_label, 16), s_num)

    good_seats: List[Dict[str, Any]] = []
    seen_codes: Set[str] = set()
    for seat in available_seats:
        row_label = str(seat.get("row", "")).upper()
        seat_num = seat.get("number")
        code = str(seat.get("code", f"{row_label}{seat_num or ''}")).upper()
        if code in seen_codes:
            continue
        seen_codes.add(code)

        max_in_row = row_max_seat.get(row_label, 18)
        is_good, quality_desc = evaluate_seat_quality(row_label, seat_num, max_in_row)
        if is_good:
            good_seats.append(
                {
                    "row": row_label,
                    "number": seat_num,
                    "code": code,
                    "quality": quality_desc,
                }
            )

    good_seats.sort(
        key=lambda x: (
            0 if "🌟" in x["quality"] else 1,
            x["row"],
            x["number"] if isinstance(x["number"], int) else 99,
        )
    )
    return good_seats


# ==============================================================================
# 5. HTML SHOWTIME PARSER (RESILIENT TO VOX DOM STRUCTURE)
# ==============================================================================

def detect_experience_for_link(link_tag: Tag) -> str:
    """Walk up parents from a booking link to find the experience name (MAX, Standard, GOLD, IMAX)."""
    curr: Optional[Tag] = link_tag
    for _ in range(5):
        if curr is None:
            break
        # Check strong/span/h4/img inside this parent block, excluding time strings
        for label_el in curr.find_all(["strong", "span", "h4", "div", "img"], recursive=False):
            raw = (label_el.get("alt") or label_el.get_text(" ", strip=True) or "").upper()
            if not raw or re.search(r"^\d{1,2}:\d{2}", raw):
                continue
            if "IMAX" in raw:
                return "IMAX"
            if "MAX" in raw:
                return "MAX"
            if "GOLD" in raw:
                return "GOLD"
            if "4DX" in raw:
                return "4DX"
            if "KIDS" in raw:
                return "KIDS"
            if "STANDARD" in raw:
                return "Standard"
        curr = curr.parent

    # Fallback: check full text of ancestor li
    li_parent = link_tag.find_parent("li")
    if li_parent:
        outer_li = li_parent.find_parent("li") or li_parent
        text_upper = outer_li.get_text(" ", strip=True).upper()
        if "IMAX" in text_upper:
            return "IMAX"
        if "MAX" in text_upper:
            return "MAX"
        if "GOLD" in text_upper:
            return "GOLD"
        if "4DX" in text_upper:
            return "4DX"
        if "KIDS" in text_upper:
            return "KIDS"
    return "Standard"


def detect_cinema_for_link(link_tag: Tag) -> str:
    """Find the closest preceding cinema heading (e.g., City Centre Almaza, Mall of Egypt)."""
    for prev in link_tag.find_all_previous(["h3", "h2"]):
        txt = " ".join(prev.get_text().split())
        if any(
            branch in txt.lower()
            for branch in ["city centre almaza", "mall of egypt", "city centre alexandria"]
        ):
            return txt
    return ""


def parse_showtimes_from_html(
    html: str,
    page_url: str,
    fallback_date_code: str = "",
) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    sessions: List[Dict[str, Any]] = []

    # Determine active date label and YYYYMMDD code
    active_date_label = fallback_date_code or "غير محدد"
    active_date_code = fallback_date_code

    m_url_date = re.search(r"d=(\d{8})", page_url)
    if m_url_date:
        active_date_code = m_url_date.group(1)

    for a_date in soup.select("a[href*='?d=']"):
        href = a_date.get("href", "")
        cls = " ".join(a_date.get("class", [])).lower()
        parent_cls = " ".join(a_date.parent.get("class", [])).lower() if a_date.parent else ""
        if (
            (active_date_code and active_date_code in href)
            or "active" in cls
            or "selected" in cls
            or "active" in parent_cls
        ):
            active_date_label = " ".join(a_date.get_text().split())
            m_d = re.search(r"d=(\d{8})", href)
            if m_d and not active_date_code:
                active_date_code = m_d.group(1)
            break

    # Find every booking link on the page and match its branch + experience
    for link in soup.find_all("a", href=re.compile(r"/booking/", re.I)):
        show_time = " ".join(link.get_text().split())
        if not re.search(r"\d{1,2}:\d{2}", show_time):
            continue

        cinema_name = detect_cinema_for_link(link)
        if not any(tc.lower() in cinema_name.lower() for tc in TARGET_CINEMAS):
            continue

        detected_exp = detect_experience_for_link(link)
        if detected_exp.upper() not in [e.upper() for e in TARGET_EXPERIENCES]:
            continue

        if TARGET_TIMES and not any(t.lower() in show_time.lower() for t in TARGET_TIMES):
            continue

        href = link.get("href", "")
        booking_url = urljoin("https://egy.voxcinemas.com", href)
        session_id = link.get("data-id") or href.rstrip("/").split("/")[-1]

        sessions.append(
            {
                "cinema": cinema_name,
                "experience": detected_exp,
                "is_max": detected_exp.upper() == "MAX",
                "date_label": active_date_label,
                "date_code": active_date_code,
                "time": show_time,
                "booking_url": booking_url,
                "session_id": session_id,
                "movie_url": page_url,
            }
        )

    unique_sessions = {}
    for s in sessions:
        key = f"{s['cinema']}_{s['experience']}_{s['date_code']}_{s['time']}_{s['booking_url']}"
        unique_sessions[key] = s
    return list(unique_sessions.values())


# ==============================================================================
# 6. SEAT MAP EXTRACTION (/booking/... -> Continue As Guest -> /seats#guest)
# ==============================================================================

def extract_seats_from_booking_page(page: Page, booking_url: str) -> List[Dict[str, Any]]:
    captured_api_seats: List[Dict[str, Any]] = []

    def handle_response(response: Response) -> None:
        try:
            url_lower = response.url.lower()
            if any(k in url_lower for k in ["seat", "layout", "plan", "ocapi", "booking"]):
                if "application/json" in response.headers.get("content-type", "").lower():
                    data = response.json()
                    parse_json_seats(data, captured_api_seats)
        except Exception:
            pass

    page.on("response", handle_response)
    try:
        page.goto(booking_url, wait_until="domcontentloaded", timeout=35000)
        page.wait_for_timeout(2500)

        # Step 1: If redirected to /guest ("Continue As Guest"), navigate to /seats#guest or click button
        if "/guest" in page.url:
            seats_url = page.url.split("?")[0].rstrip("/")
            if seats_url.endswith("/guest"):
                seats_url = seats_url[:-6] + "/seats#guest"
                page.goto(seats_url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(3000)
            else:
                guest_btn = page.query_selector(
                    "a:has-text('Continue As Guest'), button:has-text('Continue As Guest'), a[href*='seats']"
                )
                if guest_btn:
                    guest_btn.click(timeout=5000)
                    page.wait_for_timeout(3000)

        if captured_api_seats:
            return captured_api_seats

        # Step 2: Extract seats from the DOM / SVG seat plan
        dom_seats = page.evaluate(
            """() => {
                const results = [];
                const seatSelectors = [
                    '[data-seat]',
                    '[data-row]',
                    'li.seat',
                    'span.seat',
                    'a.seat',
                    'button.seat',
                    'div.seat',
                    'g.seat',
                    'svg [data-row]',
                    'svg [id*="seat" i]',
                    '.seat-plan [class*="seat" i]'
                ];
                const elements = document.querySelectorAll(seatSelectors.join(','));
                elements.forEach(el => {
                    const cls = (el.className && typeof el.className === 'string')
                        ? el.className.toLowerCase()
                        : (el.getAttribute('class') || '').toLowerCase();
                    const statusAttr = (
                        (el.getAttribute('data-status') || '') + ' ' +
                        (el.getAttribute('aria-disabled') || '') + ' ' +
                        (el.getAttribute('title') || '')
                    ).toLowerCase();

                    const isTaken = [
                        'sold', 'occupied', 'unavailable', 'taken',
                        'reserved', 'blocked', 'broken', 'wheelchair', 'companion'
                    ].some(word => cls.includes(word) || statusAttr.includes(word));
                    if (isTaken) return;

                    const row = el.getAttribute('data-row')
                        || el.getAttribute('data-row-name')
                        || (el.closest('[data-row]') ? el.closest('[data-row]').getAttribute('data-row') : '')
                        || (el.closest('tr') ? (el.closest('tr').getAttribute('data-row') || '') : '')
                        || '';
                    const num = el.getAttribute('data-seat')
                        || el.getAttribute('data-number')
                        || el.getAttribute('data-col')
                        || (el.innerText || '').trim();
                    const label = el.getAttribute('aria-label')
                        || el.getAttribute('title')
                        || el.getAttribute('data-id')
                        || `${row}${num}`;

                    if (row || label) {
                        results.push({ row: row, num: num, label: label, cls: cls });
                    }
                });
                return results;
            }"""
        )

        parsed_dom_seats: List[Dict[str, Any]] = []
        for item in dom_seats:
            raw_label = item.get("label") or f"{item.get('row', '')}{item.get('num', '')}"
            row_label, seat_num = parse_seat_code(raw_label)
            if (not row_label or len(row_label) > 2) and item.get("row"):
                row_label = str(item["row"]).strip().upper()
            if seat_num is None and str(item.get("num", "")).isdigit():
                seat_num = int(item["num"])

            if row_label and 1 <= len(row_label) <= 2 and row_label.isalpha():
                code = f"{row_label}{seat_num}" if seat_num is not None else raw_label
                parsed_dom_seats.append(
                    {
                        "row": row_label,
                        "number": seat_num,
                        "code": code,
                    }
                )

        return parsed_dom_seats

    except Exception as exc:
        print(f"[WARN] Could not inspect seat map {booking_url}: {exc}")
        return []
    finally:
        try:
            page.remove_listener("response", handle_response)
        except Exception:
            pass


def parse_json_seats(data: object, out_list: List[Dict[str, Any]]) -> None:
    if isinstance(data, dict):
        status = str(data.get("status", data.get("seatStatus", data.get("isAvailable", "")))).lower()
        row = data.get("row", data.get("rowName", data.get("physicalRowId", "")))
        number = data.get("number", data.get("seatNumber", data.get("column", "")))

        if row and number and status in ("available", "0", "true", "open", "free"):
            try:
                s_num = int(re.sub(r"\D", "", str(number)))
            except ValueError:
                s_num = None
            r_str = str(row).strip().upper()
            out_list.append(
                {
                    "row": r_str,
                    "number": s_num,
                    "code": f"{r_str}{s_num if s_num is not None else number}",
                }
            )
        for val in data.values():
            parse_json_seats(val, out_list)
    elif isinstance(data, list):
        for item in data:
            parse_json_seats(item, out_list)


# ==============================================================================
# 7. MAIN MONITORING LOOP
# ==============================================================================

def format_day_and_date(date_label: str, date_code: str) -> str:
    arabic_days = {
        "Mon": "الإثنين",
        "Tue": "الثلاثاء",
        "Wed": "الأربعاء",
        "Thu": "الخميس",
        "Fri": "الجمعة",
        "Sat": "السبت",
        "Sun": "الأحد",
    }
    formatted = date_label
    for en_day, ar_day in arabic_days.items():
        if en_day.lower() in date_label.lower():
            formatted = f"{ar_day} ({date_label})"
            break
    if date_code and len(date_code) == 8:
        yyyy, mm, dd = date_code[:4], date_code[4:6], date_code[6:]
        return f"{formatted} — {yyyy}-{mm}-{dd}"
    return formatted


def run_watcher() -> None:
    if sync_playwright is None:
        raise RuntimeError("Playwright is not installed.")

    now_cairo = datetime.now(CAIRO_TZ).strftime("%Y-%m-%d %I:%M:%S %p")
    print(f"=== Starting VOX Cinemas City Centre Almaza Watcher ({now_cairo} Cairo Time) ===")

    notified_keys = load_notified_state()
    new_notifications_sent = 0
    diagnostics: Dict[str, Any] = {
        "last_run_cairo": now_cairo,
        "pages_checked": [],
        "sessions_discovered_count": 0,
        "sessions_discovered": [],
        "new_notifications_sent": 0,
    }

    use_headed = os.getenv("DISPLAY") is not None

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=not use_headed,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--window-size=1366,900",
            ],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 900},
            locale="en-US",
        )
        # Mask navigator.webdriver to avoid Cloudflare headless blocks
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        page = context.new_page()

        all_discovered_sessions: List[Dict[str, Any]] = []

        for movie_url in MOVIE_URLS:
            print(f"[INFO] Checking base movie URL: {movie_url}")
            try:
                resp = page.goto(movie_url, wait_until="domcontentloaded", timeout=35000)
                status_code = resp.status if resp else 0
                page.wait_for_timeout(3000)
                page_title = page.title()
                print(f"[INFO] Loaded {movie_url} | Status: {status_code} | Title: {page_title}")

                diagnostics["pages_checked"].append(
                    {"url": movie_url, "status": status_code, "title": page_title}
                )

                if status_code >= 400 and "just a moment" not in page_title.lower():
                    continue

                # If Cloudflare challenge appears, wait a few seconds for it to resolve
                if "just a moment" in page_title.lower():
                    page.wait_for_timeout(6000)

                html = page.content()
                soup = BeautifulSoup(html, "html.parser")

                # Collect date codes from page + default early booking dates
                dates_to_check: List[str] = list(DEFAULT_EARLY_BOOKING_DATES)
                for a_el in soup.select("a[href*='?d=']"):
                    href = a_el.get("href", "")
                    m_d = re.search(r"d=(\d{8})", href)
                    if m_d and m_d.group(1) not in dates_to_check:
                        dates_to_check.append(m_d.group(1))

                if TARGET_DATES:
                    dates_to_check = [d for d in dates_to_check if d in TARGET_DATES]

                # Also parse default landing page
                all_discovered_sessions.extend(parse_showtimes_from_html(html, movie_url))

                # Visit each date URL explicitly using proper query string resolution
                for d_code in dates_to_check:
                    d_url = f"{movie_url}?d={d_code}"
                    try:
                        d_resp = page.goto(d_url, wait_until="domcontentloaded", timeout=30000)
                        page.wait_for_timeout(2000)
                        d_sessions = parse_showtimes_from_html(
                            page.content(), d_url, fallback_date_code=d_code
                        )
                        print(
                            f"[INFO] Date {d_code}: found {len(d_sessions)} matching session(s) at City Centre Almaza"
                        )
                        all_discovered_sessions.extend(d_sessions)
                    except Exception as d_exc:
                        print(f"[WARN] Failed loading date {d_code}: {d_exc}")

            except Exception as exc:
                print(f"[WARN] Error checking {movie_url}: {exc}")
                diagnostics["pages_checked"].append({"url": movie_url, "error": str(exc)})

        # Deduplicate discovered sessions across all dates
        unique_map = {}
        for s in all_discovered_sessions:
            k = f"{s['cinema']}_{s['experience']}_{s['date_code']}_{s['time']}_{s['session_id']}"
            unique_map[k] = s
        deduped_sessions = list(unique_map.values())

        diagnostics["sessions_discovered_count"] = len(deduped_sessions)
        diagnostics["sessions_discovered"] = [
            {
                "cinema": s["cinema"],
                "experience": s["experience"],
                "date": s["date_label"],
                "date_code": s["date_code"],
                "time": s["time"],
                "booking_url": s["booking_url"],
            }
            for s in deduped_sessions
        ]
        print(f"[INFO] Total unique City Centre Almaza sessions found: {len(deduped_sessions)}")

        for session in deduped_sessions:
            cinema = session["cinema"]
            experience = session["experience"]
            is_max = session["is_max"]
            date_str = format_day_and_date(session["date_label"], session["date_code"])
            show_time = session["time"]
            booking_url = session["booking_url"]

            print(f"[CHECK] {cinema} | {experience} | {date_str} | {show_time}")
            raw_seats = extract_seats_from_booking_page(page, booking_url)
            good_seats = filter_and_rank_seats(raw_seats)
            print(
                f"   -> Raw available seats: {len(raw_seats)} | Ideal middle/back seats: {len(good_seats)}"
            )

            unnotified_good_seats = []
            for seat in good_seats:
                seat_key = f"{session['session_id']}_{session['date_code']}_{seat['code']}"
                if seat_key not in notified_keys:
                    unnotified_good_seats.append((seat_key, seat))

            if len(unnotified_good_seats) >= MIN_SEATS_NEEDED:
                rows_summary: Dict[str, List[str]] = {}
                for seat_key, seat in unnotified_good_seats[:15]:
                    rows_summary.setdefault(seat["row"], []).append(
                        f"<code>{seat['code']}</code> ({seat['quality']})"
                    )

                rows_text_lines = [
                    f"• <b>الصف {r}:</b> " + " ، ".join(codes)
                    for r, codes in rows_summary.items()
                ]
                rows_block = "\n".join(rows_text_lines)

                max_badge = "✅ <b>نعم (قاعة MAX 🔊)</b>" if is_max else f"لا (<b>{experience}</b>)"

                msg = (
                    f"🚨 <b>توفرت كراسي مميزة لفيلم Avengers: Doomsday!</b>\n\n"
                    f"🎬 <b>الفيلم:</b> Avengers: Doomsday\n"
                    f"📍 <b>السينما:</b> {cinema}\n"
                    f"🖥 <b>هل الحفلة MAX؟</b> {max_badge}\n"
                    f"📅 <b>اليوم والتاريخ:</b> {date_str}\n"
                    f"⏰ <b>موعد الحفلة:</b> {show_time}\n\n"
                    f"💺 <b>الكراسي المتاحة (منتصف/خلف القاعة):</b>\n"
                    f"{rows_block}\n\n"
                    f"⚡ <a href=\"{booking_url}\">اضغط هنا للدخول لصفحة الحفلة والحجز فوراً</a>"
                )

                if send_telegram_message(msg):
                    for seat_key, _ in unnotified_good_seats:
                        notified_keys.add(seat_key)
                    new_notifications_sent += 1

            elif not raw_seats:
                session_open_key = (
                    f"SESSION_OPEN_{cinema}_{experience}_{session['date_code']}_{show_time}"
                )
                if session_open_key not in notified_keys:
                    max_badge = (
                        "✅ <b>نعم (قاعة MAX 🔊)</b>" if is_max else f"لا (<b>{experience}</b>)"
                    )
                    msg = (
                        f"🎟 <b>حفلة متاحة للحجز المبكر — Avengers: Doomsday!</b>\n\n"
                        f"📍 <b>السينما:</b> {cinema}\n"
                        f"🖥 <b>هل الحفلة MAX؟</b> {max_badge}\n"
                        f"📅 <b>اليوم والتاريخ:</b> {date_str}\n"
                        f"⏰ <b>موعد الحفلة:</b> {show_time}\n\n"
                        f"⚡ <a href=\"{booking_url}\">افتح مخطط الكراسي واحجز مباشرة من هنا</a>"
                    )
                    if send_telegram_message(msg):
                        notified_keys.add(session_open_key)
                        new_notifications_sent += 1

        browser.close()

    diagnostics["new_notifications_sent"] = new_notifications_sent
    save_notified_state(notified_keys)
    save_run_status(diagnostics)
    print(f"=== Scan Complete. New notifications sent: {new_notifications_sent} ===")


if __name__ == "__main__":
    if "--test" in sys.argv:
        send_telegram_message(
            "✅ <b>تجربة ناجحة!</b>\n"
            "بوت مراقبة حجوزات <b>VOX Cinemas — City Centre Almaza (Avengers: Doomsday)</b> متصل وجاهز للعمل."
        )
    else:
        run_watcher()
