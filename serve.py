"""
Production server entry point (Windows-compatible)
Uses Waitress instead of Flask's dev server.
Run: python serve.py
"""
from waitress import serve
from app import app, init_db

if __name__ == "__main__":
    init_db()
    print("=" * 50)
    print("  Sidekick AI Chatbot — PRODUCTION MODE")
    print("  Running on http://0.0.0.0:5000")
    print("  Open: http://127.0.0.1:5000")
    print("=" * 50)
    serve(app, host="0.0.0.0", port=5000, threads=8)
