import asyncio
import os
import re
import subprocess
import sys
import time
from urllib.parse import urlparse
from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

# Environment Variables-ൽ നിന്നാണ് എടുക്കുന്നത് (കോഡിൽ നേരിട്ട് ടോക്കൺ/ഹാഷ് വയ്ക്കരുത്)
API_ID = int(os.environ.get("API_ID", "0").strip() or "0")
API_HASH = os.environ.get("API_HASH", "").strip()
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", "./downloads").strip()
THUMB_DIR = os.environ.get("THUMB_DIR", "./thumbnails").strip()
COOKIE_FILE = os.environ.get("COOKIE_FILE", "cookies.txt").strip()
AIRTEL_BEARER_TOKEN = os.environ.get("AIRTEL_BEARER_TOKEN", "").strip()

if not API_ID or not API_HASH or not BOT_TOKEN:
  raise RuntimeError(
      "API_ID, API_HASH, BOT_TOKEN എന്നിവ Environment Variables ആയി സെറ്റ്"
      " ചെയ്യുക!"
  )

app = Client(
    name="airtel_xstream_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
)

# യൂസറുടെ ഡാറ്റ, സെറ്റിംഗ്സ്, പെൻഡിംഗ് ജോബുകൾ, ക്യാൻസൽ റിക്വസ്റ്റുകൾ
USER_SHOWS: dict[int, dict] = {}
USER_SETTINGS: dict[int, dict] = {}
PENDING_JOBS: dict[str, dict] = {}
CANCEL_REQUESTS: set[int] = set()

RANGE_PATTERN = re.compile(r"^/(\d+):(\d+)(?:-(\d+))?$")

QUALITY_OPTIONS = {
    "144p": ("144p", "bestvideo[height<=144]+bestaudio/best[height<=144]/best"),
    "240p": ("240p", "bestvideo[height<=240]+bestaudio/best[height<=240]/best"),
    "360p": ("360p", "bestvideo[height<=360]+bestaudio/best[height<=360]/best"),
    "480p": ("480p", "bestvideo[height<=480]+bestaudio/best[height<=480]/best"),
    "720p": ("720p", "bestvideo[height<=720]+bestaudio/best[height<=720]/best"),
    "1080p": (
        "1080p",
        "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
    ),
    "mp3": ("MP3 Audio", "bestaudio/best"),
}


def get_user_settings(user_id: int) -> dict:
  if user_id not in USER_SETTINGS:
    USER_SETTINGS[user_id] = {
        "auto_thumb": True,
        "custom_thumb": None,
        "custom_name": None,
        "mp3_quality": "192K",
    }
  return USER_SETTINGS[user_id]


def build_settings_keyboard(user_id: int) -> InlineKeyboardMarkup:
  s = get_user_settings(user_id)
  auto_thumb_label = (
      "✅ Auto Thumb: ON" if s["auto_thumb"] else "❌ Auto Thumb: OFF"
  )
  custom_thumb_label = (
      "🖼️ Custom Thumb: Set"
      if s["custom_thumb"]
      else "🖼️ Custom Thumb: None"
  )
  custom_name_label = (
      f"✏️ Name: {s['custom_name']}"
      if s["custom_name"]
      else "✏️ Custom Name: Default"
  )
  mp3_label = f"🎵 MP3 Bitrate: {s['mp3_quality']}"
  return InlineKeyboardMarkup([
      [
          InlineKeyboardButton(
              auto_thumb_label, callback_data="set|toggle_autothumb"
          )
      ],
      [
          InlineKeyboardButton(
              custom_thumb_label, callback_data="set|clear_thumb"
          )
      ],
      [
          InlineKeyboardButton(
              custom_name_label, callback_data="set|clear_name"
          )
      ],
      [InlineKeyboardButton(mp3_label, callback_data="set|toggle_mp3")],
      [InlineKeyboardButton("🔍 Check Cookies", callback_data="set|cookies")],
  ])


def format_settings_text(user_id: int) -> str:
  s = get_user_settings(user_id)
  show = USER_SHOWS.get(user_id)
  show_txt = show["slug"] if show else "None"
  return (
      "⚙️ **Airtel Bot Settings**\n\n"
      f"• **Active Show:** `{show_txt}`\n"
      f"• **Auto Thumbnail:** `{'ON' if s['auto_thumb'] else 'OFF'}`\n"
      f"• **Custom Thumbnail:** `{'Set' if s['custom_thumb'] else 'Not set'}`"
      " (ഫോട്ടോ അയച്ചാൽ തംബ്നെയിൽ ആയി സെറ്റ് ആകും)\n"
      f"• **Custom Name:** `{s['custom_name'] or 'Default'}` (ലിങ്കിനൊപ്പം `-n"
      " പേര്` എന്ന് അയച്ചും സെറ്റ് ചെയ്യാം)\n"
      f"• **MP3 Bitrate:** `{s['mp3_quality']}`\n"
      f"• **Download Dir:** `{DOWNLOAD_DIR}`"
  )


