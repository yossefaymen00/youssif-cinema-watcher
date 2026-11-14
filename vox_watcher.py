#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VOX Cinemas Egypt — Early Booking & Seat Availability Watcher
Target Movie: Avengers: Doomsday (Standard & MAX / Infinity Vision)
Target Cinema: City Centre Almaza Only
Runs automatically on GitHub Actions or locally via Playwright + Telegram Bot API.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Set, Tuple
import requests
from bs4 import BeautifulSoup

try:
    from playwright.sync_api import sync_playwright, Page, Response
except ImportError:
    sync_playwright = None
    Page = object
    Response = object

# ==============================================================================
# 1. إعدادات المستخدم الأساسية (يمكن تعديلها مباشرة أو عبر متغيرات البيئة)
# ==============================================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "PUT_YOUR_BOT_TOKEN_HERE")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "PUT_YOUR_CHAT_ID_HERE")

# روابط صفحات الفيلم على موقع VOX Cinemas Egypt (يشمل النسخة العادية ونسخة Infinity Vision)
MOVIE_URLS = [
    "https://egy.voxcinemas.com/movies/avengers-doomsday",
    "https://egy.voxcinemas.com/movies/avengers-doomsday-infinity-vision",
]

# الكلمات المفتاحية للبحث التلقائي في صفحة الأفلام في حال تغيير الرابط
MOVIE_KEYWORDS = ["avengers", "doomsday"]

# الفرع المطلوب: سيتي سنتر ألماظة فقط
TARGET_CINEMAS = [
    "City Centre Almaza",
]

# أنواع القاعات المطلوبة (يدعم MAX و Standard و IMAX)
TARGET_EXPERIENCES = [
    "MAX",
    "STANDARD",
    "IMAX",
]

# التواريخ المطلوبة بصيغة YYYYMMDD (اترك القائمة فارغة [] لمراقبة جميع الأيام المتاحة للحجز المبكر)
# مثال لتحديد أيام بعينها: ["20261216", "20261217", "20261218"]
TARGET_DATES: List[str] = []

# فلتر الأوقات (اتركه فارغاً [] لمراقبة جميع الحفلات في أي وقت)
# مثال: ["7:15pm", "10:30pm", "11:00pm"]
TARGET_TIMES: List[str] = []

# ==============================================================================
# 2. إعدادات اختيار الكراسي المثالية (تجنب الصفوف الأمامية والأطراف)
# ==============================================================================

# الصفوف الأمامية القريبة جداً من الشاشة (يتم استبعادها تماماً)
EXCLUDED_FRONT_ROWS = {"A", "B", "C", "D"}

# الصفوف المفضلة (من منتصف القاعة إلى آخرها: E إلى P)
PREFERRED_ROWS = {"E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O", "P"}

# صفوف الـ Sweet Spot (أفضل زاوية رؤية وصوت في منتصف القاعة والثلث الخلفي)
SWEET_SPOT_ROWS = {"F", "G", "H", "I", "J", "K", "L"}

# نسبة الكراسي الوسطى داخل الصف الواحد (لتجنب الكراسي المتطرفة على أقصى اليمين أو اليسار)
# 0.20 إلى 0.80 تعني استبعاد أول 20% وآخر 20% من كراسي الصف والتركيز على الـ 60% الوسطى
CENTER_SEAT_MIN_RATIO = 0.20
CENTER_SEAT_MAX_RATIO = 0.80

# الحد الأدنى لعدد الكراسي المطلوبة في الحفلة لإرسال إشعار
MIN_SEATS_NEEDED = int(os.getenv("MIN_SEATS_NEEDED", "1"))

# ملف حفظ الكراسي التي تم إرسال إشعار بها سابقاً لمنع تكرار الرسائل
STATE_FILE = "notified_seats.json"

# توقيت القاهرة (UTC+2)
CAIRO_TZ = timezone(timedelta(hours=2))


# ==============================================================================
# 3. دوال مساعدة لإدارة الحالة وإرسال إشعارات تيليجرام
# ==============================================================================

