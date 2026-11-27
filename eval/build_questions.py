"""Writes eval/questions.json: 100 questions (40 Hindi, 30 Hinglish, 30 English) with gold schemes,
plus 10 trick questions about schemes that don't exist (target: 10/10 "couldn't find").

Gold = myScheme slugs present in the corpus. About a third of the questions describe a need without
naming the scheme, so recall measures retrieval and not just name matching.
Run: python -m eval.build_questions
"""
from __future__ import annotations

import json
import os
from collections import Counter

# (gold slug, lang, question, named?)
Q = [
    # ---- PM-KISAN
    ("pm-kisan", "hi", "पीएम किसान योजना में साल में कितने पैसे मिलते हैं?", True),
    ("pm-kisan", "hi", "छोटे किसानों को सरकार हर साल 6000 रुपये किस योजना में देती है?", False),
    ("pm-kisan", "hinglish", "PM Kisan ki kist ke liye kaun se documents chahiye?", True),
    ("pm-kisan", "en", "Who is not eligible for PM-KISAN income support?", True),
    ("pm-kisan", "en", "Which scheme gives farmers Rs 6,000 per year in three instalments?", False),
    # ---- PMAY-G
    ("pmay-g", "hi", "गाँव में पक्का घर बनाने के लिए सरकार से कितनी मदद मिलती है?", False),
    ("pmay-g", "hi", "प्रधानमंत्री आवास योजना ग्रामीण के लिए पात्रता क्या है?", True),
    ("pmay-g", "hinglish", "Gramin awas yojana mein ghar ke liye kitna paisa milta hai?", True),
    ("pmay-g", "en", "How do I apply for Pradhan Mantri Awas Yojana Gramin?", True),
    ("pmay-g", "hinglish", "Kachcha ghar hai gaon mein, pakka ghar banane ke liye koi yojana hai?", False),
    # ---- PMAY-U
    ("pmay-u", "hi", "शहर में घर खरीदने के लिए ब्याज सब्सिडी किस योजना में मिलती है?", False),
    ("pmay-u", "hi", "प्रधानमंत्री आवास योजना शहरी में आवेदन कैसे करें?", True),
    ("pmay-u", "en", "What are the income categories under PMAY Urban?", True),
    ("pmay-u", "hinglish", "Shahri awas yojana ke liye kaun eligible hai?", True),
    # ---- PMUY (Ujjwala)
    ("pmuy", "hi", "उज्ज्वला योजना में मुफ्त गैस कनेक्शन के लिए कौन आवेदन कर सकता है?", True),
    ("pmuy", "hi", "गरीब महिलाओं को एलपीजी कनेक्शन किस योजना से मिलता है?", False),
    ("pmuy", "hinglish", "Ujjwala yojana ke liye kaun se documents lagte hain?", True),
    ("pmuy", "en", "Can a man apply for a Pradhan Mantri Ujjwala Yojana connection?", True),
    ("pmuy", "en", "Which scheme gives free LPG connections to women from poor households?", False),
    # ---- SSY
    ("ssy", "hi", "सुकन्या समृद्धि खाता कितनी उम्र तक की बेटी के लिए खुल सकता है?", True),
    ("ssy", "hi", "बेटी की पढ़ाई और शादी के लिए बचत खाता कौन सा है?", False),
    ("ssy", "hinglish", "Sukanya samriddhi mein minimum kitna deposit karna padta hai?", True),
    ("ssy", "en", "What is the maximum deposit per year in a Sukanya Samriddhi account?", True),
    # ---- APY
    ("apy", "hi", "अटल पेंशन योजना में कितनी उम्र में जुड़ सकते हैं?", True),
    ("apy", "hi", "असंगठित क्षेत्र के मज़दूरों के लिए 60 साल के बाद पेंशन वाली योजना कौन सी है?", False),
    ("apy", "hinglish", "Atal pension yojana mein kitni pension milti hai?", True),
    ("apy", "en", "Who cannot join the Atal Pension Yojana?", True),
    # ---- PMJJBY
    ("pmjjby", "hi", "जीवन ज्योति बीमा योजना का प्रीमियम कितना है?", True),
    ("pmjjby", "hi", "सिर्फ़ 436 रुपये में 2 लाख का जीवन बीमा किस योजना में है?", False),
    ("pmjjby", "en", "What is the age limit for PMJJBY life insurance?", True),
    ("pmjjby", "hinglish", "Jeevan jyoti bima ka claim kaise karein?", True),
    # ---- PMSBY
    ("pmsby", "hi", "सुरक्षा बीमा योजना में दुर्घटना पर कितना पैसा मिलता है?", True),
    ("pmsby", "hi", "20 रुपये सालाना वाला दुर्घटना बीमा कौन सा है?", False),
    ("pmsby", "en", "What does Pradhan Mantri Suraksha Bima Yojana cover?", True),
    ("pmsby", "hinglish", "Suraksha bima yojana ke liye bank account zaroori hai kya?", True),
    # ---- PMMY (Mudra)
    ("pmmy", "hi", "मुद्रा लोन में शिशु, किशोर और तरुण क्या हैं?", True),
    ("pmmy", "hi", "छोटा व्यापार शुरू करने के लिए बिना गारंटी लोन किस योजना में मिलता है?", False),
    ("pmmy", "hinglish", "Mudra loan kaise milega dukan ke liye?", True),
    ("pmmy", "en", "What is the maximum loan under Pradhan Mantri Mudra Yojana?", True),
    # ---- Stand-Up India
    ("sui", "hi", "स्टैंड-अप इंडिया योजना में कितना लोन मिलता है?", True),
    ("sui", "hinglish", "SC/ST ya mahila entrepreneur ke liye 10 lakh se 1 crore ka loan kis yojana mein milta hai?", False),
    ("sui", "en", "How do I apply for a Stand-Up India loan?", True),
    ("sui", "en", "Is Stand-Up India only for new greenfield businesses?", True),
    # ---- KCC
    ("kcc", "hi", "किसान क्रेडिट कार्ड पर ब्याज दर कितनी है?", True),
    ("kcc", "hi", "खेती के लिए कम ब्याज पर उधार किस कार्ड से मिलता है?", False),
    ("kcc", "hinglish", "Kisan credit card banwane ke liye kya documents chahiye?", True),
    ("kcc", "en", "Can fishermen and animal farmers get a Kisan Credit Card?", True),
    # ---- PMFBY
    ("pmfby", "hi", "फसल बीमा योजना में किसान को कितना प्रीमियम देना होता है?", True),
    ("pmfby", "hi", "बारिश या सूखे से फसल खराब होने पर मुआवज़ा किस योजना में मिलता है?", False),
    ("pmfby", "hinglish", "Fasal bima ka claim kaise karte hain?", True),
    ("pmfby", "en", "Which crops are covered under Pradhan Mantri Fasal Bima Yojana?", True),
    # ---- MGNREGA
    ("mgnrega", "hi", "मनरेगा में साल में कितने दिन काम की गारंटी है?", True),
    ("mgnrega", "hi", "गाँव में रोज़गार न मिले तो सरकार से 100 दिन का काम कैसे मिलेगा?", False),
    ("mgnrega", "hinglish", "Manrega job card kaise banta hai?", True),
    ("mgnrega", "en", "What happens if MGNREGA work is not given within 15 days?", True),
    ("mgnrega", "hinglish", "Gaon mein kaam nahi mil raha, 100 din ka rozgar wali yojana batao", False),
    # ---- PMMVY
    ("pmmvy", "hi", "मातृ वंदना योजना में गर्भवती महिला को कितने पैसे मिलते हैं?", True),
    ("pmmvy", "hi", "पहले बच्चे के जन्म पर माँ को सरकारी पैसा किस योजना में मिलता है?", False),
    ("pmmvy", "hinglish", "Matru vandana yojana ka form kahan bharein?", True),
    ("pmmvy", "en", "What documents are needed for Pradhan Mantri Matru Vandana Yojana?", True),
    ("pmmvy", "en", "Is there cash support for pregnant women for their first child?", False),
    # ---- IGNWPS
    ("ignwps", "hi", "विधवा पेंशन योजना में हर महीने कितनी पेंशन मिलती है?", True),
    ("ignwps", "hi", "पति की मृत्यु के बाद गरीब महिला को पेंशन कैसे मिलेगी?", False),
    ("ignwps", "hinglish", "Vidhwa pension ke liye umar kitni honi chahiye?", True),
    ("ignwps", "en", "Who is eligible for the Indira Gandhi National Widow Pension Scheme?", True),
    # ---- IGNDPS
    ("igndps", "hi", "दिव्यांग पेंशन के लिए कितने प्रतिशत विकलांगता चाहिए?", True),
    ("igndps", "hi", "बीपीएल परिवार के विकलांग व्यक्ति को केंद्र सरकार की कौन सी पेंशन मिलती है?", False),
    ("igndps", "en", "What is the monthly amount under the Indira Gandhi National Disability Pension Scheme?", True),
    ("igndps", "hinglish", "Viklang pension ke liye kaun se documents lagte hain?", True),
    ("igndps", "hinglish", "Disability hai aur BPL card hai, pension wali koi yojana hai kya?", False),
    # ---- PMEGP
    ("pmegp", "hi", "पीएमईजीपी में नया उद्योग लगाने पर कितनी सब्सिडी मिलती है?", False),
    ("pmegp", "hi", "प्रधानमंत्री रोजगार सृजन कार्यक्रम के लिए कौन आवेदन कर सकता है?", True),
    ("pmegp", "hinglish", "PMEGP loan ke liye minimum padhai kitni chahiye?", True),
    ("pmegp", "en", "What is the margin money subsidy under PMEGP for rural areas?", True),
    ("pmegp", "en", "Is there a subsidy for setting up a new manufacturing unit in a village?", False),
    # ---- KSY (UP)
    ("ksy", "hi", "कन्या सुमंगला योजना में कुल कितने रुपये मिलते हैं?", True),
    ("ksy", "hi", "उत्तर प्रदेश में बेटी के जन्म पर पैसे किस योजना में मिलते हैं?", False),
    ("ksy", "hinglish", "Kanya sumangala yojana ke liye family income kitni honi chahiye?", True),
    ("ksy", "en", "How many installments are paid under the Kanya Sumangala Yojana?", True),
    ("ksy", "hinglish", "UP mein beti hui hai, uske liye koi sarkari yojana hai?", False),
    # ---- PMJDY
    ("pmjdy", "hi", "जन धन खाते में ओवरड्राफ्ट कितना मिलता है?", True),
    ("pmjdy", "hi", "बिना पैसे के ज़ीरो बैलेंस बैंक खाता किस योजना में खुलता है?", False),
    ("pmjdy", "hinglish", "Jan dhan account kholne ke liye kya chahiye?", True),
    ("pmjdy", "en", "What insurance comes with a Pradhan Mantri Jan Dhan Yojana account?", True),
    ("pmjdy", "en", "How can I open a zero-balance bank account under a government scheme?", False),
    # ---- PM SVANidhi
    ("pm-svanidhi", "hi", "रेहड़ी-पटरी वालों को स्वनिधि योजना में कितना लोन मिलता है?", True),
    ("pm-svanidhi", "hi", "ठेला लगाने वाले को बिना गारंटी लोन कहाँ से मिलेगा?", False),
    ("pm-svanidhi", "hinglish", "Street vendor hoon, PM Svanidhi loan ke liye kya karna hoga?", True),
    ("pm-svanidhi", "hinglish", "Thela lagata hoon, 10000 ka loan kaise milega?", False),
    ("pm-svanidhi", "en", "Is there a loan scheme for street vendors without collateral?", False),
    ("pm-svanidhi", "en", "What is the interest subsidy under PM SVANidhi?", True),
    # ---- top-ups to reach 40 / 30 / 30
    ("pm-kisan", "hi", "पीएम किसान में ई-केवाईसी कैसे करें?", True),
    ("pmuy", "hinglish", "Free gas cylinder connection ke liye kahan apply karein?", False),
    ("kcc", "en", "What is the credit limit under the Kisan Credit Card scheme?", True),
    ("pmfby", "hinglish", "Fasal kharab ho gayi baarish se, bima kaise milega?", False),
    ("apy", "en", "Is there a government pension scheme for unorganised sector workers?", False),
    ("ssy", "hinglish", "Beti ke liye government savings account kaunsa hai?", False),
    ("pmay-u", "en", "Is there a subsidy on home loan interest for low-income families in cities?", False),
    ("pmjjby", "hinglish", "Kam premium wala life insurance sarkar ka kaunsa hai?", False),
    ("pmsby", "hinglish", "Accident insurance sirf 20 rupaye mein kaise milega?", False),
]

