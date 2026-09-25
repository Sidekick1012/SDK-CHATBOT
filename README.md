# 🚀 Sidekick AI Chatbot & Lead Management Portal

Production-ready conversational AI chatbot and administrative leads portal built for **Sidekick Venture Private Limited (sidekick.pk)**. Powered by Google Gemini API with automatic key rotation, multi-tier model fallbacks, rate limiting, and an interactive admin dashboard.

---

## 🌟 Key Features

- **Multi-API Key Rotation**: Automatically rotates across multiple Gemini API keys if quota limits are encountered.
- **Model Fallback Cascade**: Tries high-speed models in sequence (`gemini-3.6-flash` ➔ `gemini-3.5-flash-lite` ➔ `gemini-3.5-flash` ➔ `gemini-2.5-flash-lite`).
- **Automated Lead Extraction**: Automatically captures phone numbers and emails directly from chat transcripts, with AI-driven service interest detection.
- **SQLite Concurrency (WAL Mode)**: Enabled with Write-Ahead Logging and 30-second busy timeouts to handle concurrent users without database locks.
- **Production WSGI Server**: Uses multi-threaded **Waitress** WSGI server (`threads=8`).
- **Interactive Admin Dashboard**:
  - Live leads count and session statistics
  - Date filtering (Today, Last 7 Days, Last 30 Days, Custom Date Range)
  - Live keyword search across contact, name, and interest
  - Full conversation transcript viewer modal
  - 1-Click CSV export
- **Security & Protection**:
  - IP-based rate limiting via `Flask-Limiter`
  - Session-based admin authentication
  - Configurable CORS origin filtering

---

## 📁 Project Structure

```text
Sidekick-Custom-ChatBot/
├── app.py                 # Core Flask backend & Waitress WSGI server
├── admin.html             # Admin Leads Portal single-page app
├── index.html             # Client-facing interactive chatbot interface
├── sidekick_chat.db       # SQLite database (auto-created on startup)
├── .env                   # Configuration & API keys (keep private)
├── requirements.txt       # Python dependencies
├── Dockerfile             # Container definition for Docker/Cloud Run
├── .dockerignore          # Docker build exclusions
├── Procfile               # PaaS deployment specification (Render/Railway)
├── sidekick.service       # Linux systemd daemon service template
└── nginx.conf.example     # Nginx reverse proxy & SSL config example
```

---

## ⚙️ Environment Configuration (`.env`)

Configure the following variables in `.env`:

```env
# Google Gemini API Keys (comma-separated for auto-rotation)
GEMINI_API_KEY=YOUR_GEMINI_KEY_1,YOUR_GEMINI_KEY_2

# Admin Portal Credentials
ADMIN_USERNAME=admin
ADMIN_PASSWORD=your_secure_password_here

# Secret session key (random secure string)
SECRET_KEY=generate_a_random_secret_string

# Port (default 5000)
PORT=5000

# CORS settings (* for all, or https://sidekick.pk)
CORS_ORIGINS=*
```

---

## 🏃 Local Run

1. **Activate virtual environment & install dependencies:**
   ```bash
   python -m venv venv
   # Windows:
   venv\Scripts\activate
   # Linux/Mac:
   source venv/bin/activate

   pip install -r requirements.txt
   ```

2. **Start the application:**
   ```bash
   python app.py
   ```

3. **Access points:**
   - Chatbot UI: `http://localhost:5000/`
   - Admin Leads Portal: `http://localhost:5000/admin`
   - Health check: `http://localhost:5000/health`

---

## 🚢 Production Deployment Options

### Option 1: Docker
```bash
docker build -t sidekick-chatbot .
docker run -d -p 5000:5000 --env-file .env -v $(pwd)/sidekick_chat.db:/app/sidekick_chat.db --name sidekick-bot sidekick-chatbot
```

### Option 2: Linux VPS (Ubuntu / Debian with Systemd & Nginx)
1. Clone the repository into `/var/www/Sidekick-Custom-ChatBot`.
2. Setup virtualenv and install requirements:
   ```bash
   cd /var/www/Sidekick-Custom-ChatBot
   python3 -m venv venv
   ./venv/bin/pip install -r requirements.txt
   ```
3. Copy systemd service file:
   ```bash
   sudo cp sidekick.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable sidekick
   sudo systemctl start sidekick
   ```
4. Configure Nginx reverse proxy using `nginx.conf.example` and enable SSL with Let's Encrypt:
   ```bash
   sudo apt install certbot python3-certbot-nginx
   sudo certbot --nginx -d chat.sidekick.pk
   ```

### Option 3: PaaS (Render / Railway)
- Connect this GitHub repository.
- Render / Railway will automatically detect `Procfile` and `requirements.txt`.
- Add your environment variables in the dashboard.
- Deploy!

---

## 🔒 Security Best Practices for Production
1. **Never commit `.env`** to public source control.
2. Change default `ADMIN_PASSWORD` and `SECRET_KEY` before going live.
3. Ensure HTTPS is enabled in front of the application.
