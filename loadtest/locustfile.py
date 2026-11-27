"""Load test: 50 concurrent users against /chat, /eligibility and /search.

    locust -f loadtest/locustfile.py --host https://<api> --users 50 --spawn-rate 5 --run-time 5m --headless \
        --csv loadtest/results

Then read p95 from loadtest/results_stats.csv and GET /metrics for cost per 1,000 queries.
Raise RATE_LIMIT_PER_MIN on the server for the run (all users share one IP), or the limiter returns 429s.
"""
import random

from locust import HttpUser, between, task

QUESTIONS = [
    "PM Kisan mein kitne paise milte hain?",
    "उज्ज्वला योजना के लिए कौन से दस्तावेज़ चाहिए?",
    "I am a 45 year old farmer in UP with 2 acres of land, which schemes can I get?",
    "विधवा पेंशन योजना में कितनी पेंशन मिलती है?",
    "How do I apply for a Stand-Up India loan?",
    "Street vendor hoon, PM Svanidhi loan kaise milega?",
    "Is there a loan scheme for street vendors without collateral?",
    "मनरेगा में साल में कितने दिन काम की गारंटी है?",
    "Kanya sumangala yojana ke liye family income kitni honi chahiye?",
    "What is the PM Gold Coin Yojana?",
]
PROFILES = [
    {"state": "Uttar Pradesh", "age": 45, "occupation": "farmer", "land_acres": 2, "annual_income": 120000},
    {"state": "Uttar Pradesh", "age": 22, "gender": "female", "occupation": "student", "annual_income": 250000},
    {"state": "Uttar Pradesh", "age": 67, "bpl": True},
]


class Citizen(HttpUser):
    wait_time = between(2, 6)

    @task(6)
    def chat(self):
        q = random.choice(QUESTIONS)
        self.client.post("/chat", json={"message": q, "stream": False}, name="/chat")

    @task(2)
    def eligibility(self):
        self.client.post("/eligibility", json={"profile": random.choice(PROFILES)}, name="/eligibility")

    @task(1)
    def search(self):
        self.client.post("/search", json={"query": random.choice(QUESTIONS), "k": 5}, name="/search")

    @task(1)
    def health(self):
        self.client.get("/health", name="/health")
