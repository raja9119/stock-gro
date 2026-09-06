#!/usr/bin/env python3
"""stock_firebase.py — Multi-Firebase & Telegram Integrated Automated Script.

- Multi-Firebase via firebases.txt: Paste all your Firebase Realtime DB URLs in firebases.txt at once!
- Live Telegram Notifications: Enter your Bot Token & Chat ID in telegram_config.json for instant alerts.
- Ultra-Fast Parallel OTP Fetcher: Scans all Firebases concurrently.
- Auto-saves registered account data to Firebase DBs & local accounts.json.
"""
import concurrent.futures
import json
import os
import random
import re
import time
from urllib.parse import unquote

import requests
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

# --- PATH CONFIGURATION ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ACCOUNTS_FILE = os.path.join(SCRIPT_DIR, "accounts.json")
FIREBASES_TXT_FILE = os.path.join(SCRIPT_DIR, "firebases.txt")
TELEGRAM_CONFIG_FILE = os.path.join(SCRIPT_DIR, "telegram_config.json")

# --- DEFAULT CONFIGURATION ---
DEFAULT_FIREBASE_URLS = [
    "https://dark-274b4-default-rtdb.firebaseio.com"
]
DEFAULT_REFERRAL = "YYX20ZK5"  # Default referral code
OTP_TIMEOUT_SECONDS = 15       # Fast 15-second timeout per number
SEEN_OTP_MSG_IDS = set()       # Track processed OTP messages so old OTPs are never reused

APP = "https://app.stockgro.club"
ACCOUNTS = "https://accounts.stockgro.club"
CLIENT_ID = "b711c4dd-7df5-42e6-80e6-d111c1255cd7"
UA = ("Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/151.0.0.0 Mobile Safari/537.36")

NAMES = ("Arjun", "Rohan", "Kabir", "Aryan", "Vivaan", "Aditya", "Ishaan",
         "Dhruv", "Reyansh", "Ananya")

console = Console()


def digits(s, n=10):
    d = re.sub(r"\D", "", str(s or ""))
    return d[-n:] if len(d) > n else d


def error_panel(title, msg):
    console.print(Panel(f"[red]{msg}[/red]",
                        title=f"[bold red]{title}[/bold red]",
                        border_style="red"))


# ---------------- Multi-Firebase File Loader ----------------

