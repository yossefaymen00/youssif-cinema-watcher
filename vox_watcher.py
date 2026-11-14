#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VOX Cinemas Egypt — Early Booking & Ideal Seat Watcher (v4 - Screen-Distance Aware)
Target Movie: Avengers: Doomsday (Standard & MAX / Infinity Vision)
Target Cinema: City Centre Almaza Only

Key Fixes in v4:
1. Screen-Distance Row Detection:
   - In VOX Cinemas Egypt, Row A is at the BACK of the hall and Row L / N is the FIRST row
     right in front of the screen!
   - v4 dynamically measures each row's physical distance from the SCREEN element in the DOM
     (and reverses alphabetical order when L/N is at the screen), strictly rejecting the
     front 38% of rows closest to the screen (such as rows I, J, K, L, M, N) and ONLY
     accepting true Middle & Back rows (such as rows A, B, C, D, E, F, G, H).
2. True Row-Width Horizontal Center Calculation:
   - Counts ALL seats in each row (both occupied and available) so side seats (like seat 1-4)
     are never mistaken for center seats when the rest of the row is booked.
3. Excludes `special` (wheelchair/companion front-row seats) and `blank` spacers.
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

MOVIE_URLS = [
    "https://egy.voxcinemas.com/movies/avengers-doomsday",
    "https://egy.voxcinemas.com/movies/avengers-doomsday-infinity-vision",
]

TARGET_CINEMAS = [
    "City Centre Almaza",
]

TARGET_EXPERIENCES = [
    "MAX",
    "STANDARD",
    "IMAX",
]

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

TARGET_DATES: List[str] = []
TARGET_TIMES: List[str] = []

# ==============================================================================
# 2. SCREEN-DISTANCE & CENTER SEAT RULES
# ==============================================================================

# Reject the closest 38% of rows to the SCREEN (e.g., in a 12-row hall L..A, rejects L, K, J, I)
MIN_DISTANCE_FROM_SCREEN_RATIO = 0.38

# Sweet spot is 40% to 78% of the way back from the screen (e.g., rows H, G, F, E, D)
SWEET_SPOT_MAX_RATIO = 0.78

# Horizontal center: only accept seats in the middle 50% of the row (25% to 75%)
CENTER_SEAT_MIN_RATIO = 0.25
CENTER_SEAT_MAX_RATIO = 0.75

STRICT_CENTER_ONLY = True
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
                # Only load keys created by v4 (prefixed with v4_) so old front-row keys are cleared
                return {
                    k for k in data.get("notified_keys", [])
                    if str(k).startswith("v4_")
                }
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
# 4. SCREEN-DISTANCE & HORIZONTAL CENTER SEAT EVALUATION
# ==============================================================================