TRICK = [
    ("hi", "प्रधानमंत्री मुफ्त स्मार्टफोन योजना में कैसे आवेदन करें?"),
    ("hi", "पीएम फ्री लैपटॉप योजना 2026 की पात्रता क्या है?"),
    ("hi", "राष्ट्रीय मुफ्त ट्रैक्टर योजना में कितनी सब्सिडी मिलती है?"),
    ("hinglish", "PM Bijli Bill Maafi Yojana ke liye form kaise bharein?"),
    ("hinglish", "PM Free Washing Machine Yojana mein kaun eligible hai?"),
    ("hinglish", "Rashtriya Free Bike Yojana ka registration kaise hoga?"),
    ("en", "What are the eligibility rules for the PM Gold Coin Yojana?"),
    ("en", "How do I apply for the PM Mobile Recharge Yojana?"),
    ("en", "What documents are required for the Digital Gaay Bima Yojana?"),
    ("en", "How much money does the PM Free Solar Car Scheme give?"),
]


def build() -> dict:
    questions = [{"id": f"q{i:03d}", "question": q, "lang": lang, "gold_slugs": [slug], "named": named}
                 for i, (slug, lang, q, named) in enumerate(Q, start=1)]
    trick = [{"id": f"t{i:02d}", "question": q, "lang": lang, "gold_slugs": [], "expect_refusal": True}
             for i, (lang, q) in enumerate(TRICK, start=1)]
    return {"questions": questions, "trick": trick}


def main() -> None:
    data = build()
    counts = Counter(q["lang"] for q in data["questions"])
    assert len(data["questions"]) == 100, len(data["questions"])
    assert counts == {"hi": 40, "hinglish": 30, "en": 30}, counts
    out = os.path.join(os.path.dirname(__file__), "questions.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    print(f"wrote {out}: {dict(counts)} + {len(data['trick'])} trick")


if __name__ == "__main__":
    main()
