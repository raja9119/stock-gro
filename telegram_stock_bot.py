#!/usr/bin/env python3
"""telegram_stock_bot.py — Full Telegram Bot for StockGro Auto Signups & Multi-Firebase Management.

Features:
- Multi-Firebase Management: Add/Remove/List multiple Firebase DBs directly via Telegram commands!
- Dynamic Number Scanner: Scans all connected Firebases for available phone numbers.
- Fast-Forward OTP Fetcher: Concurrently polls all connected Firebases for incoming OTPs.
- Custom Referral & Timeout Settings: Control referral code & OTP wait timeout via Telegram.

Telegram Commands:
- /start, /help            : Main menu & help
- /auto                    : Auto-pick 1 available number from any Firebase & register
- /signup <phone>          : Register a specific 10-digit phone number
- /batch                   : Auto-register ALL pending numbers across ALL Firebases
- /accounts                : View all saved accounts summary
- /firebases               : List all connected Firebase Database URLs
- /addfirebase <url>       : Add a new Firebase Database URL
- /delfirebase <url_or_num>: Remove a Firebase Database URL
- /referral <code_or_get>  : View or update default referral code
- /timeout <seconds>       : View or update OTP wait timeout (Default: 30s)
"""
import concurrent.futures
import json
import os
import random
import re
import threading
import time
from urllib.parse import unquote

import requests

# --- PATH CONFIGURATION ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ACCOUNTS_FILE = os.path.join(SCRIPT_DIR, "accounts.json")
TELEGRAM_CONFIG_FILE = os.path.join(SCRIPT_DIR, "telegram_config.json")
FIREBASES_FILE = os.path.join(SCRIPT_DIR, "firebases.json")

# --- GLOBAL BOT CONFIGURATION ---
DEFAULT_FIREBASE_URLS = [
    "https://dark-274b4-default-rtdb.firebaseio.com"
]
DEFAULT_REFERRAL = "YYX20ZK5"
OTP_TIMEOUT_SECONDS = 10  # Fast 10-second timeout per number for rapid batch processing

APP = "https://app.stockgro.club"
ACCOUNTS = "https://accounts.stockgro.club"
CLIENT_ID = "b711c4dd-7df5-42e6-80e6-d111c1255cd7"
UA = ("Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/151.0.0.0 Mobile Safari/537.36")

NAMES = ("Arjun", "Rohan", "Kabir", "Aryan", "Vivaan", "Aditya", "Ishaan",
         "Dhruv", "Reyansh", "Ananya")


def digits(s, n=10):
    d = re.sub(r"\D", "", str(s or ""))
    return d[-n:] if len(d) > n else d


# ---------------- Multi-Firebase Storage Helper ----------------

def load_firebase_urls():
    if os.path.exists(FIREBASES_FILE):
        try:
            with open(FIREBASES_FILE, "r", encoding="utf-8") as f:
                urls = json.load(f)
                if isinstance(urls, list) and urls:
                    return urls
        except Exception:
            pass
    # Save default if file doesn't exist
    save_firebase_urls(DEFAULT_FIREBASE_URLS)
    return list(DEFAULT_FIREBASE_URLS)


def save_firebase_urls(urls):
    try:
        with open(FIREBASES_FILE, "w", encoding="utf-8") as f:
            json.dump(urls, f, indent=2)
    except Exception:
        pass


# ---------------- StockGro API layer ----------------

def bootstrap(sess, retries=4):
    for attempt in range(retries):
        r = sess.get(f"{APP}/", timeout=(5, 12))
        m = re.search(r"state=([A-Za-z0-9%]+)", r.text)
        if m:
            break
        time.sleep(3 * (attempt + 1))
    else:
        raise RuntimeError("could not obtain state from app root")
    state = unquote(m.group(1))
    page = (f"{ACCOUNTS}/?client_id={CLIENT_ID}&client_name=stockgro_web"
            f"&redirect_uri=https%3A%2F%2Fapp.stockgro.club%3F"
            f"&state={state.replace('=', '%3D')}&theme=dark")
    sess.get(page, headers={
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "cross-site", "Referer": f"{APP}/",
    }, timeout=(5, 12))
    sess.cookies.set("sgInfo", json.dumps({
        "client_id": CLIENT_ID,
        "client_name": "stockgro_web",
        "redirect_uri": "https://app.stockgro.club?",
        "state": state, "theme": "dark",
    }), domain="accounts.stockgro.club", path="/")
    return state, page


