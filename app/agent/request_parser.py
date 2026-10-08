"""Rule-based travel request extraction. Powers the stub LLM and keeps tests deterministic."""
from __future__ import annotations

import re
from datetime import date

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], start=1)}
MONTHS.update({k[:3]: v for k, v in list(MONTHS.items())})
MONTHS["sept"] = 9
WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
CURRENCY_WORDS = {"$": "USD", "usd": "USD", "dollar": "USD", "dollars": "USD", "aed": "AED", "dirham": "AED",
                  "dirhams": "AED", "pkr": "PKR", "rs": "PKR", "rupees": "PKR", "rupee": "PKR", "eur": "EUR",
                  "euro": "EUR", "euros": "EUR", "€": "EUR", "gbp": "GBP", "£": "GBP", "pounds": "GBP",
                  "qar": "QAR", "myr": "MYR", "try": "TRY"}
MONTH_RE = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"


def _num(token: str) -> int | None:
    token = token.lower()
    if token.isdigit():
        return int(token)
    return WORD_NUM.get(token)


def _next_occurrence(month: int, day: int, today: date) -> date | None:
    for year in (today.year, today.year + 1):
        try:
            d = date(year, month, day)
        except ValueError:
            return None
        if d >= today:
            return d
    return None


def find_dates(text: str, today: date) -> list[date]:
    found: list[tuple[int, date]] = []
    for m in re.finditer(r"\b(\d{4})-(\d{2})-(\d{2})\b", text):
        try:
            found.append((m.start(), date(int(m[1]), int(m[2]), int(m[3]))))
        except ValueError:
            pass
    for m in re.finditer(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{MONTH_RE}\b(?:,?\s+(\d{{4}}))?", text, re.I):
        month = MONTHS[m[2].lower()[:4] if m[2].lower().startswith("sept") else m[2].lower()[:3]]
        d = _safe_date(int(m[3]), month, int(m[1])) if m[3] else _next_occurrence(month, int(m[1]), today)
        if d:
            found.append((m.start(), d))
    for m in re.finditer(rf"\b{MONTH_RE}\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(\d{{4}}))?", text, re.I):
        month = MONTHS[m[1].lower()[:4] if m[1].lower().startswith("sept") else m[1].lower()[:3]]
        d = _safe_date(int(m[3]), month, int(m[2])) if m[3] else _next_occurrence(month, int(m[2]), today)
        if d:
            found.append((m.start(), d))
    found.sort(key=lambda x: x[0])
    out: list[date] = []
    for _, d in found:
        if d not in out:
            out.append(d)
    return out


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _city(fragment: str, known: list[str]) -> str | None:
    frag = fragment.lower()
    for name in sorted(known, key=len, reverse=True):
        if frag.startswith(name.lower()):
            return name
    return None


def parse_request(text: str, known_cities: list[str], today: date) -> dict:
    t = " " + text.strip() + " "
    low = t.lower()
    result: dict = {"origin": None, "destination": None, "departure_date": None, "return_date": None,
                    "duration_days": None, "travelers": None, "budget": None, "currency": None,
                    "preferences": {}, "date_hint": None}

    # route
    m = re.search(r"\bfrom\s+([a-z][a-z ]{1,30}?)\s+to\s+([a-z][a-z ]{1,30})", low)
    if m:
        result["origin"] = _city(m[1], known_cities) or m[1].strip().title()
        result["destination"] = _city(m[2], known_cities) or m[2].strip().split("  ")[0].title()
        if not _city(m[2], known_cities):
            result["destination"] = m[2].strip().split(" for ")[0].split(" on ")[0].split(" in ")[0].title()
    else:
        # "Islamabad to Dubai" without "from": two known cities joined by "to"
        names = "|".join(re.escape(n.lower()) for n in sorted(known_cities, key=len, reverse=True))
        pair = re.search(rf"\b({names})\s+(?:to|->|→)\s+({names})\b", low) if names else None
        if pair:
            result["origin"], result["destination"] = _city(pair[1], known_cities), _city(pair[2], known_cities)
    if not result["destination"]:
        to = re.search(r"\b(?:to|visit|in)\s+([a-z][a-z ]{1,30})", low)
        if to:
            result["destination"] = _city(to[1], known_cities)
        fr = re.search(r"\bfrom\s+([a-z][a-z ]{1,30})", low)
        if fr and not result["origin"]:
            result["origin"] = _city(fr[1], known_cities)

    # dates and duration
    dates = find_dates(t, today)
    if dates:
        result["departure_date"] = dates[0].isoformat()
        if len(dates) > 1 and dates[1] > dates[0]:
            result["return_date"] = dates[1].isoformat()
    dm = re.search(r"\b(\d{1,2}|" + "|".join(WORD_NUM) + r")[\s-]*(day|days|night|nights)\b", low)
    if dm:
        n = _num(dm[1])
        if n:
            result["duration_days"] = n + 1 if dm[2].startswith("night") else n
    hint = re.search(r"\b(next (?:week|month|year)|this (?:weekend|month)|in (?:the )?(?:summer|winter|spring|autumn)|soon|"
                     rf"(?:in|during|early|late|mid)\s+{MONTH_RE}\b)", low)
    if hint and not result["departure_date"]:
        result["date_hint"] = hint[0].strip()

    # travellers
    tm = re.search(r"\b(\d{1,2}|" + "|".join(WORD_NUM) + r")\s+(people|persons|travell?ers|adults|passengers|guests|of us)\b", low)
    if tm:
        result["travelers"] = _num(tm[1])
    elif re.search(r"\b(wife|husband|partner|friend|spouse)\s+and\s+i\b|\bi\s+and\s+my\s+(wife|husband|partner|friend)\b", low):
        result["travelers"] = 2
    elif re.search(r"\b(we|us|our family|my family)\b", low):
        result["travelers"] = None
    elif re.search(r"\b(i|i'm|i am|me|my)\b", low):
        result["travelers"] = 1

    # budget
    bm = re.search(r"(\$|€|£)\s?([\d,]+(?:\.\d+)?)\s*(k)?", t) or None
    if bm:
        amount = float(bm[2].replace(",", "")) * (1000 if bm[3] else 1)
        result["budget"], result["currency"] = amount, CURRENCY_WORDS[bm[1]]
    else:
        bm = re.search(r"\b([\d,]+(?:\.\d+)?)\s*(k)?\s*(usd|dollars?|aed|dirhams?|pkr|rupees?|rs|eur|euros?|gbp|pounds|qar|myr)\b", low) \
            or re.search(r"\b(usd|aed|pkr|rs\.?|eur|gbp|qar|myr)\s?([\d,]+(?:\.\d+)?)", low)
        if bm:
            g = bm.groups()
            if g[0].replace(",", "").replace(".", "").isdigit():
                amount, cur = float(g[0].replace(",", "")) * (1000 if g[1] else 1), g[2]
            else:
                cur, amount = g[0].rstrip("."), float(g[1].replace(",", ""))
            result["budget"], result["currency"] = amount, CURRENCY_WORDS.get(cur, cur.upper())

    # preferences
    prefs: dict = {}
    if re.search(r"(don't|do not|dont|no|avoid|not)\s+(want\s+)?(a\s+)?(very\s+)?early", low) or "red-eye" in low or "red eye" in low:
        prefs["avoid_early_departure"] = True
    if re.search(r"\b(direct|non-?stop)\b", low):
        prefs["prefer_direct"] = True
    if re.search(r"\b(luxury|5[- ]star|five[- ]star)\b", low):
        prefs["min_hotel_rating"] = 4.5
    elif re.search(r"\b(comfortable|nice|good)\s+hotel|\b4[- ]star\b", low):
        prefs["min_hotel_rating"] = 4.0
    if re.search(r"(not too expensive|cheap|budget|affordable|inexpensive|low[- ]cost|free)\s*(activities|things|attractions)?", low) \
            and re.search(r"activit|things to do|attraction|not too expensive", low):
        prefs["activity_budget"] = "low"
    elif re.search(r"\b(splurge|premium|high[- ]end)\b.*(activit|experience)", low):
        prefs["activity_budget"] = "high"
    result["preferences"] = prefs
    return result