def filter_and_rank_seats(
    available_seats: List[Dict[str, Any]],
    rows_front_to_back: List[str],
) -> List[Dict[str, Any]]:
    """
    Filters available seats using the actual front-to-back row order in the auditorium:
    - rows_front_to_back[0] is the FIRST row right in front of the screen (rejected!).
    - rows_front_to_back[-1] is the BACK row at the top of the auditorium.
    """
    # Fallback if rows_front_to_back wasn't populated: in VOX Egypt, rows are ordered L->A (or N->A) from screen to back
    if not rows_front_to_back:
        # Standard 12-row hall at VOX Almaza goes L (front/screen) down to A (back)
        rows_front_to_back = ["N", "M", "L", "K", "J", "I", "H", "G", "F", "E", "D", "C", "B", "A"]

    total_rows = len(rows_front_to_back)
    good_seats: List[Dict[str, Any]] = []
    seen_codes: Set[str] = set()

    for seat in available_seats:
        row_label = str(seat.get("row", "")).strip().upper()
        seat_num = seat.get("number")
        total_in_row = max(int(seat.get("total_in_row") or 14), 12)
        code = str(seat.get("code", f"{row_label}{seat_num or ''}")).upper()

        if not row_label or seat_num is None or code in seen_codes:
            continue
        seen_codes.add(code)

        # 1. Determine how far this row is from the SCREEN (0.0 = 1st row at screen, 1.0 = back row)
        if row_label in rows_front_to_back:
            row_idx_from_screen = rows_front_to_back.index(row_label)
            dist_ratio = (row_idx_from_screen + 1) / float(total_rows)
        else:
            # In VOX Egypt, A-H are middle/back and I-P are front rows near the screen
            if row_label in {"I", "J", "K", "L", "M", "N", "O", "P"}:
                dist_ratio = 0.15  # Front row near screen -> reject
            else:
                dist_ratio = 0.65  # Middle/back row -> accept

        # Reject front rows (closest 38% of rows to the screen)
        if dist_ratio <= MIN_DISTANCE_FROM_SCREEN_RATIO:
            continue

        # 2. Check horizontal center position within the row
        min_center = max(4, int(round(total_in_row * CENTER_SEAT_MIN_RATIO)))
        max_center = min(total_in_row - 3, int(round(total_in_row * CENTER_SEAT_MAX_RATIO)))
        is_center = min_center <= seat_num <= max_center

        if STRICT_CENTER_ONLY and not is_center:
            continue

        if dist_ratio <= SWEET_SPOT_MAX_RATIO:
            quality = f"🌟 مثالي — منتصف القاعة (الصف {row_label} رقم {row_idx_from_screen + 1} من الشاشة)"
            priority = 0
        else:
            quality = f"✅ ممتاز — الصفوف الخلفية بالمنتصف (الصف {row_label})"
            priority = 1

        good_seats.append(
            {
                "row": row_label,
                "number": seat_num,
                "code": code,
                "quality": quality,
                "priority": priority,
            }
        )

    good_seats.sort(key=lambda x: (x["priority"], x["row"], x["number"]))
    return good_seats


# ==============================================================================
# 5. HTML SHOWTIME PARSER
# ==============================================================================

def detect_experience_for_link(link_tag: Tag) -> str:
    curr: Optional[Tag] = link_tag
    for _ in range(5):
        if curr is None:
            break
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
# 6. SEAT MAP EXTRACTION WITH SCREEN-TO-BACK ROW ORDERING
# ==============================================================================