def api_headers(state, page):
    return {"Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json;charset=UTF-8",
            "Origin": ACCOUNTS, "Referer": page, "platform": "web",
            "client-state": state}


def stockgro_identity(sess, state, page, phone, country="IN", retries=2):
    for attempt in range(retries):
        try:
            r = sess.post(f"{ACCOUNTS}/api/getIdentity",
                          json={"phone_number": phone, "country_code": country,
                                "otp_channel": "sms"},
                          headers=api_headers(state, page), timeout=(5, 12))
            return r.json().get("data") or {}
        except Exception:
            time.sleep(1)
    return {}


def create_otp(sess, state, page, phone, referral, country="IN", retries=3):
    curr_sess, curr_state, curr_page = sess, state, page
    for attempt in range(retries):
        try:
            r = curr_sess.post(f"{ACCOUNTS}/api/login/createOtp",
                               json={"phone_number": phone, "country_code": country,
                                     "otp_channel": "sms", "flow_type": "signup",
                                     "invitation_code": referral},
                               headers=api_headers(curr_state, curr_page), timeout=(5, 12))
            body = r.json()
            sid = (body.get("data") or {}).get("session_id")
            if sid:
                return sid, curr_sess, curr_state, curr_page
            
            err_code = str(body.get("error_code") or "")
            msg = str(body.get("message") or "")
            if "EXPIRE" in err_code.upper() or "EXPIRE" in msg.upper() or "SESSION" in err_code.upper():
                curr_sess = requests.Session()
                curr_sess.headers.update({"User-Agent": UA})
                curr_state, curr_page = bootstrap(curr_sess)
                time.sleep(1)
            else:
                raise RuntimeError(f"createOtp failed: {err_code} {msg}")
        except Exception as e:
            if attempt == retries - 1:
                raise e
            time.sleep(1)
            try:
                curr_sess = requests.Session()
                curr_sess.headers.update({"User-Agent": UA})
                curr_state, curr_page = bootstrap(curr_sess)
            except Exception:
                pass
    raise RuntimeError("createOtp failed after session refresh retries")


def validate_otp(sess, state, page, phone, otp, session_id, country="IN"):
    r = sess.post(f"{ACCOUNTS}/api/login/validateOtp",
                  json={"session_id": session_id, "otp": otp,
                        "phone_number": phone, "country_code": country,
                        "otp_channel": "sms", "flow_type": "signup"},
                  headers=api_headers(state, page), timeout=(5, 12))
    body = r.json()
    if not body.get("success"):
        raise RuntimeError(f"validateOtp: {body.get('error_code')} {body.get('message')}")
    return True


def register_user(sess, state, page, phone, otp, session_id, referral, name, country="IN"):
    r = sess.post(f"{ACCOUNTS}/api/signup/registerUser",
                  json={"display_name": name, "invitation_code": referral,
                        "otp": otp, "session_id": session_id,
                        "whatsapp_consent": True,
                        "phone_number": phone, "country_code": country,
                        "otp_channel": "sms"},
                  headers=api_headers(state, page), timeout=(5, 12))
    body = r.json()
    data = body.get("data") or {}
    if not body.get("success"):
        raise RuntimeError(f"registerUser: {body.get('error_code')} {body.get('message')}")
    return data


def redeem_access_code(sess, data, page):
    m = re.search(r"access_code=([0-9a-f-]+)", data.get("redirect_uri", ""))
    if not m:
        raise RuntimeError("no access_code in redirect_uri")
    code = m.group(1)
    sess.get(data["redirect_uri"], headers={
        "Sec-Fetch-Dest": "document", "Referer": page}, timeout=(5, 12))
    r = sess.post(f"{APP}/api/login", json={"code": code},
                  headers={"User-Agent": UA, "Origin": APP,
                           "Referer": f"{APP}/",
                           "Content-Type": "text/plain;charset=UTF-8",
                           "Accept": "*/*"}, timeout=(5, 12))
    logged_in = "loginInfo" in [c.name for c in sess.cookies]
    return {"user_id": data.get("user_id"), "access_code": code, "logged_in": logged_in}