def build_quality_keyboard(job_id: str, user_id: int, selected=None) -> InlineKeyboardMarkup:
  selected = set(selected or [])
  rows = []
  keys = ["144p", "240p", "360p", "480p", "720p", "1080p"]
  for i in range(0, len(keys), 3):
    rows.append([
        InlineKeyboardButton(
            ("✅ " if key in selected else "") + key,
            callback_data=f"pick|{job_id}|{key}",
        ) for key in keys[i:i + 3]
    ])
  rows.append([InlineKeyboardButton(
      ("✅ " if "mp3" in selected else "") + "🎵 MP3 Audio",
      callback_data=f"pick|{job_id}|mp3",
  )])
  rows.append([
      InlineKeyboardButton("❌ Cancel Task", callback_data=f"cancel_job|{job_id}|{user_id}"),
      InlineKeyboardButton("Submit", callback_data=f"submit|{job_id}"),
  ])
  return InlineKeyboardMarkup(rows)


def check_cookies_file(cookie_path: str) -> tuple[bool, str]:
  if not cookie_path:
    return False, "COOKIE_FILE പാത്ത് നൽകിയിട്ടില്ല."
  if os.path.isdir(cookie_path):
    return (
        False,
        f"`{cookie_path}` ഒരു ഫോൾഡർ ആയി കിടക്കുന്നു! അതൊരു ഫയൽ ആയിരിക്കണം.",
    )
  if not os.path.exists(cookie_path):
    return False, f"`{cookie_path}` ഫയൽ കണ്ടെത്താൻ കഴിഞ്ഞില്ല."
  size = os.path.getsize(cookie_path)
  if size == 0:
    return (
        False,
        f"`{cookie_path}` ഫയൽ കാലിയാണ് (0 bytes). കുക്കീസ് പേസ്റ്റ് ചെയ്യുക.",
    )
  return True, f"`{cookie_path}` ഫയൽ ലഭ്യമാണ് ({size} bytes)."


def extract_airtel_info(url: str) -> dict | None:
  cleaned = url.strip().rstrip(".,)")
  parsed = urlparse(cleaned)
  host = parsed.netloc.lower()
  if parsed.scheme not in ("http", "https") or (
      "airtelxstream.in" not in host and "airtel.in" not in host
  ):
    return None
  parts = [p for p in parsed.path.split("/") if p]
  slug = parts[1] if len(parts) >= 2 else (parts[0] if parts else "airtel_media")
  content_id = parts[2] if len(parts) >= 3 else None
  return {
      "url": cleaned,
      "category": parts[0] if parts else "stream",
      "slug": slug,
      "content_id": content_id,
  }


def parse_episode_command(text: str) -> dict | None:
  match = RANGE_PATTERN.match(text.strip())
  if not match:
    return None
  season = int(match.group(1))
  start_ep = int(match.group(2))
  end_ep = int(match.group(3)) if match.group(3) else start_ep
  if season < 1 or start_ep < 1 or end_ep < start_ep:
    return None
  return {
      "season": season,
      "episodes": list(range(start_ep, end_ep + 1)),
  }


def generate_thumbnail(video_path: str, user_id: int) -> str | None:
  os.makedirs(THUMB_DIR, exist_ok=True)
  thumb_path = os.path.join(THUMB_DIR, f"auto_{user_id}.jpg")
  try:
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        "00:00:05",
        "-i",
        video_path,
        "-vframes",
        "1",
        "-q:v",
        "2",
        thumb_path,
    ]
    subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
    )
    if os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 0:
      return thumb_path
  except Exception:
    pass
  return None


