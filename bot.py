import asyncio
import os
import re
import subprocess
from urllib.parse import urlparse
from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

# Environment Variables-ൽ നിന്നാണ് എടുക്കുന്നത് (കോഡിൽ നേരിട്ട് ടോക്കൺ/ഹാഷ് വയ്ക്കരുത്)
API_ID = int(os.environ.get("API_ID", "0").strip() or "0")
API_HASH = os.environ.get("API_HASH", "").strip()
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", "./downloads").strip()
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

# യൂസറുടെ അവസാനത്തെ ഷോ ലിങ്ക്, പെൻഡിംഗ് ക്യൂ എന്നിവ സൂക്ഷിക്കാൻ
USER_SHOWS: dict[int, dict] = {}
PENDING_JOBS: dict[str, dict] = {}

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


def build_quality_keyboard(job_id: str) -> InlineKeyboardMarkup:
  return InlineKeyboardMarkup([
      [
          InlineKeyboardButton("144p", callback_data=f"dl|{job_id}|144p"),
          InlineKeyboardButton("240p", callback_data=f"dl|{job_id}|240p"),
          InlineKeyboardButton("360p", callback_data=f"dl|{job_id}|360p"),
      ],
      [
          InlineKeyboardButton("480p", callback_data=f"dl|{job_id}|480p"),
          InlineKeyboardButton("720p", callback_data=f"dl|{job_id}|720p"),
          InlineKeyboardButton("1080p", callback_data=f"dl|{job_id}|1080p"),
      ],
      [
          InlineKeyboardButton("🎵 MP3 Audio", callback_data=f"dl|{job_id}|mp3"),
      ],
  ])


def check_cookies_file(cookie_path: str) -> tuple[bool, str]:
  """
    cookies.txt ഫയൽ നിലവിലുണ്ടോ, ഫയൽ തന്നെയാണോ, കാലിയല്ലയോ എന്ന് പരിശോധിക്കുന്നു.
    """
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


def extract_xstream_info(url: str) -> dict | None:
  parsed = urlparse(url.strip())
  if parsed.scheme not in ("http", "https") or "airtelxstream.in" not in parsed.netloc:
    return None
  parts = [p for p in parsed.path.split("/") if p]
  if len(parts) < 3:
    return None
  return {
      "url": url.strip(),
      "category": parts[0],
      "slug": parts[1],
      "content_id": parts[2],
      "tail": parts[3] if len(parts) > 3 else None,
  }


def parse_episode_command(text: str) -> dict | None:
  """
    /1:1-10 -> season=1, episodes=[1..10]
    /1:5    -> season=1, episodes=[5]
    """
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


def run_yt_dlp(url: str, out_file: str, quality_key: str) -> tuple[bool, str]:
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
        "192K",
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


@app.on_message(filters.command("start") & filters.private)
async def start_cmd(_, message: Message):
  await message.reply_text(
      "നമസ്കാരം!\n"
      "1. ആദ്യം Airtel Xstream ഷോ ലിങ്ക് അയക്കുക.\n"
      "2. പിന്നാലെ സീസണും എപ്പിസോഡും അയക്കുക:\n"
      "   • `/1:1-10` → Season 1-ലെ 1 മുതൽ 10 വരെ എപ്പിസോഡുകൾ\n"
      "   • `/1:5` → Season 1-ലെ 5-ാമത്തെ എപ്പിസോഡ് മാത്രം\n"
      "3. ബട്ടണിൽ നിന്ന് `144p`–`1080p` അല്ലെങ്കിൽ `MP3 Audio` തിരഞ്ഞെടുക്കുക.\n"
      "4. `/checkcookies` → `cookies.txt` ഉണ്ടോ എന്ന് പരിശോധിക്കാൻ"
  )


@app.on_message(filters.command("checkcookies") & filters.private)
async def check_cookies_cmd(_, message: Message):
  ok, msg = check_cookies_file(COOKIE_FILE)
  icon = "✅" if ok else "❌"
  await message.reply_text(f"{icon} {msg}")