def load_registered_phones():
    try:
        if os.path.exists(ACCOUNTS_FILE):
            with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
                accs = json.load(f)
                return set(a.get("phone") for a in accs if a.get("phone"))
        return set()
    except Exception:
        return set()


def save_account(rec):
    # Local save
    try:
        accs = []
        if os.path.exists(ACCOUNTS_FILE):
            try:
                with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
                    accs = json.load(f)
            except Exception:
                accs = []
        if not any(a.get("phone") == rec.get("phone") for a in accs):
            accs.append(rec)
            with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
                json.dump(accs, f, indent=1)
    except Exception:
        pass

    # Save to all connected Firebases
    save_account_to_firebases(rec.get("phone"), rec)


def save_account_to_firebases(phone, rec):
    clean_phone = digits(phone)
    if not clean_phone:
        return

    payload = {
        "registered": rec.get("status") == "registered",
        "status": rec.get("status", "completed"),
        "user_id": rec.get("user_id", ""),
        "otp": rec.get("otp", ""),
        "referral": rec.get("referral", DEFAULT_REFERRAL),
        "updated_at": int(time.time())
    }

    fb_urls = load_firebase_urls()

    def _update_fb(url):
        try:
            url = url.rstrip('/')
            ep = f"{url}/numbers/+91{clean_phone}.json"
            requests.patch(ep, json=payload, timeout=3)
        except Exception:
            pass

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        for url in fb_urls:
            executor.submit(_update_fb, url)


# ---------------- Multi-Firebase Fast Forward OTP Functions ----------------

SEEN_OTP_MSG_IDS = set()

def _fetch_from_url(fb_url, phone, start_time_ms):
    global SEEN_OTP_MSG_IDS
    fb_url = fb_url.rstrip('/')
    endpoints = [
        f"{fb_url}/messages.json",
        f"{fb_url}/otps/{phone}.json",
        f"{fb_url}/otps.json",
        f"{fb_url}/latest_otp.json"
    ]
    for ep in endpoints:
        try:
            r = requests.get(ep, timeout=2)
            if r.status_code == 200 and r.json():
                val = r.json()
                if ep.endswith("/messages.json"):
                    candidates = []
                    if isinstance(val, dict):
                        for k, item in val.items():
                            if isinstance(item, dict):
                                if "message" in item:
                                    candidates.append(item)
                                else:
                                    for sub_k, sub_item in item.items():
                                        if isinstance(sub_item, dict) and "message" in sub_item:
                                            candidates.append(sub_item)
                    
                    matched_otps = []
                    for msg_obj in candidates:
                        try:
                            msg_id = int(msg_obj.get("id") or 0)
                        except (ValueError, TypeError):
                            msg_id = 0
                        
                        msg_text = str(msg_obj.get("message", ""))
                        sender = str(msg_obj.get("sender", ""))
                        
                        text_lower = msg_text.lower()
                        sender_lower = sender.lower()
                        
                        # Strictly verify this is a StockGro OTP SMS
                        is_stockgro = (
                            "stockgro" in text_lower or
                            "stkgro" in text_lower or
                            "stock gro" in text_lower or
                            "stkgro" in sender_lower or
                            "stock" in sender_lower
                        )
                        
                        if is_stockgro and msg_id >= (start_time_ms - 2000):
                            m = re.search(r'\b(\d{6})\b', msg_text)
                            if m:
                                otp_code = m.group(1)
                                msg_key = f"{msg_id}_{otp_code}"
                                if msg_key not in SEEN_OTP_MSG_IDS:
                                    matched_otps.append((msg_id, otp_code, msg_key))
                    
                    if matched_otps:
                        matched_otps.sort(key=lambda x: x[0], reverse=True)
                        best_msg_id, best_otp, best_key = matched_otps[0]
                        SEEN_OTP_MSG_IDS.add(best_key)
                        return best_otp
                else:
                    if isinstance(val, dict):
                        if "otp" in val:
                            otp_code = re.sub(r"\D", "", str(val["otp"]))
                            if len(otp_code) == 6:
                                return otp_code
                    elif isinstance(val, (str, int)):
                        c = re.sub(r"\D", "", str(val))
                        if len(c) == 6:
                            return c
        except Exception:
            pass
    return None


