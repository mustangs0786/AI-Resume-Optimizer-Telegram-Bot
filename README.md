# 🤖 AI Resume Optimizer — Telegram Bot

> **Tailor your resume to any job in minutes. Right inside Telegram. No app. No sign-up.**

Built with Google Gemini API · python-telegram-bot · FPDF2 · Selenium · pdfplumber

---

## ✨ What It Does

| Feature | Description |
|---|---|
| 📄 **Resume Upload** | Upload once — saved to your profile forever |
| 🔍 **Job Discovery** | Finds FAANG / Big Tech / Unicorn roles in your city, ranked by match |
| 🔗 **Job Link** | Paste any job URL — it scrapes, analyzes, rewrites |
| 🤖 **Match Analysis** | Scores your resume 0–100 with gap breakdown |
| ✍️ **Smart Rewrite** | Tailors your resume using REUSE + STAR framework |
| 📊 **ATS Check** | Scores parse rate, keywords, formatting — auto-fixes issues |
| 🎙️ **Voice Notes** | Answer questions or describe updates by voice — auto-transcribed |
| 🔄 **Resume Update** | Add a new job, cert, or promotion — merged + rewritten instantly |

---

## 🧠 How It Works

```
/start
  └── Saved resume found? → Use / View / Upload new
  └── New user? → Upload resume

Choose path:
  ├── ✏️  Update My Resume   → Flash merges → Preview → Pro rewrites → PDF
  ├── 🔍 Looking for a Job  → Role → City → Exp → Time → Ranked job list
  └── 🔗 I Have a Job Link  → Scrape → Analyze → Questions → Rewrite → PDF
                                                                   ↓
                                                         ATS Check (opt-in)
                                                         Auto-fix if needed
```

---

## 🏗️ Model Tiers

| Model | Variable | Used For |
|---|---|---|
| `gemini-3.1-pro-preview` | `PRO_MODEL` | Resume rewrite only — max quality |
| `gemini-flash-latest` | `FLASH_PRO` | ATS fix, resume merge — high quality, fast |
| `gemini-flash-lite-latest` | `FLASH_MODEL` | Analysis, questions, ranking, transcription |

---

## 📁 Project Structure

```
Resume_Builder/
├── bot.py              # Telegram bot — all handlers, conversation states
├── prompts.py          # All LLM prompts (REUSE + STAR + ATS rules baked in)
├── resume_pdf.py       # PDF generator with validation layer
├── parser.py           # Gemini File API resume parser
├── scraper.py          # Job URL scraper
├── ats_checker.py      # ATS scorer — Jobswagon → Gemini fallback
├── job_fetcher.py      # LinkedIn guest API job fetcher
├── agent.py            # CLI pipeline (standalone)
├── main.py             # CLI entry point
├── fonts/              # DejaVu fonts for PDF rendering
├── temp_resumes/       # Uploaded originals (auto-created)
├── output/             # Generated PDFs (auto-created)
└── user_profiles/      # Per-user saved resumes (auto-created)
```

---

## ⚙️ Setup

### 1. Clone the repo

```bash
git clone https://github.com/YOUR_USERNAME/resume-builder-bot.git
cd resume-builder-bot
```

### 2. Install dependencies

```bash
# Using uv (recommended)
uv pip install python-telegram-bot google-genai python-dotenv \
               selenium webdriver-manager fpdf2 pdfplumber \
               beautifulsoup4 requests

# Or using pip
pip install python-telegram-bot google-genai python-dotenv \
            selenium webdriver-manager fpdf2 pdfplumber \
            beautifulsoup4 requests
```

### 3. Create your `.env` file

```bash
cp .env.example .env
```

Edit `.env`:

```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
GEMINI_API_KEY=your_gemini_api_key_here
```

### 4. Add fonts

Download DejaVu fonts and place in `fonts/`:

```
fonts/
├── DejaVuSans.ttf
├── DejaVuSans-Bold.ttf
└── DejaVuSans-Oblique.ttf
```

> Download from [dejavu-fonts.github.io](https://dejavu-fonts.github.io/)

### 5. Run the bot

```bash
python bot.py
```

---

## 📱 Telegram Bot Setup (Step by Step)

### Step 1 — Create your bot with BotFather

1. Open Telegram and search **@BotFather**
2. Send `/newbot`
3. Enter a name: e.g. `AI Resume Optimizer`
4. Enter a username: e.g. `my_resume_optimizer_bot` *(must end in `bot`)*
5. BotFather gives you a token like:
   ```
   8706051751:A********Tw
   ```
6. Copy this into your `.env` as `TELEGRAM_BOT_TOKEN`

### Step 2 — Get your Gemini API key

1. Go to [aistudio.google.com](https://aistudio.google.com)
2. Click **Get API Key** → **Create API Key**
3. Copy into your `.env` as `GEMINI_API_KEY`

### Step 3 — Run and find your bot

```bash
python bot.py
```

You'll see:
```
Bot commands registered.
Bot is running... Press Ctrl+C to stop.
```

Open Telegram → search `@your_bot_username` → tap **Start**

### Step 4 — Verify it's working

```bash
# Quick test — prints your bot's username
python -c "
import os
from dotenv import load_dotenv
import urllib.request, json
load_dotenv()
token = os.getenv('TELEGRAM_BOT_TOKEN')
res = json.loads(urllib.request.urlopen(f'https://api.telegram.org/bot{token}/getMe').read())
print(f'Bot: @{res[\"result\"][\"username\"]}')
"
```

---

## 🌐 Running on a Server (Optional)

To keep the bot running 24/7:

```bash
# Using screen
screen -S resume-bot
python bot.py
# Ctrl+A then D to detach

# Or using systemd (create /etc/systemd/system/resume-bot.service)
# Or using pm2
pm2 start bot.py --interpreter python3
```

---

## 🔑 Environment Variables

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | ✅ | From @BotFather |
| `GEMINI_API_KEY` | ✅ | From Google AI Studio |

---

## 📌 Bot Commands

| Command | Description |
|---|---|
| `/start` | Main menu — shows saved resume options |
| `/my_resume` | Download your saved resume versions |
| `/new` | Clear current session |
| `/cancel` | Cancel active flow |
| `/help` | Help and all commands |

---

## 🚧 Roadmap

- [ ] Batch optimization — select multiple jobs, get PDFs for all
- [ ] Claude desktop integration — auto-apply to jobs
- [ ] Cover letter generator
- [ ] Interview prep mode

---

## 🛠️ Built With

- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot)
- [Google Gemini API](https://ai.google.dev/)
- [FPDF2](https://pyfpdf.github.io/fpdf2/)
- [pdfplumber](https://github.com/jsvine/pdfplumber)
- [Selenium](https://selenium-python.readthedocs.io/)
- [BeautifulSoup4](https://www.crummy.com/software/BeautifulSoup/)

---

## 📄 License

MIT License — do whatever you want with it.

---

---

*Built by [Deepak Kumar](https://linkedin.com/in/mustangs007)*