@app.on_message(filters.text & filters.private)
async def text_handler(_, message: Message):
  text = (message.text or "").strip()
  user_id = message.from_user.id if message.from_user else 0

  # ലിങ്ക് ആണോ എന്ന് പരിശോധിക്കുന്നു
  if text.startswith(("http://", "https://")):
    info = extract_xstream_info(text)
    if not info:
      await message.reply_text("❌ സാധുവായ Airtel Xstream ലിങ്ക് അയക്കുക.")
      return
    USER_SHOWS[user_id] = info
    await message.reply_text(
        f"✅ ഷോ സേവ് ചെയ്തു: `{info['slug']}`\n"
        "ഇനി `/1:1-10` അല്ലെങ്കിൽ `/1:5` പോലെ അയക്കുക."
    )
    return

  # /season:start-end ഫോർമാറ്റ് പരിശോധിക്കുന്നു
  parsed = parse_episode_command(text)
  if parsed:
    show = USER_SHOWS.get(user_id)
    if not show:
      await message.reply_text(
          "⚠️ ആദ്യം ഷോ ലിങ്ക് അയക്കുക, എന്നിട്ട് `/1:1-10` പോലെ അയക്കുക."
      )
      return

    cookie_ok, cookie_msg = check_cookies_file(COOKIE_FILE)
    if not cookie_ok:
      await message.reply_text(f"❌ {cookie_msg}")
      return

    job_id = f"{user_id}_{message.id}"
    PENDING_JOBS[job_id] = {
        "user_id": user_id,
        "show": show,
        "season": parsed["season"],
        "episodes": parsed["episodes"],
    }

    ep_label = (
        f"E{parsed['episodes'][0]}"
        if len(parsed["episodes"]) == 1
        else f"E{parsed['episodes'][0]}–E{parsed['episodes'][-1]}"
    )

    await message.reply_text(
        f"🎬 **Show:** `{show['slug']}`\n"
        f"📺 **Season {parsed['season']}** ({ep_label})\n\n"
        "ക്വാളിറ്റി അല്ലെങ്കിൽ **MP3 Audio** തിരഞ്ഞെടുക്കുക:",
        reply_markup=build_quality_keyboard(job_id),
    )
    return

  await message.reply_text(
      "ലിങ്ക് അല്ലെങ്കിൽ `/1:1-10` / `/1:5` എന്ന ഫോർമാറ്റിൽ കമാൻഡ് അയക്കുക."
  )


@app.on_callback_query(filters.regex(r"^dl\|"))
async def quality_callback(_, callback: CallbackQuery):
  try:
    _, job_id, quality_key = callback.data.split("|", 2)
  except ValueError:
    await callback.answer("Invalid selection", show_alert=True)
    return

  job = PENDING_JOBS.get(job_id)
  if not job:
    await callback.answer(
        "ഈ ഓപ്ഷൻ കാലഹരണപ്പെട്ടു. വീണ്ടും കമാൻഡ് അയക്കുക.", show_alert=True
    )
    return

  if callback.from_user and callback.from_user.id != job["user_id"]:
    await callback.answer(
        "ഇത് നിങ്ങളുടെ റിക്വസ്റ്റ് അല്ല.", show_alert=True
    )
    return

  if quality_key not in QUALITY_OPTIONS:
    await callback.answer("Unknown quality", show_alert=True)
    return

  q_label, _ = QUALITY_OPTIONS[quality_key]
  await callback.answer(f"{q_label} തിരഞ്ഞെടുത്തു...")

  show = job["show"]
  season = job["season"]
  episodes = job["episodes"]

  status_msg = await callback.message.edit_text(
      f"⏳ **Season {season}** — {len(episodes)} എപ്പിസോഡ് (`{q_label}`) "
      "ഡൗൺലോഡ് ചെയ്യുന്നു..."
  )

  results = []
  for ep in episodes:
    folder_tag = "MP3" if quality_key == "mp3" else quality_key
    ext_template = "%(ext)s" if quality_key == "mp3" else "mp4"
    out_path = os.path.join(
        DOWNLOAD_DIR,
        show["slug"],
        f"S{season:02d}",
        folder_tag,
        f"E{ep:02d}.{ext_template}",
    )
    ok, msg = await asyncio.to_thread(
        run_yt_dlp, show["url"], out_path, quality_key
    )
    icon = "✅" if ok else "❌"
    results.append(f"{icon} S{season}E{ep} [{q_label}]: {msg}")
    if len(results) % 3 == 0 or ep == episodes[-1]:
      await status_msg.edit_text("\n".join(results[-10:]))


if __name__ == "__main__":
  app.run()
