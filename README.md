# Complete Step-by-Step Tutorial: VOX Cinemas (City Centre Almaza) Seat & Early-Booking Watcher Bot

This automated cloud bot monitors [Avengers: Doomsday on VOX Cinemas Egypt](https://egy.voxcinemas.com/movies/avengers-doomsday) and the `Avengers: Doomsday (Infinity Vision)` listing on the [VOX Coming Soon page](https://egy.voxcinemas.com/movies/comingsoon) and [VOX What's On page](https://egy.voxcinemas.com/movies/whatson) exclusively for the **[City Centre Almaza](https://egy.voxcinemas.com/showtimes/city-centre-almaza)** branch. It runs automatically every **10 minutes** for free on **GitHub Actions** (so your PC does not need to stay on) and sends instant **Telegram** alerts whenever a seat in the middle or back rows opens up (such as a canceled/returned ticket or a newly added showtime).

## 1. How the Bot Picks Ideal Seats Automatically

The seat filter in `vox_watcher.py` is calibrated for **[MAX](https://egy.voxcinemas.com/ways-to-watch/max)**, **Standard**, and **[IMAX](https://egy.voxcinemas.com/ways-to-watch/imax)** auditoriums at [VOX Cinemas City Centre Almaza](https://egy.voxcinemas.com/showtimes/city-centre-almaza):

| Auditorium Zone | Row Letters | Bot Action | Viewing Experience |
| :--- | :--- | :--- | :--- |
| **Front Rows** | `A, B, C, D` | ❌ **Strictly Excluded** | Too close to the screen; causes neck strain and viewing angle distortion |
| **Sweet Spot (Middle-Back Center)** | `F, G, H, I, J, K, L` (Middle 60% of row) | 🌟 **Top Priority (Ideal)** | Optimal eye-level sightline and Dolby Atmos acoustic focal point |
| **Elevated Back Rows** | `E` and `M, N, O, P` | ✅ **Accepted & Ranked** | Full panoramic view from the upper stadium tiers |
| **Extreme Side Edges** | Outer 20% left/right seats per row | ⚠️ **Secondary Priority** | Center seats (`20%–80%` of row width) are ranked first in every alert |

## 2. What Every Telegram Notification Includes

Whenever an eligible seat (or new showtime) becomes available at [City Centre Almaza](https://egy.voxcinemas.com/showtimes/city-centre-almaza), the bot sends a formatted Telegram message containing:

* **Movie & Branch**: `Avengers: Doomsday` — `City Centre Almaza`
* **MAX Status (`هل الحفلة MAX؟`)**: Explicitly states `✅ نعم (قاعة MAX 🔊)` or `لا (Standard)` / `لا (IMAX)`
* **Day & Full Date**: e.g., `الأربعاء (Wed 16 Dec) — 2026-12-16`
* **Showtime**: e.g., `07:15 PM` or `11:00 PM`
* **Row Number & Seat Code (`رقم الصف وكود الكرسي`)**: e.g., `الصف H: H9, H10 (🌟 مثالي - منتصف القاعة)`
* **Direct Booking Link**: A one-tap link that opens that exact showtime's seat map so you can book immediately.

## 3. Project Folder Structure

Your GitHub repository must have these exact 3 files in this structure:

```text
vox-cinema-watcher/
├── vox_watcher.py
├── requirements.txt
└── .github/
    └── workflows/
        └── vox_watcher.yml
```

## 4. Detailed Step-by-Step Setup Tutorial

### Step 1: Create Your Telegram Bot & Get Your Token and Chat ID

1. Open **Telegram**, search for `@BotFather` (with the official blue verification badge), and click **Start**.
2. Send the command `/newbot`.
3. Type any display name for your bot (for example: `Almaza VOX Watcher`).
4. Type a username ending in `bot` (for example: `youssif_almaza_vox_bot`).
5. `@BotFather` will reply with an **HTTP API Token** (looks like `7123456789:AAHxyz123456...`). Copy and save this — it is your `TELEGRAM_BOT_TOKEN`.
6. Click the `t.me/...` link in `@BotFather`'s message to open your new bot, press **Start**, and send any message (like `hello`).
7. Open your browser and visit this URL (replace `<YOUR_BOT_TOKEN>` with the token you copied in step 5):
   `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates`
8. Look for `"chat":{"id":123456789` in the JSON text that appears. Copy that number (e.g., `123456789`) — this is your `TELEGRAM_CHAT_ID`.

### Step 2: Create a Free GitHub Repository

1. Sign in to GitHub, click the **`+`** icon in the top-right corner, and choose **New repository**.
2. Set **Repository name** to `vox-cinema-watcher`.
3. Select **Public**:
   * **Why Public?** GitHub Actions gives **Public** repositories **unlimited free minutes (100% free 24/7)**, whereas Private repositories are capped at 2,000 minutes/month. Your Telegram Token and Chat ID will be stored inside encrypted **GitHub Secrets**, so no one can ever see them even in a Public repo.
4. Check **Add a README file**, then click **Create repository**.

### Step 3: Upload the Project Files to GitHub

You can either extract `vox_cinema_almaza_watcher.zip` and upload its contents, or create the 3 files directly in the GitHub web interface:

1. **Add `vox_watcher.py`**:
   * Inside your repo, click **Add file** → **Create new file**.
   * Name the file `vox_watcher.py`, paste the full code of `vox_watcher.py`, and click **Commit changes**.
2. **Add `requirements.txt`**:
   * Click **Add file** → **Create new file**.
   * Name the file `requirements.txt`, paste the 3 lines (`playwright==1.49.0`, `requests==2.32.3`, `beautifulsoup4==4.12.3`), and click **Commit changes**.
3. **Add `.github/workflows/vox_watcher.yml`**:
   * Click **Add file** → **Create new file**.
   * In the file name box, type the full path including slashes:
     `.github/workflows/vox_watcher.yml`
   * Paste the full content of `vox_watcher.yml` and click **Commit changes**.

### Step 4: Add Your Telegram Credentials to GitHub Secrets

1. In your GitHub repository, click the **Settings** tab at the top.
2. In the left sidebar, expand **Secrets and variables** and click **Actions**.
3. Click the green **New repository secret** button:
   * **Name**: `TELEGRAM_BOT_TOKEN`
   * **Secret**: Paste your BotFather token, then click **Add secret**.
4. Click **New repository secret** again:
   * **Name**: `TELEGRAM_CHAT_ID`
   * **Secret**: Paste your numeric Chat ID, then click **Add secret**.

### Step 5: Test & Activate the Bot

1. Click the **Actions** tab at the top of your GitHub repository.
2. On the left sidebar, click **VOX Cinemas Seat Watcher (Avengers Doomsday)**.
3. On the right side, click the **Run workflow** dropdown button.
4. Set **إرسال رسالة تجريبية على تيليجرام؟** to `true`, then click the green **Run workflow** button.
5. Wait ~45 seconds:
   * You will receive a **Telegram test confirmation message** (`✅ تجربة ناجحة!`) verifying your bot is connected.
   * Immediately after, the bot will scan all early-booking dates (Wed 16 Dec – Wed 23 Dec 2026 and any new dates added) for [Avengers: Doomsday](https://egy.voxcinemas.com/movies/avengers-doomsday) at [City Centre Almaza](https://egy.voxcinemas.com/showtimes/city-centre-almaza) and send alerts for all matching showtimes/seats.
   * From that moment on, GitHub Actions will automatically run the check **every 10 minutes** 24/7 and only message you when **new** seats or showtimes open up!

## 5. Customizing Filters (Optional)

At the top of `vox_watcher.py`, you can tweak any setting at any time:

* **Only require 2+ seats together**: Change `MIN_SEATS_NEEDED: '1'` to `'2'` in `.github/workflows/vox_watcher.yml`.
* **Filter specific dates only**: Set `TARGET_DATES = ["20261216", "20261217", "20261218"]` (in `YYYYMMDD` format). Leaving it `[]` checks all available dates automatically.
* **Filter specific showtimes only**: Set `TARGET_TIMES = ["7:15pm", "10:30pm", "11:00pm"]`. Leaving it `[]` checks all showtimes.
* **Filter MAX only**: Set `TARGET_EXPERIENCES = ["MAX"]` if you want to ignore Standard halls.