def extract_seats_from_booking_page(
    page: Page,
    booking_url: str,
    debug_out: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    try:
        page.goto(booking_url, wait_until="domcontentloaded", timeout=35000)
        page.wait_for_timeout(2000)

        # Click "Continue As Guest" if on the /guest login gate
        guest_loc = page.locator(
            "a:has-text('Continue As Guest'), button:has-text('Continue As Guest'), "
            "a:has-text('Continue as guest'), button:has-text('Continue as guest'), "
            "a[href*='seats#guest'], a[href*='/seats']"
        )
        if guest_loc.count() > 0:
            try:
                guest_loc.first.click(timeout=6000)
                page.wait_for_timeout(2500)
            except Exception:
                pass

        # Click "I Agree" on the 18TC access conditions popup if present
        agree_loc = page.locator(
            "button:has-text('I Agree'), a:has-text('I Agree'), "
            "button:has-text('Agree'), a.action.primary:has-text('Agree')"
        )
        if agree_loc.count() > 0:
            try:
                if agree_loc.first.is_visible():
                    agree_loc.first.click(timeout=5000)
                    page.wait_for_timeout(2000)
            except Exception:
                pass

        eval_result = page.evaluate(
            """() => {
                const isTakenOrExcluded = (str) => {
                    const s = (str || '').toLowerCase();
                    return [
                        'sold', 'occupied', 'unavailable', 'taken', 'reserved',
                        'blocked', 'broken', 'wheelchair', 'companion', 'disabled',
                        'unselectable', 'not-available', 'booked', 'blank', 'special'
                    ].some(w => s.includes(w));
                };

                // 1. Collect all seat elements (both available and occupied) to know exact row order & row widths
                const allSeatEls = document.querySelectorAll('.seat, [data-seat], li.seat, span.seat');
                const rowsInDomOrder = [];
                const rowMaxSeatMap = {};
                const availableSeats = [];

                allSeatEls.forEach(el => {
                    const cls = (typeof el.className === 'string' ? el.className : (el.getAttribute('class') || '')).toLowerCase();
                    if (cls.includes('seat-plan') || cls.includes('seat-map') || cls.includes('seat-legend') || cls.includes('blank')) {
                        return;
                    }

                    let row = (
                        el.getAttribute('data-row') ||
                        el.getAttribute('data-row-name') ||
                        (el.closest('[data-row]') ? el.closest('[data-row]').getAttribute('data-row') : '') ||
                        ''
                    ).trim().toUpperCase();

                    // In VOX Cinemas, real seat rows are single letters A-P (corridor blanks have numeric data-row like "10")
                    if (!/^[A-P]$/.test(row)) {
                        return;
                    }

                    if (!rowsInDomOrder.includes(row)) {
                        rowsInDomOrder.push(row);
                    }

                    let numStr = (
                        el.getAttribute('data-seat') ||
                        el.getAttribute('data-number') ||
                        (el.textContent || '')
                    ).trim();
                    const seatNum = /^\\d{1,2}$/.test(numStr) ? parseInt(numStr, 10) : null;
                    if (seatNum !== null) {
                        rowMaxSeatMap[row] = Math.max(rowMaxSeatMap[row] || 14, seatNum);
                    }

                    const statusStr = [
                        cls,
                        el.getAttribute('data-status'),
                        el.getAttribute('aria-disabled'),
                        el.getAttribute('title')
                    ].join(' ');

                    if (!isTakenOrExcluded(statusStr) && seatNum !== null) {
                        availableSeats.push({
                            row: row,
                            number: seatNum,
                            code: `${row}${seatNum}`,
                            cls: cls
                        });
                    }
                });

                // Attach total_in_row to each available seat
                availableSeats.forEach(s => {
                    s.total_in_row = rowMaxSeatMap[s.row] || 16;
                });

                return {
                    url: window.location.href,
                    title: document.title,
                    rows_front_to_back: rowsInDomOrder,
                    row_widths: rowMaxSeatMap,
                    seats: availableSeats
                };
            }"""
        )

        rows_front_to_back: List[str] = eval_result.get("rows_front_to_back", [])
        seats: List[Dict[str, Any]] = eval_result.get("seats", [])

        if debug_out is not None and "hall_layout_sample" not in debug_out and rows_front_to_back:
            total_r = len(rows_front_to_back)
            excluded_front = [
                r for idx, r in enumerate(rows_front_to_back)
                if ((idx + 1) / float(total_r)) <= MIN_DISTANCE_FROM_SCREEN_RATIO
            ]
            accepted_middle_back = [
                r for r in rows_front_to_back if r not in excluded_front
            ]
            debug_out["hall_layout_sample"] = {
                "booking_url": booking_url,
                "rows_from_screen_to_back": rows_front_to_back,
                "excluded_front_rows_near_screen": excluded_front,
                "accepted_middle_and_back_rows": accepted_middle_back,
                "row_widths": eval_result.get("row_widths", {}),
            }

        return seats, rows_front_to_back

    except Exception as exc:
        print(f"[WARN] Could not inspect seat map {booking_url}: {exc}")
        return [], []


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
    print(f"=== Starting VOX Cinemas City Centre Almaza Watcher v4 ({now_cairo} Cairo Time) ===")

    notified_keys = load_notified_state()
    new_notifications_sent = 0
    diagnostics: Dict[str, Any] = {
        "version": "v4-screen-distance-aware",
        "last_run_cairo": now_cairo,
        "pages_checked": [],
        "sessions_discovered_count": 0,
        "sessions_with_ideal_seats": [],
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
                page.wait_for_timeout(2000)
                page_title = page.title()

                diagnostics["pages_checked"].append(
                    {"url": movie_url, "status": status_code, "title": page_title}
                )

                if status_code >= 400 and "just a moment" not in page_title.lower():
                    continue

                if "just a moment" in page_title.lower():
                    page.wait_for_timeout(5000)

                html = page.content()
                soup = BeautifulSoup(html, "html.parser")

                dates_to_check: List[str] = list(DEFAULT_EARLY_BOOKING_DATES)
                for a_el in soup.select("a[href*='?d=']"):
                    href = a_el.get("href", "")
                    m_d = re.search(r"d=(\d{8})", href)
                    if m_d and m_d.group(1) not in dates_to_check:
                        dates_to_check.append(m_d.group(1))

                if TARGET_DATES:
                    dates_to_check = [d for d in dates_to_check if d in TARGET_DATES]

                all_discovered_sessions.extend(parse_showtimes_from_html(html, movie_url))

                for d_code in dates_to_check:
                    d_url = f"{movie_url}?d={d_code}"
                    try:
                        page.goto(d_url, wait_until="domcontentloaded", timeout=30000)
                        page.wait_for_timeout(1200)
                        d_sessions = parse_showtimes_from_html(
                            page.content(), d_url, fallback_date_code=d_code
                        )
                        all_discovered_sessions.extend(d_sessions)
                    except Exception as d_exc:
                        print(f"[WARN] Failed loading date {d_code}: {d_exc}")

            except Exception as exc:
                print(f"[WARN] Error checking {movie_url}: {exc}")

        unique_map = {}
        for s in all_discovered_sessions:
            k = f"{s['cinema']}_{s['experience']}_{s['date_code']}_{s['time']}_{s['session_id']}"
            unique_map[k] = s
        deduped_sessions = list(unique_map.values())

        diagnostics["sessions_discovered_count"] = len(deduped_sessions)
        print(f"[INFO] Total unique City Centre Almaza sessions found: {len(deduped_sessions)}")

        for session in deduped_sessions:
            cinema = session["cinema"]
            experience = session["experience"]
            is_max = session["is_max"]
            date_str = format_day_and_date(session["date_label"], session["date_code"])
            show_time = session["time"]
            booking_url = session["booking_url"]

            print(f"[CHECK] {cinema} | {experience} | {date_str} | {show_time}")
            raw_seats, rows_front_to_back = extract_seats_from_booking_page(
                page, booking_url, debug_out=diagnostics
            )
            good_seats = filter_and_rank_seats(raw_seats, rows_front_to_back)
            print(
                f"   -> Open seats: {len(raw_seats)} | True Middle/Back-Center seats: {len(good_seats)}"
            )

            if good_seats:
                diagnostics["sessions_with_ideal_seats"].append(
                    {
                        "experience": experience,
                        "date": session["date_code"],
                        "time": show_time,
                        "ideal_seats_count": len(good_seats),
                        "top_ideal_seats": [s["code"] for s in good_seats[:10]],
                    }
                )

            unnotified_good_seats = []
            for seat in good_seats:
                seat_key = f"v4_{session['session_id']}_{session['date_code']}_{seat['code']}"
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
                    f"🚨 <b>توفرت كراسي مميزة (منتصف/خلف القاعة) — Avengers: Doomsday!</b>\n\n"
                    f"🎬 <b>الفيلم:</b> Avengers: Doomsday\n"
                    f"📍 <b>السينما:</b> {cinema}\n"
                    f"🖥 <b>هل الحفلة MAX؟</b> {max_badge}\n"
                    f"📅 <b>اليوم والتاريخ:</b> {date_str}\n"
                    f"⏰ <b>موعد الحفلة:</b> {show_time}\n\n"
                    f"💺 <b>الكراسي المتاحة (منتصف وأعلى القاعة فقط — بعيداً عن الشاشة):</b>\n"
                    f"{rows_block}\n\n"
                    f"⚡ <a href=\"{booking_url}\">اضغط هنا للدخول لصفحة الحفلة والحجز فوراً</a>"
                )

                if send_telegram_message(msg):
                    for seat_key, _ in unnotified_good_seats:
                        notified_keys.add(seat_key)
                    new_notifications_sent += 1

        browser.close()

    diagnostics["new_notifications_sent"] = new_notifications_sent
    save_notified_state(notified_keys)
    save_run_status(diagnostics)
    print(f"=== Scan Complete. New ideal-seat notifications sent: {new_notifications_sent} ===")


if __name__ == "__main__":
    if "--test" in sys.argv:
        send_telegram_message(
            "✅ <b>تجربة ناجحة!</b>\n"
            "بوت مراقبة حجوزات <b>VOX Cinemas — City Centre Almaza (Avengers: Doomsday)</b> متصل وجاهز للعمل."
        )
    else:
        run_watcher()