def load_firebase_urls():
    """Reads all Firebase URLs from firebases.txt (one per line)."""
    urls = []
    if os.path.exists(FIREBASES_TXT_FILE):
        try:
            with open(FIREBASES_TXT_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        u = line.rstrip('/')
                        if u.startswith("http") and u not in urls:
                            urls.append(u)
        except Exception:
            pass

    if not urls:
        urls = list(DEFAULT_FIREBASE_URLS)
        # Create default firebases.txt file
        try:
            with open(FIREBASES_TXT_FILE, "w", encoding="utf-8") as f:
                f.write("# Paste your Firebase Realtime Database URLs below (one per line):\n")
                for u in urls:
                    f.write(f"{u}\n")
        except Exception:
            pass

    return urls


# ---------------- Telegram Notification Helper ----------------

def load_telegram_config():
    if os.path.exists(TELEGRAM_CONFIG_FILE):
        try:
            with open(TELEGRAM_CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"bot_token": "", "chat_id": ""}


def save_telegram_config(token, chat_id):
    with open(TELEGRAM_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump({"bot_token": token, "chat_id": chat_id}, f, indent=2)


def send_telegram_notification(text):
    cfg = load_telegram_config()
    token = cfg.get("bot_token")
    chat_id = cfg.get("chat_id")
    if token and chat_id:
        try:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            requests.post(url, json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML"
            }, timeout=5)
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
    # 1. Save locally to accounts.json
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
    except Exception as e:
        console.print(f"[yellow]could not save local account: {e}[/yellow]")

    # 2. Save directly to all connected Firebases under /numbers/+91<phone>
    save_account_to_firebases(rec.get("phone"), rec)

    # 3. Send Telegram Notification if status is registered
    if rec.get("status") == "registered":
        tg_msg = (
            f"🚀 <b>StockGro Account Registered!</b>\n"
            f"📱 <b>Phone:</b> <code>{rec.get('phone')}</code>\n"
            f"👤 <b>Name:</b> {rec.get('name')}\n"
            f"🔑 <b>OTP:</b> <code>{rec.get('otp')}</code>\n"
            f"🆔 <b>User ID:</b> <code>{rec.get('user_id')}</code>\n"
            f"🎁 <b>Referral:</b> <code>{rec.get('referral')}</code>"
        )
        send_telegram_notification(tg_msg)


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
                        
                        # Timestamp must be fresh (created around or after createOtp) AND not already used for a previous number
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


def fetch_fast_otp_multi_firebase(phone, start_time_ms, timeout_seconds=OTP_TIMEOUT_SECONDS):
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


# ---------------- Registration Logic ----------------

def process_single_number(phone, referral=DEFAULT_REFERRAL, country="IN"):
    name = random.choice(NAMES) + str(random.randint(10, 99))
    console.print(f"\n[bold cyan]----------------------------------------[/bold cyan]")
    console.print(f"[bold cyan]Processing Number: {phone} | Referral: {referral}[/bold cyan]")
    
    sess = requests.Session()
    sess.headers.update({"User-Agent": UA})
    try:
        state, page = bootstrap(sess)
    except Exception as e:
        error_panel("BOOTSTRAP FAILED", str(e)[:200])
        save_account({"phone": phone, "status": "bootstrap_failed", "ts": int(time.time())})
        return "FAILED"

    try:
        ident = stockgro_identity(sess, state, page, phone, country)
    except Exception as e:
        error_panel("IDENTITY CHECK FAILED", str(e)[:200])
        save_account({"phone": phone, "status": "identity_check_failed", "ts": int(time.time())})
        return "FAILED"

    if ident.get("existing_user"):
        console.print(f"[yellow]Skipping {phone}: Already registered on StockGro[/yellow]")
        save_account({"phone": phone, "status": "already_registered", "user_id": ident.get("user_id"), "ts": int(time.time())})
        return "ALREADY_REGISTERED"

    try:
        request_time_ms = int(time.time() * 1000)
        session_id, sess, state, page = create_otp(sess, state, page, phone, referral, country)
    except Exception as e:
        error_panel("OTP REQUEST FAILED", str(e)[:200])
        save_account({"phone": phone, "status": "create_otp_failed", "ts": int(time.time())})
        return "FAILED"

    console.print(f"[green]OTP requested for {phone}. Waiting for fast-forward Firebase OTP...[/green]")

    with console.status(f"[bold yellow]⚡ Ultra-Fast Forward Polling (0.1s interval, {OTP_TIMEOUT_SECONDS}s timeout)...[/bold yellow]"):
        otp = fetch_fast_otp_multi_firebase(phone, request_time_ms, timeout_seconds=OTP_TIMEOUT_SECONDS)

    if not otp:
        console.print(f"[yellow]No OTP received for {phone} within {OTP_TIMEOUT_SECONDS}s. Auto-skipping to next number...[/yellow]")
        save_account({"phone": phone, "status": "otp_timeout", "ts": int(time.time())})
        return "FAILED"

    console.print(f"[bold green]⚡ AUTO-CAPTURED OTP: {otp}[/bold green]")

    try:
        validate_otp(sess, state, page, phone, otp, session_id, country)
        data = register_user(sess, state, page, phone, otp, session_id, referral, name, country)
        result = redeem_access_code(sess, data, page)
    except Exception as e:
        error_panel("REGISTRATION FAILED", str(e)[:200])
        save_account({"phone": phone, "status": "registration_failed", "otp": otp, "ts": int(time.time())})
        return "FAILED"

    save_account({"phone": phone, "status": "registered",
                  "user_id": result["user_id"], "otp": otp,
                  "logged_in": result["logged_in"], "referral": referral,
                  "name": name, "ts": int(time.time())})
    
    console.print(Panel(
        f"[bold green]SUCCESSFULLY REGISTERED & SAVED TO FIREBASE[/bold green]\n"
        f"Phone:    {phone}\n"
        f"User ID:  {result['user_id']}\n"
        f"Name:     {name}\n"
        f"Referral: {referral}",
        title="[bold green]ACCOUNT CREATED[/bold green]", border_style="green"))
    return "REGISTERED"


# ---------------- Actions ----------------

def action_configure_telegram():
    cfg = load_telegram_config()
    console.print(Panel("[bold cyan]Configure Telegram Notifications[/bold cyan]", border_style="cyan"))
    console.print(f"Current Bot Token: [dim]{cfg.get('bot_token') or 'Not Set'}[/dim]")
    console.print(f"Current Chat ID:   [dim]{cfg.get('chat_id') or 'Not Set'}[/dim]\n")
    
    token = Prompt.ask("Enter Telegram Bot Token (or press Enter to keep current)", default=cfg.get("bot_token")).strip()
    chat_id = Prompt.ask("Enter Telegram Chat ID / User ID (or press Enter to keep current)", default=cfg.get("chat_id")).strip()
    
    if token and chat_id:
        save_telegram_config(token, chat_id)
        console.print("[bold green]Telegram notification configuration saved! Sending test message...[/bold green]")
        send_telegram_notification("🤖 <b>StockGro Notifications Connected Successfully!</b>\nYou will receive live updates here.")


def action_manage_firebases():
    fb_urls = load_firebase_urls()
    while True:
        console.print("\n[bold cyan]Manage Connected Firebase DBs (Loaded from firebases.txt):[/bold cyan]")
        for idx, url in enumerate(fb_urls, 1):
            console.print(f"  [{idx}] {url}")
        console.print("[bold cyan]+[/bold cyan] Add New Firebase URL")
        console.print("[bold red]-[/bold red] Remove Firebase URL")
        console.print("[bold yellow]b[/bold yellow] Back to Main Menu")
        
        c = Prompt.ask("Choose option", default="b").strip().lower()
        if c == "b":
            break
        elif c == "+":
            new_u = Prompt.ask("Enter new Firebase DB URL").strip().rstrip('/')
            if new_u and new_u not in fb_urls:
                fb_urls.append(new_u)
                try:
                    with open(FIREBASES_TXT_FILE, "a", encoding="utf-8") as f:
                        f.write(f"\n{new_u}")
                except Exception:
                    pass
                console.print(f"[green]Added {new_u} to firebases.txt[/green]")
        elif c == "-":
            rm_idx = Prompt.ask("Enter number of URL to remove")
            try:
                idx_val = int(rm_idx) - 1
                if 0 <= idx_val < len(fb_urls):
                    removed = fb_urls.pop(idx_val)
                    try:
                        with open(FIREBASES_TXT_FILE, "w", encoding="utf-8") as f:
                            f.write("# Paste your Firebase Realtime Database URLs below (one per line):\n")
                            for u in fb_urls:
                                f.write(f"{u}\n")
                    except Exception:
                        pass
                    console.print(f"[yellow]Removed {removed}[/yellow]")
            except ValueError:
                pass


def action_batch_signup(referral):
    fb_urls = load_firebase_urls()
    console.print(f"[dim]Scanning {len(fb_urls)} connected Firebases for available numbers...[/dim]")
    fb_numbers = get_numbers_from_all_firebases()
    already_saved = load_registered_phones()
    pending_numbers = [n for n in fb_numbers if n not in already_saved]
    already_processed_before = len(fb_numbers) - len(pending_numbers)
    
    if not pending_numbers:
        console.print(Panel(
            f"[yellow]No new pending numbers found across all Firebase DBs![/yellow]\n"
            f"Total Numbers in Firebase: {len(fb_numbers)}\n"
            f"Already Processed previously: {already_processed_before}",
            border_style="yellow"))
        return

    console.print(Panel(
        f"[bold cyan]100% AUTOMATED BATCH SIGNUP MODE[/bold cyan]\n"
        f"Connected Firebases: [green]{len(fb_urls)}[/green]\n"
        f"Total Numbers Found: {len(fb_numbers)}\n"
        f"Previously Processed (Skipped): {already_processed_before}\n"
        f"Pending New Numbers to Process: [bold green]{len(pending_numbers)}[/bold green]\n"
        f"Referral Code: [bold cyan]{referral}[/bold cyan]\n"
        f"Fast-Forward Polling: [bold green]0.2s Interval[/bold green]",
        border_style="cyan"))

    send_telegram_notification(f"🚀 <b>Starting Automated Batch Signup for {len(pending_numbers)} numbers across {len(fb_urls)} Firebases!</b>")

    success_count = 0
    already_reg_count = 0
    failed_count = 0

    for idx, phone in enumerate(pending_numbers, 1):
        console.print(f"\n[dim]Progress: {idx}/{len(pending_numbers)}[/dim]")
        res = process_single_number(phone, referral)
        if res == "REGISTERED":
            success_count += 1
        elif res == "ALREADY_REGISTERED":
            already_reg_count += 1
        else:
            failed_count += 1
        time.sleep(1)

    total_rejections = already_processed_before + already_reg_count

    # --- FINAL BATCH SUMMARY REPORT ---
    summary_table = Table(title="[bold green]FINAL BATCH SIGNUP SUMMARY REPORT[/bold green]")
    summary_table.add_column("Category", style="cyan")
    summary_table.add_column("Count", style="bold white", justify="right")
    summary_table.add_column("Status Detail", style="dim")

    summary_table.add_row("Total Numbers in Firebase", str(len(fb_numbers)), "All numbers found across connected Firebases")
    summary_table.add_row("Newly Registered (DONE)", f"[bold green]{success_count}[/bold green]", "Accounts successfully created & saved to Firebase")
    summary_table.add_row("Total Rejections / Skipped", f"[bold yellow]{total_rejections}[/bold yellow]", f"({already_processed_before} saved previously + {already_reg_count} existing on StockGro)")
    summary_table.add_row("Failed / Timeout", f"[bold red]{failed_count}[/bold red]", "OTP timeout or API registration failure")

    console.print("\n")
    console.print(summary_table)
    console.print(Panel(
        f"[bold green]BATCH COMPLETED![/bold green]\n"
        f"✔ [bold green]{success_count} Done[/bold green] | ✖ [bold yellow]{total_rejections} Rejected/Existing[/bold yellow] | ⚠ [bold red]{failed_count} Failed[/bold red]\n"
        f"All details updated in Firebase DBs (/numbers) and [bold]{ACCOUNTS_FILE}[/bold]",
        border_style="green"))

    send_telegram_notification(
        f"✅ <b>Batch Completed!</b>\n"
        f"✔ Done: <b>{success_count}</b>\n"
        f"✖ Rejected/Existing: <b>{total_rejections}</b>\n"
        f"⚠ Failed: <b>{failed_count}</b>"
    )


def action_single_signup(referral):
    fb_numbers = get_numbers_from_all_firebases()
    if fb_numbers:
        console.print(f"[dim]Available numbers in Firebases: {', '.join(fb_numbers[:5])}...[/dim]")
    
    raw = Prompt.ask("Enter phone number (or press Enter to auto-pick first from Firebase)")
    if not raw.strip():
        if fb_numbers:
            phone = fb_numbers[0]
            console.print(f"[bold cyan]Auto-picked number: {phone}[/bold cyan]")
        else:
            error_panel("NO NUMBER", "No numbers available in Firebase to auto-pick.")
            return
    else:
        phone = digits(raw)

    if len(phone) != 10:
        error_panel("INVALID NUMBER", f"'{phone}' is not a 10-digit number")
        return

    process_single_number(phone, referral)


def action_view_accounts():
    if not os.path.exists(ACCOUNTS_FILE):
        console.print(Panel(f"[yellow]No saved accounts file found yet at:\n{ACCOUNTS_FILE}[/yellow]", border_style="yellow"))
        return
    
    try:
        with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
            accs = json.load(f)
    except Exception as e:
        error_panel("CORRUPT ACCOUNTS FILE", f"Could not read accounts file: {e}")
        return

    if not accs:
        console.print(Panel("[yellow]Accounts list is empty.[/yellow]", border_style="yellow"))
        return

    t = Table(title=f"Saved Accounts ({len(accs)} Total) — Local File & Firebase Synced")
    t.add_column("#", style="dim", width=4)
    t.add_column("Phone Number", style="bold cyan")
    t.add_column("User ID", style="dim")
    t.add_column("Referral Used")
    t.add_column("Status")

    newly_created = 0
    for idx, a in enumerate(accs, 1):
        st = str(a.get("status", "-"))
        if st == "registered":
            status_fmt = "[bold green]NEW REGISTERED[/bold green]"
            newly_created += 1
        elif st == "already_registered":
            status_fmt = "[yellow]EXISTS ON STOCKGRO[/yellow]"
        else:
            status_fmt = f"[dim]{st}[/dim]"

        t.add_row(
            str(idx),
            str(a.get("phone", "-")),
            str(a.get("user_id", "-")),
            str(a.get("referral", "-")),
            status_fmt
        )

    console.print(t)
    console.print(f"[bold green]Summary:[/bold green] {len(accs)} total accounts logged ({newly_created} newly created via bot).")


def main():
    global DEFAULT_REFERRAL, OTP_TIMEOUT_SECONDS
    fb_urls = load_firebase_urls()
    tg_cfg = load_telegram_config()
    tg_status = "[green]Enabled[/green]" if (tg_cfg.get("bot_token") and tg_cfg.get("chat_id")) else "[dim]Disabled[/dim]"

    console.print(Panel(
        "[bold cyan]STOCKGRO ULTRA-FAST AUTOMATED MULTI-FIREBASE BOT[/bold cyan]\n"
        f"Firebase List: [dim]{FIREBASES_TXT_FILE}[/dim] ([green]{len(fb_urls)} URLs loaded[/green])\n"
        f"Telegram Notifications: {tg_status}\n"
        f"Accounts File: [dim]{ACCOUNTS_FILE}[/dim]\n"
        f"Fast-Forward Polling: [yellow]0.2 Seconds[/yellow]\n"
        f"Default Referral Code: [bold green]{DEFAULT_REFERRAL}[/bold green]",
        border_style="blue", title="VS Code Automated Bot"))

    while True:
        console.print("\n[bold]Choose Action:[/bold]")
        console.print("[bold cyan]1[/bold cyan] Start 100% Automated Batch Signup (Process ALL Firebase numbers)")
        console.print("[bold cyan]2[/bold cyan] Single Auto Signup (Process 1 number)")
        console.print("[bold cyan]3[/bold cyan] Configure Telegram Notifications (Token & Chat ID)")
        console.print("[bold cyan]4[/bold cyan] Manage Firebase Database URLs (" + str(len(fb_urls)) + " connected)")
        console.print("[bold cyan]5[/bold cyan] Change Default Referral Code (Current: " + DEFAULT_REFERRAL + ")")
        console.print("[bold cyan]6[/bold cyan] Change OTP Wait Timeout (Current: " + str(OTP_TIMEOUT_SECONDS) + "s)")
        console.print("[bold cyan]7[/bold cyan] View Saved Accounts Table")
        console.print("[bold cyan]8[/bold cyan] Exit")
        
        choice = Prompt.ask("Action [1/2/3/4/5/6/7/8]", default="1").strip()
        
        if choice == "1":
            action_batch_signup(DEFAULT_REFERRAL)
        elif choice == "2":
            action_single_signup(DEFAULT_REFERRAL)
        elif choice == "3":
            action_configure_telegram()
        elif choice == "4":
            action_manage_firebases()
            fb_urls = load_firebase_urls()
        elif choice == "5":
            new_ref = Prompt.ask("Enter your new referral code").strip().upper()
            if new_ref:
                DEFAULT_REFERRAL = new_ref
                console.print(f"[green]Default referral code saved as: {DEFAULT_REFERRAL}[/green]")
        elif choice == "6":
            new_t = Prompt.ask("Enter OTP wait timeout in seconds", default=str(OTP_TIMEOUT_SECONDS)).strip()
            if new_t.isdigit():
                OTP_TIMEOUT_SECONDS = int(new_t)
                console.print(f"[green]OTP timeout set to {OTP_TIMEOUT_SECONDS} seconds[/green]")
        elif choice == "7":
            action_view_accounts()
        elif choice == "8":
            console.print("[yellow]Bye![/yellow]")
            return
        else:
            console.print("[red]Invalid choice![/red]")


if __name__ == "__main__":
    main()