def load_notified_state() -> Set[str]:
    """تحميل سجل الكراسي والحفلات التي تم الإبلاغ عنها مسبقاً."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return set(data.get("notified_keys", []))
        except Exception as exc:
            print(f"[WARN] تعذر قراءة ملف الحالة {STATE_FILE}: {exc}")
    return set()


def save_notified_state(notified_keys: Set[str]) -> None:
    """حفظ سجل الكراسي المبلّغ عنها (مع الاحتفاظ بآخر 5000 سجل)."""
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
        print(f"[WARN] تعذر حفظ ملف الحالة {STATE_FILE}: {exc}")


def send_telegram_message(text: str) -> bool:
    """إرسال رسالة فورية إلى المستخدم عبر بوت تيليجرام."""
    if not TELEGRAM_BOT_TOKEN or "PUT_YOUR" in TELEGRAM_BOT_TOKEN:
        print("[ERROR] لم يتم ضبط TELEGRAM_BOT_TOKEN.")
        return False
    if not TELEGRAM_CHAT_ID or "PUT_YOUR" in TELEGRAM_CHAT_ID:
        print("[ERROR] لم يتم ضبط TELEGRAM_CHAT_ID.")
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
            print("[OK] تم إرسال إشعار تيليجرام بنجاح.")
            return True
        print(f"[ERROR] فشل إرسال تيليجرام ({resp.status_code}): {resp.text}")
    except Exception as exc:
        print(f"[ERROR] استثناء أثناء إرسال تيليجرام: {exc}")
    return False


# ==============================================================================
# 4. تحليل وتصنيف الكراسي (Seat Filtering & Evaluation)
# ==============================================================================

def parse_seat_code(raw_code: str) -> Tuple[str, Optional[int]]:
    """
    استخراج رمز الصف ورقم الكرسي من كود مثل 'H12' أو 'Row H - Seat 12' أو 'H-12'.
    """
    cleaned = raw_code.strip().upper()
    match = re.search(r"([A-Z]{1,2})\s*[-:]?\s*(\d{1,2})", cleaned)
    if match:
        return match.group(1), int(match.group(2))
    return cleaned, None


def evaluate_seat_quality(
    row_label: str,
    seat_num: Optional[int],
    max_seats_in_row: int = 18,
) -> Tuple[bool, str]:
    """
    تحديد ما إذا كان الكرسي في موقع ممتاز (المنتصف أو الخلف) وليس في الصفوف الأمامية.
    يرجع: (هل_مقبول, وصف_الموقع)
    """
    row_clean = row_label.strip().upper()

    # 1. استبعاد الصفوف الأمامية (A, B, C, D)
    if row_clean in EXCLUDED_FRONT_ROWS:
        return False, "صف أمامي (مستبعد)"

    # 2. التحقق من تمركز الكرسي أفقياً (تجنب الأطراف الجانبية قدر الإمكان)
    is_center_horizontal = True
    if seat_num is not None and max_seats_in_row >= 8:
        min_center = max(2, int(round(max_seats_in_row * CENTER_SEAT_MIN_RATIO)))
        max_center = min(
            max_seats_in_row - 1,
            int(round(max_seats_in_row * CENTER_SEAT_MAX_RATIO)),
        )
        is_center_horizontal = min_center <= seat_num <= max_center

    # 3. تصنيف جودة الصف
    if row_clean in SWEET_SPOT_ROWS:
        if is_center_horizontal:
            return True, "🌟 مثالي (منتصف القاعة - Sweet Spot)"
        return True, "✅ جيد جداً (صف متوسط - قريب من المنتصف)"

    if row_clean in PREFERRED_ROWS or (len(row_clean) == 1 and row_clean > "D"):
        if is_center_horizontal:
            return True, "🌟 ممتاز (الصفوف الخلفية - منتصف)"
        return True, "✅ جيد (الصفوف الخلفية)"

    return False, "غير مطابق لمعايير الصفوف"


def filter_and_rank_seats(available_seats: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """
    فلترة قائمة الكراسي المتاحة وإرجاع الكراسي الواقعة في النص أو الخلف فقط،
    مرتبة حسب الأفضلية (Sweet Spot المنتصف أولاً).
    """
    row_max_seat: Dict[str, int] = {}
    for s in available_seats:
        r_label = s.get("row", "").upper()
        s_num = s.get("number")
        if r_label and isinstance(s_num, int):
            row_max_seat[r_label] = max(row_max_seat.get(r_label, 16), s_num)

    good_seats: List[Dict[str, str]] = []
    for seat in available_seats:
        row_label = seat.get("row", "").upper()
        seat_num = seat.get("number")
        code = seat.get("code", f"{row_label}{seat_num or ''}")
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
# 5. استخراج الحفلات والكراسي من موقع VOX Cinemas Egypt باستخدام Playwright
# ==============================================================================

def parse_showtimes_from_html(html: str, page_url: str) -> List[Dict[str, str]]:
    """
    استخراج جميع الحفلات المعروضة في صفحة الفيلم لفرع City Centre Almaza فقط.
    """
    soup = BeautifulSoup(html, "html.parser")
    sessions: List[Dict[str, str]] = []

    active_date_label = "غير محدد"
    active_date_code = ""
    date_el = soup.select_one(
        ".dates a.active, .dates li.active a, nav.dates a.selected, [data-ShowtimeDate].active"
    )
    if date_el:
        active_date_label = " ".join(date_el.get_text().split())
        href = date_el.get("href", "")
        m_date = re.search(r"d=(\d{8})", href)
        if m_date:
            active_date_code = m_date.group(1)

    cinema_headers = soup.find_all(["h3", "h2", "div"], class_=re.compile(r"highlight|cinema|location", re.I))
    if not cinema_headers:
        cinema_headers = soup.find_all(["h3", "h2"])

    for header in cinema_headers:
        cinema_name = " ".join(header.get_text().split())
        if not any(tc.lower() in cinema_name.lower() for tc in TARGET_CINEMAS):
            continue

        container = header.find_next_sibling(["ol", "ul", "div"]) or header.parent
        if not container:
            continue

        for exp_block in container.find_all(["li", "div"], recursive=True):
            links = exp_block.find_all("a", href=re.compile(r"/booking/|showtime", re.I), recursive=False)
            if not links:
                links = [
                    a for a in exp_block.find_all("a", class_=re.compile(r"showtime|action", re.I))
                    if re.search(r"\d{1,2}:\d{2}\s*(am|pm)", a.get_text(), re.I)
                ]
            if not links:
                continue

            exp_tag = exp_block.find(["strong", "span", "h4", "img"])
            experience_raw = exp_tag.get_text(" ", strip=True) if exp_tag else exp_block.get_text(" ", strip=True)
            experience_upper = experience_raw.upper()

            detected_exp = "Standard"
            if "IMAX" in experience_upper:
                detected_exp = "IMAX"
            elif "MAX" in experience_upper:
                detected_exp = "MAX"
            elif "GOLD" in experience_upper:
                detected_exp = "GOLD"
            elif "4DX" in experience_upper:
                detected_exp = "4DX"
            elif "KIDS" in experience_upper:
                detected_exp = "KIDS"

            if detected_exp.upper() not in [e.upper() for e in TARGET_EXPERIENCES]:
                continue

            for link in links:
                show_time = " ".join(link.get_text().split())
                if not re.search(r"\d{1,2}:\d{2}", show_time):
                    continue

                if TARGET_TIMES and not any(t.lower() in show_time.lower() for t in TARGET_TIMES):
                    continue

                href = link.get("href", "")
                booking_url = (
                    href if href.startswith("http") else f"https://egy.voxcinemas.com{href}"
                )
                session_id = link.get("data-id") or href.split("/")[-1]

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
        key = f"{s['cinema']}_{s['experience']}_{s['date_label']}_{s['time']}_{s['booking_url']}"
        unique_sessions[key] = s
    return list(unique_sessions.values())


def extract_seats_from_booking_page(page: Page, booking_url: str) -> List[Dict[str, str]]:
    """
    فتح صفحة الحفلة واستخراج الكراسي المتاحة (سواء من استجابة JSON الخاصة بـ Vista API أو من عناصر الـ DOM/SVG).
    """
    captured_api_seats: List[Dict[str, str]] = []

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
        page.wait_for_timeout(4000)

        continue_btn = page.query_selector(
            "button.action.primary, a.action.primary, button:has-text('Continue'), button:has-text('Choose Seats')"
        )
        if continue_btn and continue_btn.is_visible():
            try:
                continue_btn.click(timeout=5000)
                page.wait_for_timeout(3000)
            except Exception:
                pass

        if captured_api_seats:
            return captured_api_seats

        dom_seats = page.evaluate(
            """() => {
                const results = [];
                const seatSelectors = [
                    '[data-seat]',
                    '[data-row]',
                    '.seat',
                    'li.seat',
                    'span.seat',
                    'button.seat',
                    'g.seat',
                    'svg [id*="seat" i]'
                ];
                const elements = document.querySelectorAll(seatSelectors.join(','));
                elements.forEach(el => {
                    const cls = (el.className && typeof el.className === 'string')
                        ? el.className.toLowerCase()
                        : (el.getAttribute('class') || '').toLowerCase();
                    const statusAttr = (el.getAttribute('data-status') || el.getAttribute('aria-disabled') || '').toLowerCase();

                    const isTaken = ['sold', 'occupied', 'unavailable', 'taken', 'reserved', 'blocked', 'broken', 'true', 'wheelchair']
                        .some(word => cls.includes(word) || statusAttr.includes(word));
                    if (isTaken) return;

                    const isAvailable = ['available', 'free', 'open', 'standard', 'premium', 'seat']
                        .some(word => cls.includes(word) || statusAttr.includes(word));
                    if (!isAvailable) return;

                    const row = el.getAttribute('data-row')
                        || el.getAttribute('data-row-name')
                        || (el.closest('[data-row]') ? el.closest('[data-row]').getAttribute('data-row') : '')
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
                        results.push({ row: row, num: num, label: label });
                    }
                });
                return results;
            }"""
        )

        parsed_dom_seats: List[Dict[str, str]] = []
        for item in dom_seats:
            raw_label = item.get("label") or f"{item.get('row', '')}{item.get('num', '')}"
            row_label, seat_num = parse_seat_code(raw_label)
            if not row_label and item.get("row"):
                row_label = str(item["row"]).strip().upper()
            if seat_num is None and str(item.get("num", "")).isdigit():
                seat_num = int(item["num"])

            if row_label and len(row_label) <= 2:
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
        print(f"[WARN] تعذر فحص صفحة الكراسي {booking_url}: {exc}")
        return []
    finally:
        try:
            page.remove_listener("response", handle_response)
        except Exception:
            pass


def parse_json_seats(data: object, out_list: List[Dict[str, str]]) -> None:
    """البحث التكراري في استجابات JSON عن الكراسي المتاحة (Available Seats)."""
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
# 6. حلقة الفحص الرئيسية (Main Monitoring Routine)
# ==============================================================================

def format_day_and_date(date_label: str, date_code: str) -> str:
    """تنسيق اليوم والتاريخ بشكل واضح باللغتين العربية والإنجليزية."""
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
        raise RuntimeError("يرجى تثبيت مكتبة playwright أولاً: pip install playwright && playwright install chromium")

    now_cairo = datetime.now(CAIRO_TZ).strftime("%Y-%m-%d %I:%M:%S %p")
    print(f"=== بدء فحص VOX Cinemas City Centre Almaza ({now_cairo} بتوقيت القاهرة) ===")

    notified_keys = load_notified_state()
    new_notifications_sent = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
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
        page = context.new_page()

        all_discovered_sessions: List[Dict[str, str]] = []

        for movie_url in MOVIE_URLS:
            print(f"[INFO] جاري فتح صفحة الفيلم: {movie_url}")
            try:
                resp = page.goto(movie_url, wait_until="domcontentloaded", timeout=35000)
                if not resp or resp.status >= 400:
                    print(f"[INFO] الصفحة {movie_url} غير متاحة حالياً (Status: {resp.status if resp else 'None'}).")
                    continue
                page.wait_for_timeout(3000)

                html = page.content()
                soup = BeautifulSoup(html, "html.parser")

                date_links: List[Tuple[str, str]] = []
                for a_el in soup.select(".dates a[href*='?d='], a[href*='?d=']"):
                    href = a_el.get("href", "")
                    m_d = re.search(r"d=(\d{8})", href)
                    if m_d:
                        d_code = m_d.group(1)
                        if TARGET_DATES and d_code not in TARGET_DATES:
                            continue
                        full_d_url = (
                            href if href.startswith("http") else f"https://egy.voxcinemas.com{href}"
                        )
                        date_links.append((d_code, full_d_url))

                all_discovered_sessions.extend(parse_showtimes_from_html(html, movie_url))

                seen_dates = set()
                for d_code, d_url in date_links:
                    if d_code in seen_dates:
                        continue
                    seen_dates.add(d_code)
                    try:
                        page.goto(d_url, wait_until="domcontentloaded", timeout=30000)
                        page.wait_for_timeout(2000)
                        all_discovered_sessions.extend(
                            parse_showtimes_from_html(page.content(), d_url)
                        )
                    except Exception as d_exc:
                        print(f"[WARN] تعذر تحميل تاريخ {d_code}: {d_exc}")

            except Exception as exc:
                print(f"[WARN] خطأ أثناء فحص {movie_url}: {exc}")

        print(f"[INFO] إجمالي حفلات City Centre Almaza المكتشفة: {len(all_discovered_sessions)}")

        for session in all_discovered_sessions:
            cinema = session["cinema"]
            experience = session["experience"]
            is_max = session["is_max"]
            date_str = format_day_and_date(session["date_label"], session["date_code"])
            show_time = session["time"]
            booking_url = session["booking_url"]

            print(f"[CHECK] {cinema} | {experience} | {date_str} | {show_time}")
            raw_seats = extract_seats_from_booking_page(page, booking_url)
            good_seats = filter_and_rank_seats(raw_seats)

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
                session_open_key = f"SESSION_OPEN_{cinema}_{experience}_{session['date_code']}_{show_time}"
                if session_open_key not in notified_keys:
                    max_badge = "✅ <b>نعم (قاعة MAX 🔊)</b>" if is_max else f"لا (<b>{experience}</b>)"
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

    save_notified_state(notified_keys)
    print(f"=== انتهى الفحص. عدد الإشعارات الجديدة المرسلة: {new_notifications_sent} ===")


if __name__ == "__main__":
    if "--test" in sys.argv:
        send_telegram_message(
            "✅ <b>تجربة ناجحة!</b>\n"
            "بوت مراقبة حجوزات <b>VOX Cinemas — City Centre Almaza (Avengers: Doomsday)</b> متصل وجاهز للعمل."
        )
    else:
        run_watcher()
