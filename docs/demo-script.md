# Demo script (2 minutes)

Three personas, then the metrics and the scaling slide. Rehearse twice; warm the server first.

## Before you start (5 minutes ahead)

1. `curl $API/health` twice (Render's free server sleeps; the first call can take ~50 s).
2. Warm the answer cache by asking each demo question once (cached answers come back without an LLM call).
3. Open the app on a phone over mobile data, install it ("Add to Home screen"), set **हिंदी**.
4. Keep the recorded backup video open in another tab in case the free tier runs out mid-demo.

## Script

| Time | Do | Say |
| --- | --- | --- |
| 0:00 | Show the home screen | "Millions of eligible Indians miss benefits because schemes are buried in English PDFs. Haqdar AI answers in your language, with an official link every time." |
| 0:10 | **Ramesh, farmer in UP, speaks Hindi.** Tap 🎤, say: *"मैं उत्तर प्रदेश का 45 साल का किसान हूँ, मेरे पास 2 एकड़ ज़मीन है"*. Show the editable transcript, tap भेजें | "Voice in Hindi. He can fix the transcript before sending." |
| 0:30 | Scroll the answer: cited sentences [1][2], PM-KISAN card marked "शायद पात्र हैं", documents, official link, last-checked date, verification badge | "Eligibility is decided by rules, not the AI. Every sentence links to the official page." |
| 0:45 | Tap 🔊 सुनें | "And it reads the answer aloud." |
| 0:50 | **Priya, 22, student, types Hinglish.** Switch to English UI, type *"Main 22 saal ki student hoon UP se, meri family income 2.5 lakh hai, kaun si yojana milegi?"* | "Hinglish works too." |
| 1:05 | Answer the follow-up chip (*BPL card?* → हाँ / नहीं) and show the cards change | "One follow-up question, chosen because it changes the most results. The results update live." |
| 1:20 | **Trick question:** *"PM Gold Coin Yojana ke liye form kaise bharein?"* | "Ask about a scheme that doesn't exist, and it says it couldn't find it in official sources instead of making something up." |
| 1:30 | Home screen: **Recently updated** panel; open a scheme's document checklist and tick an item | "Every scheme is re-checked daily; changes show here. The checklist works offline." |
| 1:40 | Switch to slides: metrics table, then scaling + cost | "Recall@5, faithfulness and eligibility accuracy on 100 multilingual questions; ₹0 to run on free tiers." |
| 2:00 | End | |

## Exact demo inputs

* Ramesh (voice, Hindi): `मैं उत्तर प्रदेश का 45 साल का किसान हूँ, मेरे पास 2 एकड़ ज़मीन है`
* Priya (typed, Hinglish): `Main 22 saal ki student hoon UP se, meri family income 2.5 lakh hai, kaun si yojana milegi?`
* Trick: `PM Gold Coin Yojana ke liye form kaise bharein?`
* Backup if voice fails: type Ramesh's sentence; the transcript box is the same input.

## If something breaks

| Problem | Fallback |
| --- | --- |
| Server cold | Ping `/health`, talk over the first slide while it wakes |
| Free-tier limit hit | Cached answers still work for the demo questions; otherwise play the recorded video |
| Microphone blocked | Type the Hindi sentence |
| Mobile data drops | The installed app opens offline; show the checklist, then the video |