def fetch_fast_otp_multi_firebase(phone, start_time_ms, timeout_seconds=15):
    deadline = time.time() + timeout_seconds
    fb_urls = load_firebase_urls()

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(len(fb_urls) * 6, 20)) as executor:
        while time.time() < deadline:
            futures = [executor.submit(_fetch_from_url, url, phone, start_time_ms) for url in fb_urls]
            for future in concurrent.futures.as_completed(futures):
                res = future.result()
                if res:
                    return res
            time.sleep(0.1)
    return None


def get_numbers_from_all_firebases():
    available_numbers = []
    fb_urls = load_firebase_urls()

    def _scan_db(url):
        nums = []
        url = url.rstrip('/')
        try:
            r = requests.get(f"{url}/numbers.json", timeout=3)
            if r.status_code == 200 and r.json():
                for n, details in r.json().items():
                    cn = digits(n)
                    if len(cn) == 10:
                        if isinstance(details, dict) and details.get("registered") is True:
                            continue
                        nums.append(cn)
        except Exception:
            pass
        try:
            r = requests.get(f"{url}/messages.json", timeout=3)
            if r.status_code == 200 and r.json():
                content_str = json.dumps(r.json())
                found = re.findall(r'\b[6-9]\d{9}\b', content_str)
                nums.extend(found)
        except Exception:
            pass
        return nums

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(_scan_db, u) for u in fb_urls]
        for f in concurrent.futures.as_completed(futures):
            for num in f.result():
                if num not in available_numbers:
                    available_numbers.append(num)
    return available_numbers


def process_single_number_bot(phone, referral=DEFAULT_REFERRAL, country="IN"):
    name = random.choice(NAMES) + str(random.randint(10, 99))
    sess = requests.Session()
    sess.headers.update({"User-Agent": UA})
    try:
        state, page = bootstrap(sess)
    except Exception as e:
        save_account({"phone": phone, "status": "bootstrap_failed", "ts": int(time.time())})
        return False, f"❌ Bootstrap Failed for {phone}: {str(e)[:100]}"

    try:
        ident = stockgro_identity(sess, state, page, phone, country)
    except Exception as e:
        save_account({"phone": phone, "status": "identity_check_failed", "ts": int(time.time())})
        return False, f"❌ Identity check failed for {phone}: {str(e)[:100]}"

    if ident.get("existing_user"):
        save_account({"phone": phone, "status": "already_registered", "user_id": ident.get("user_id"), "ts": int(time.time())})
        return False, f"⚠️ Number {phone} is already registered on StockGro (User ID: {ident.get('user_id')})"

    try:
        request_time_ms = int(time.time() * 1000)
        session_id, sess, state, page = create_otp(sess, state, page, phone, referral, country)
    except Exception as e:
        save_account({"phone": phone, "status": "create_otp_failed", "ts": int(time.time())})
        return False, f"❌ OTP Send Failed for {phone}: {str(e)[:100]}"

    otp = fetch_fast_otp_multi_firebase(phone, request_time_ms, timeout_seconds=OTP_TIMEOUT_SECONDS)
    if not otp:
        save_account({"phone": phone, "status": "otp_timeout", "ts": int(time.time())})
        return False, f"⚠️ OTP Timeout ({OTP_TIMEOUT_SECONDS}s) for {phone}. No incoming SMS detected across connected Firebases."

    try:
        validate_otp(sess, state, page, phone, otp, session_id, country)
        data = register_user(sess, state, page, phone, otp, session_id, referral, name, country)
        result = redeem_access_code(sess, data, page)
    except Exception as e:
        save_account({"phone": phone, "status": "registration_failed", "otp": otp, "ts": int(time.time())})
        return False, f"❌ Registration failed for {phone}: {str(e)[:100]}"

    save_account({"phone": phone, "status": "registered",
                  "user_id": result["user_id"], "otp": otp,
                  "logged_in": result["logged_in"], "referral": referral,
                  "name": name, "ts": int(time.time())})

    msg = (
        f"🎉 <b>StockGro Account Registered!</b>\n\n"
        f"📱 <b>Phone:</b> <code>{phone}</code>\n"
        f"👤 <b>Name:</b> {name}\n"
        f"🔑 <b>OTP:</b> <code>{otp}</code>\n"
        f"🆔 <b>User ID:</b> <code>{result['user_id']}</code>\n"
        f"🎁 <b>Referral Code:</b> <code>{referral}</code>\n"
        f"✅ <i>Synced to Firebase & Local DB</i>"
    )
    return True, msg