def run_yt_dlp(
    url: str, out_file: str, quality_key: str, mp3_bitrate: str = "192K"
) -> tuple[bool, str]:
  os.makedirs(os.path.dirname(out_file), exist_ok=True)

  cookie_ok, cookie_msg = check_cookies_file(COOKIE_FILE)
  if not cookie_ok:
    return False, f"കുക്കീസ് ചെക്ക് പരാജയപ്പെട്ടു: {cookie_msg}"

  _, fmt_selector = QUALITY_OPTIONS.get(quality_key, QUALITY_OPTIONS["720p"])

  cmd = [
      "yt-dlp",
      "--no-warnings",
      "-f",
      fmt_selector,
      "--add-header",
      "Referer: https://www.airtelxstream.in/",
      "--add-header",
      "Origin: https://www.airtelxstream.in",
      "--add-header",
      (
          "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
          " AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0"
          " Safari/537.36"
      ),
      "-o",
      out_file,
  ]

  if quality_key == "mp3":
    cmd.extend([
        "-x",
        "--audio-format",
        "mp3",
        "--audio-quality",
        mp3_bitrate,
    ])
  else:
    cmd.extend([
        "--merge-output-format",
        "mp4",
    ])

  if COOKIE_FILE and os.path.exists(COOKIE_FILE):
    cmd.extend(["--cookies", COOKIE_FILE])

  if AIRTEL_BEARER_TOKEN:
    cmd.extend(["--add-header", f"Authorization: Bearer {AIRTEL_BEARER_TOKEN}"])

  cmd.append(url)

  try:
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if res.returncode == 0:
      return True, "പൂർത്തിയായി"
    err = (res.stderr or res.stdout or "ഡൗൺലോഡ് പരാജയപ്പെട്ടു").strip()
    return False, err[-400:]
  except FileNotFoundError:
    return False, "`yt-dlp` ഇൻസ്റ്റാൾ ചെയ്തിട്ടില്ല."
  except subprocess.TimeoutExpired:
    return False, "സമയപരിധി കഴിഞ്ഞു (Timeout)."


async def create_and_send_quality_menu(
    message: Message, user_id: int, show: dict, season: int, episodes: list[int]
):
  job_id = f"{user_id}_{message.id}_{int(time.time())}"
  PENDING_JOBS[job_id] = {
      "user_id": user_id,
      "show": show,
      "season": season,
      "episodes": episodes,
      "selected": set(),
  }
  ep_label = (
      f"E{episodes[0]}" if len(episodes) == 1 else f"E{episodes[0]}–E{episodes[-1]}"
  )
  await message.reply_text(
      f"🎬 **Airtel Link Detected:** `{show['slug']}`\n"
      f"📺 **Season {season}** ({ep_label})\n\n"
      "ക്വാളിറ്റി അല്ലെങ്കിൽ **MP3 Audio** തിരഞ്ഞെടുക്കുക:",
      reply_markup=build_quality_keyboard(job_id, user_id),
  )


@app.on_message(filters.command("start") & filters.private)
async def start_cmd(_, message: Message):
  await message.reply_text(
      "ഹായ്, ഞാൻ Airtel വീഡിയോ ഡൗൺലോഡ് ബോട്ടാണ്!\n\n"
      "📌 **എങ്ങനെ ഉപയോഗിക്കാം:**\n"
      "• Airtel ലിങ്ക് അയച്ചാൽ ഓട്ടോമാറ്റിക്കായി ഡിറ്റക്ട് ചെയ്ത് ക്വാളിറ്റി"
      " ബട്ടൺസ് വരും.\n"
      "• ഷോ ലിങ്ക് സേവ് ചെയ്ത ശേഷം `/1:1-10` (Season 1, Ep 1–10) അല്ലെങ്കിൽ"
      " `/1:5` (Season 1, Ep 5) എന്ന് അയക്കാം.\n"
      "• ലിങ്കിനൊപ്പം `-n പേര്` ചേർത്ത് കസ്റ്റം പേര് നൽകാം.\n\n"
      "⚙️ **കമാൻഡുകൾ:**\n"
      "• `/start` — സ്വാഗത സന്ദേശം\n"
      "• `/settings` — ബോട്ട് സെറ്റിംഗ്സ്\n"
      "• `/restart` — ബോട്ട് റീസ്റ്റാർട്ട് ചെയ്യാൻ\n"
      "• `/checkcookies` — `cookies.txt` പരിശോധിക്കാൻ"
  )


@app.on_message(filters.command("settings") & filters.private)
async def settings_cmd(_, message: Message):
  user_id = message.from_user.id if message.from_user else 0
  await message.reply_text(
      format_settings_text(user_id),
      reply_markup=build_settings_keyboard(user_id),
  )


@app.on_message(filters.command("restart") & filters.private)
async def restart_cmd(_, message: Message):
  await message.reply_text("🔄 ബോട്ട് റീസ്റ്റാർട്ട് ചെയ്യുന്നു...")
  os.execv(sys.executable, [sys.executable] + sys.argv)


@app.on_message(filters.command("checkcookies") & filters.private)
async def check_cookies_cmd(_, message: Message):
  ok, msg = check_cookies_file(COOKIE_FILE)
  icon = "✅" if ok else "❌"
  await message.reply_text(f"{icon} {msg}")


