from scripts.load_hf_dataset import bootstrap_criteria, fix_mojibake, parse_pdf_text, split_documents

PAGE = (
    "Aam Aadmi Bima YojanaAre you sure you want to sign out?CancelSign OutEngEnglish/à¤¹à¤¿à¤‚à¤¦à¥€Sign In"
    "BackDetailsBenefitsEligibility... Check EligibilityAssamAam Aadmi Bima YojanaBima YojanaInsurance"
    "DetailsAam Aadmi Bima Yojana is a social security scheme.ï»¿Benefits1. Cover of Rs. 30,000 on natural death."
    "ï»¿Eligibility1. Age Limit: The scheme is open to individuals between the ages of 18 and 59 years.2. Income "
    "Limit: annual income not exceeding Rs. 1,00,000.ï»¿Application ProcessOfflineStep 01: Visit the centre."
    "ï»¿Documents Required1. Aadhaar Card: proof.2. Age Proof: birth certificate.Frequently Asked QuestionsCan I "
    "apply at 62?No. Sources And ReferencesUser Manual"
)


def test_fix_mojibake():
    assert fix_mojibake("à¤¹à¤¿à¤‚à¤¦à¥€") == "हिंदी"
    assert fix_mojibake("Ministerâ€™s") == "Minister’s"


def test_parse_pdf_text_sections_and_fields():
    rec = parse_pdf_text("aaby", PAGE)
    s, sec = rec["scheme"], rec["sections"]["en"]
    assert s["name"] == "Aam Aadmi Bima Yojana" and s["state"] == "Assam"
    assert s["source_url"] == "https://www.myscheme.gov.in/schemes/aaby"
    assert set(sec) >= {"details", "benefits", "eligibility", "application_process", "documents_required", "faq"}
    assert "30,000" in sec["benefits"]
    assert s["documents_required"] == ["Aadhaar Card", "Age Proof"]
    assert s["criteria"]["age"] == {"min": 18, "max": 59}
    assert s["criteria"]["annual_income"] == {"max": 100000}


def test_split_documents_glued_labels():
    assert split_documents("BPL CardAadhaar cardAge Proof") == ["BPL Card", "Aadhaar card", "Age Proof"]


def test_bootstrap_criteria_gender_and_bpl():
    c = bootstrap_criteria({"eligibility": "The applicant must be a woman from a below poverty line household."}, "ALL")
    assert c["gender"] == "female" and c["bpl"] is True and c["states"] == ["ALL"]