# ---------------- Telegram Bot Runner ----------------

class TelegramBot:
    def __init__(self, token):
        self.token = token
        self.api_url = f"https://api.telegram.org/bot{token}"
        self.offset = 0

    def send_message(self, chat_id, text):
        try:
            requests.post(f"{self.api_url}/sendMessage", json={
                "chat_id": chat_id, "text": text, "parse_mode": "HTML"
            }, timeout=8)
        except Exception:
            pass

    def get_updates(self):
        try:
            r = requests.get(f"{self.api_url}/getUpdates", params={
                "offset": self.offset, "timeout": 20
            }, timeout=25)
            if r.status_code == 200:
                data = r.json()
                if data.get("ok"):
                    return data.get("result", [])
        except Exception:
            pass
        return []

    def handle_message(self, update):
        global DEFAULT_REFERRAL, OTP_TIMEOUT_SECONDS
        msg = update.get("message") or update.get("edited_message")
        if not msg or "text" not in msg:
            return

        chat_id = msg["chat"]["id"]
        text = msg["text"].strip()
        parts = text.split()
        cmd = parts[0].lower()

        if cmd in ("/start", "/help"):
            fb_count = len(load_firebase_urls())
            reply = (
                f"🤖 <b>StockGro Automated Multi-Firebase Bot</b>\n\n"
                f"Connected Firebases: <b>{fb_count}</b>\n"
                f"OTP Timeout: <b>{OTP_TIMEOUT_SECONDS}s</b>\n"
                f"Referral Code: <code>{DEFAULT_REFERRAL}</code>\n\n"
                f"Available Commands:\n"
                f"🔹 <b>/auto</b> — Auto-pick 1 available number from Firebase & register\n"
                f"🔹 <b>/signup &lt;phone&gt;</b> — Register a specific 10-digit phone number\n"
                f"🔹 <b>/batch</b> — Auto-register ALL pending numbers across all Firebases\n"
                f"🔹 <b>/accounts</b> — View total registered accounts summary\n\n"
                f"⚙️ <b>Firebase Management Commands:</b>\n"
                f"🌐 <b>/firebases</b> — View all connected Firebase DB URLs\n"
                f"➕ <b>/addfirebase &lt;url&gt;</b> — Add a new Firebase Database URL\n"
                f"➖ <b>/delfirebase &lt;url_or_num&gt;</b> — Remove a Firebase Database URL\n\n"
                f"⚙️ <b>Settings Commands:</b>\n"
                f"🎁 <b>/referral &lt;code&gt;</b> — View or change default referral code\n"
                f"⏱ <b>/timeout &lt;seconds&gt;</b> — Set OTP timeout limit (current: {OTP_TIMEOUT_SECONDS}s)\n"
            )
            self.send_message(chat_id, reply)

        elif cmd == "/auto":
            fb_urls = load_firebase_urls()
            self.send_message(chat_id, f"⏳ <i>Scanning {len(fb_urls)} connected Firebases for available numbers...</i>")
            fb_nums = get_numbers_from_all_firebases()
            already_saved = load_registered_phones()
            pending = [n for n in fb_nums if n not in already_saved]

            if not pending:
                self.send_message(chat_id, f"⚠️ No pending numbers found across {len(fb_urls)} connected Firebases!")
                return

            phone = pending[0]
            self.send_message(chat_id, f"⚡ Auto-picked number <code>{phone}</code>. Requesting OTP...")
            
            def run_job():
                success, result_msg = process_single_number_bot(phone)
                self.send_message(chat_id, result_msg)
            
            threading.Thread(target=run_job).start()

        elif cmd in ("/signup", "/register"):
            if len(parts) < 2:
                self.send_message(chat_id, "⚠️ Usage: <code>/signup 9876543210</code>")
                return
            phone = digits(parts[1])
            if len(phone) != 10:
                self.send_message(chat_id, "❌ Please enter a valid 10-digit phone number.")
                return

            self.send_message(chat_id, f"⚡ Starting registration for <code>{phone}</code> with referral <code>{DEFAULT_REFERRAL}</code>...")
            
            def run_job():
                success, result_msg = process_single_number_bot(phone)
                self.send_message(chat_id, result_msg)

            threading.Thread(target=run_job).start()

        elif cmd == "/batch":
            fb_urls = load_firebase_urls()
            self.send_message(chat_id, f"🔍 <i>Scanning {len(fb_urls)} connected Firebases for pending numbers...</i>")
            fb_nums = get_numbers_from_all_firebases()
            already_saved = load_registered_phones()
            pending = [n for n in fb_nums if n not in already_saved]

            if not pending:
                self.send_message(chat_id, f"⚠️ No pending numbers found. Total in Firebases: {len(fb_nums)} (All already processed).")
                return

            self.send_message(chat_id, f"🚀 <b>Starting Automated Batch Signup for {len(pending)} numbers across {len(fb_urls)} Firebases!</b>")

            def run_batch():
                success_count = 0
                failed_count = 0
                for idx, phone in enumerate(pending, 1):
                    self.send_message(chat_id, f"⏳ Processing <code>{phone}</code> ({idx}/{len(pending)})...")
                    ok, res = process_single_number_bot(phone)
                    if ok:
                        success_count += 1
                        self.send_message(chat_id, res)
                    else:
                        failed_count += 1
                        self.send_message(chat_id, res)
                    time.sleep(1)

                self.send_message(chat_id, (
                    f"✅ <b>BATCH COMPLETED!</b>\n\n"
                    f"✔ Done: <b>{success_count}</b>\n"
                    f"✖ Failed/Skipped: <b>{failed_count}</b>\n"
                    f"All data updated in connected Firebases and local storage!"
                ))

            threading.Thread(target=run_batch).start()

        elif cmd == "/firebases":
            fb_urls = load_firebase_urls()
            lines = [f"🌐 <b>Connected Firebase DBs ({len(fb_urls)} Total):</b>\n"]
            for idx, url in enumerate(fb_urls, 1):
                lines.append(f"{idx}. <code>{url}</code>")
            lines.append("\n➕ Add new: <code>/addfirebase https://your-db.firebaseio.com</code>")
            lines.append("➖ Remove: <code>/delfirebase 1</code>")
            self.send_message(chat_id, "\n".join(lines))

        elif cmd in ("/addfirebase", "/addfb"):
            if len(parts) < 2:
                self.send_message(chat_id, "⚠️ Usage: <code>/addfirebase https://myproject-rtdb.firebaseio.com</code>")
                return
            new_url = parts[1].strip().rstrip('/')
            if not new_url.startswith("http"):
                self.send_message(chat_id, "❌ Please enter a valid Firebase URL starting with https://")
                return

            fb_urls = load_firebase_urls()
            if new_url in fb_urls:
                self.send_message(chat_id, "⚠️ That Firebase URL is already added!")
                return

            fb_urls.append(new_url)
            save_firebase_urls(fb_urls)
            self.send_message(chat_id, f"✅ <b>Added new Firebase DB:</b>\n<code>{new_url}</code>\nTotal Connected: {len(fb_urls)}")

        elif cmd in ("/delfirebase", "/delfb", "/rmfirebase"):
            if len(parts) < 2:
                self.send_message(chat_id, "⚠️ Usage: <code>/delfirebase 1</code> or <code>/delfirebase https://url</code>")
                return
            arg = parts[1].strip()
            fb_urls = load_firebase_urls()
            removed_url = None

            if arg.isdigit():
                idx_val = int(arg) - 1
                if 0 <= idx_val < len(fb_urls):
                    removed_url = fb_urls.pop(idx_val)
            else:
                arg_clean = arg.rstrip('/')
                if arg_clean in fb_urls:
                    fb_urls.remove(arg_clean)
                    removed_url = arg_clean

            if removed_url:
                save_firebase_urls(fb_urls)
                self.send_message(chat_id, f"🗑 <b>Removed Firebase DB:</b>\n<code>{removed_url}</code>\nRemaining Connected: {len(fb_urls)}")
            else:
                self.send_message(chat_id, "❌ Firebase URL or list index not found!")

        elif cmd == "/accounts":
            if not os.path.exists(ACCOUNTS_FILE):
                self.send_message(chat_id, "📂 No accounts file found yet.")
                return
            try:
                with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
                    accs = json.load(f)
            except Exception:
                accs = []

            if not accs:
                self.send_message(chat_id, "📂 Accounts list is empty.")
                return

            registered_ones = [a for a in accs if a.get("status") == "registered"]
            lines = [f"📊 <b>Total Logged Accounts:</b> {len(accs)} ({len(registered_ones)} newly created)\n"]
            for idx, a in enumerate(accs[-10:], 1):
                lines.append(f"{idx}. <code>{a.get('phone')}</code> | {a.get('status')} | ID: <code>{a.get('user_id','-')[:8]}...</code>")
            
            self.send_message(chat_id, "\n".join(lines))

        elif cmd == "/referral":
            if len(parts) > 1:
                DEFAULT_REFERRAL = parts[1].strip().upper()
                self.send_message(chat_id, f"✅ Default referral code set to: <code>{DEFAULT_REFERRAL}</code>")
            else:
                self.send_message(chat_id, f"🎁 Current default referral code: <code>{DEFAULT_REFERRAL}</code>")

        elif cmd == "/timeout":
            if len(parts) > 1 and parts[1].isdigit():
                OTP_TIMEOUT_SECONDS = int(parts[1])
                self.send_message(chat_id, f"⏱ OTP timeout updated to: <b>{OTP_TIMEOUT_SECONDS} seconds</b>")
            else:
                self.send_message(chat_id, f"⏱ Current OTP wait timeout: <b>{OTP_TIMEOUT_SECONDS} seconds</b>\nChange via: <code>/timeout 45</code>")

    def run(self):
        fb_urls = load_firebase_urls()
        print(f"🤖 Telegram Bot Server Running... (Connected Firebases: {len(fb_urls)} | Bot Token: {self.token[:8]}...)")
        while True:
            updates = self.get_updates()
            for update in updates:
                self.offset = update["update_id"] + 1
                try:
                    self.handle_message(update)
                except Exception as e:
                    print(f"Error handling Telegram update: {e}")
            time.sleep(1)


def main():
    if os.path.exists(TELEGRAM_CONFIG_FILE):
        try:
            with open(TELEGRAM_CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                token = cfg.get("bot_token")
        except Exception:
            token = ""
    else:
        token = ""

    if not token:
        print("\n================ TELEGRAM BOT SETUP ================")
        token = input("Enter your Telegram Bot Token (from @BotFather): ").strip()
        chat_id = input("Enter your Telegram Chat ID (from @userinfobot): ").strip()
        if token:
            with open(TELEGRAM_CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump({"bot_token": token, "chat_id": chat_id}, f, indent=2)
            print("Telegram config saved to telegram_config.json!")

    if not token:
        print("❌ No Bot Token provided. Aborting.")
        return

    bot = TelegramBot(token)
    bot.run()


if __name__ == "__main__":
    main()