@app.on_message(filters.photo & filters.private)
async def photo_thumb_handler(_, message: Message):
  user_id = message.from_user.id if message.from_user else 0
  os.makedirs(THUMB_DIR, exist_ok=True)
  thumb_path = os.path.join(THUMB_DIR, f"custom_{user_id}.jpg")
  await message.download(file_name=thumb_path)
  s = get_user_settings(user_id)
  s["custom_thumb"] = thumb_path
  await message.reply_text("✅ Custom Thumbnail സേവ് ചെയ്തു!")


@app.on_message(filters.text & filters.private)
async def text_handler(_, message: Message):
  text = (message.text or "").strip()
  user_id = message.from_user.id if message.from_user else 0
  s = get_user_settings(user_id)

  # ലിങ്ക് ഓട്ടോ-ഡിറ്റക്ഷൻ (Airtel ലിങ്ക് ആണോ എന്ന് പരിശോധിക്കുന്നു)
  url_match = re.search(r"https?://[^\s]+", text)
  if url_match:
    raw_url = url_match.group(0)
    info = extract_airtel_info(raw_url)
    if not info:
      await message.reply_text("❌ സാധുവായ Airtel ലിങ്ക് അയക്കുക.")
      return

    name_match = re.search(
        r"(?:^|\s)-n(?:\s+|=)(.+?)\s*$", text, flags=re.IGNORECASE
    )
    if name_match:
      clean_name = re.sub(
          r'[\\/:*?"<>|]+', "_", name_match.group(1).strip()
      ).strip()
      s["custom_name"] = clean_name or None

    USER_SHOWS[user_id] = info
    await create_and_send_quality_menu(
        message, user_id, info, season=1, episodes=[1]
    )
    return

  # /season:start-end ഫോർമാറ്റ് പരിശോധിക്കുന്നു
  parsed = parse_episode_command(text)
  if parsed:
    show = USER_SHOWS.get(user_id)
    if not show:
      await message.reply_text(
          "⚠️ ആദ്യം Airtel ഷോ ലിങ്ക് അയക്കുക, എന്നിട്ട് `/1:1-10` പോലെ അയക്കുക."
      )
      return

    cookie_ok, cookie_msg = check_cookies_file(COOKIE_FILE)
    if not cookie_ok:
      await message.reply_text(f"❌ {cookie_msg}")
      return

    await create_and_send_quality_menu(
        message, user_id, show, parsed["season"], parsed["episodes"]
    )
    return

  await message.reply_text(
      "Airtel ലിങ്ക് അല്ലെങ്കിൽ `/1:1-10` / `/1:5` എന്ന ഫോർമാറ്റിൽ കമാൻഡ് അയക്കുക."
  )


@app.on_callback_query(filters.regex(r"^set\|"))
async def settings_callback(_, callback: CallbackQuery):
  user_id = callback.from_user.id if callback.from_user else 0
  s = get_user_settings(user_id)
  action = callback.data.split("|", 1)[1]

  if action == "toggle_autothumb":
    s["auto_thumb"] = not s["auto_thumb"]
    await callback.answer(
        f"Auto Thumbnail: {'ON' if s['auto_thumb'] else 'OFF'}"
    )
  elif action == "clear_thumb":
    if s["custom_thumb"] and os.path.exists(s["custom_thumb"]):
      try:
        os.remove(s["custom_thumb"])
      except OSError:
        pass
    s["custom_thumb"] = None
    await callback.answer("Custom Thumbnail നീക്കം ചെയ്തു.")
  elif action == "clear_name":
    s["custom_name"] = None
    await callback.answer("Custom Name റീസെറ്റ് ചെയ്തു.")
  elif action == "toggle_mp3":
    s["mp3_quality"] = "320K" if s["mp3_quality"] == "192K" else "192K"
    await callback.answer(f"MP3 Bitrate: {s['mp3_quality']}")
  elif action == "cookies":
    ok, msg = check_cookies_file(COOKIE_FILE)
    await callback.answer(msg, show_alert=True)
    return

  await callback.message.edit_text(
      format_settings_text(user_id),
      reply_markup=build_settings_keyboard(user_id),
  )


@app.on_callback_query(filters.regex(r"^cancel_job\|"))
async def cancel_job_callback(_, callback: CallbackQuery):
  parts = callback.data.split("|")
  job_id = parts[1] if len(parts) > 1 else ""
  target_uid = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
  req_uid = callback.from_user.id if callback.from_user else 0
  if req_uid != target_uid:
    await callback.answer("ഇത് നിങ്ങളുടെ റിക്വസ്റ്റ് അല്ല.", show_alert=True)
    return
  CANCEL_REQUESTS.add(req_uid)
  PENDING_JOBS.pop(job_id, None)
  await callback.message.edit_text("❌ ടാസ്ക് ക്യാൻസൽ ചെയ്തു.")
  await callback.answer("Cancelled")


if __name__ == "__main__":
  app.run()
