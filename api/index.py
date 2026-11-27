"""Vercel entry point: the FastAPI app as a Python serverless function (see vercel.json)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.api.main import app  # noqa: E402,F401
