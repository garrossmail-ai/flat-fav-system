import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime, date
import re
import os

DATA_FILE = 'data/races.json'

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

UK_COURSES = [
    'ascot', 'ayr', 'bath', 'beverley', 'brighton', 'carlisle', 'catterick',
    'chepstow', 'chester', 'doncaster', 'epsom', 'exeter', 'goodwood',
    'hamilton', 'haydock', 'kempton', 'leicester', 'lingfield', 'musselburgh',
    'newbury', 'newcastle', 'newmarket', 'nottingham', 'pontefract', 'redcar',
    'ripon', 'salisbury', 'sandown', 'southwell', 'thirsk', 'windsor',
    'wolverhampton', 'yarmouth', 'york', 'chelmsford', 'ffos las'
]

GOING_SKIP = ['soft', 'heavy']

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r') as f:
            return json.load(f)
    return {'last_updated': None, 'races': []}

def save_data(data):
    data['last_updated'] = datetime.utcnow().isoformat()
    with open(DATA_FILE, 'w') as f:
        json.dump(data, f, indent=2)

def is_uk_course(course):
    return any(uk in course.lower() for uk in UK_COURSES)

def is_handicap(race_name):
    name = race_name.lower()
    return 'handicap' in name or ' hcap' in name or '(0-' in name

def is_jumps(race_name):
    name = race_name.lower()
    return any(x in name for x in ['hurdle', 'chase', 'bumper', 'national hunt', 'steeple', 'novices'])

def going_ok(going):
    if not going:
        return True
    g = going.lower().strip()
    return not any(skip in g for skip in GOING_SKIP)

def get_todays_racecards():
    url = 'https://www.attheraces.com/racecards'
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, 'lxml')
    except Exception as e:
        print(f"Error fetching racecards: {e}")
        return []

    race_urls = []
    seen = set()
    for link in soup.find_all('a', href=True):
        href = link['href']
        if '/racecard/' in href:
            full = 'https://www.attheraces.com' + href if not href.startswith('http') else href
            # Only UK races
            parts = full.rstrip('/').split('/')
            if len(parts) >= 3:
                course = parts[-3].replace('-', ' ').lower()
                if not is_uk_course(course):
                    continue
            if full not in seen:
                seen.add(full)
                race_urls.append(full)
    return race_urls

def parse_racecard(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            print(f"HTTP {r.status_code} for {url}")
            return None
        soup = BeautifulSoup(r.text, 'lxml')

        parts = url.rstrip('/').split('/')
        course = parts[-3].replace('-', ' ').title()
        date_str = parts[-2]
        time_raw = parts[-1]
        time_fmt = time_raw[:2] + ':' + time_raw[2:] if len(time_raw) == 4 else time_raw

        # Race name — look for specific ATR elements, avoid ad text
        race_name = ''
        for selector in [
            {'class': re.compile(r'race-title|raceTitle|race_title', re.I)},
            {'class': re.compile(r'race-name|raceName', re.I)},
        ]:
            el = soup.find(attrs=selector)
            if el:
                text = el.get_text(strip=True)
                if len(text) > 5 and 'free bet' not in text.lower() and 'offer' not in text.lower():
                    race_name = text
                    break

        if not race_name:
            # Fall back to page title minus site name
            title = soup.find('title')
            if title:
                t = title.get_text(strip=True)
                t = re.sub(r'\|.*$', '', t).strip()
                t = re.sub(r'At The Races.*$', '', t, flags=re.I).strip()
                if len(t) > 5:
                    race_name = t

        # Going
        going = ''
        page_text = soup.get_text(separator=' ')
        m = re.search(r'\bGoing\b[:\s]+([A-Za-z][A-Za-z\s\/]{2,35}?)(?=\s{2,}|\bClass\b|\bDistance\b|\bCourse\b|\bPrize\b)', page_text)
        if m:
            going = m.group(1).strip()

        # Runners — count stall/draw numbers in the page
        runners = 0
        all_text = [s.strip() for s in soup.strings if s.strip()]
        stall_nums = [t for t in all_text if re.match(r'^\d{1,2}$', t) and 1 <= int(t) <= 25]
        if stall_nums:
            runners = max(int(t) for t in stall_nums)

        # Odds — find shortest fractional price
        favourite = ''
        odds = None
        prices = []
        frac_pattern = re.compile(r'\b(\d{1,3})/(\d{1,2})\b')
        for m2 in frac_pattern.finditer(page_text):
            num, den = int(m2.group(1)), int(m2.group(2))
            if den > 0:
                dec = round(num / den + 1, 2)
                if 1.1 < dec < 20:
                    prices.append(dec)
        if prices:
            odds = min(prices)

        # Horse name
        for cls in ['horse-name', 'runner-name', 'horseName', 'runnerName', 'horse_name']:
            el = soup.find(class_=cls)
            if el:
                name = el.get_text(strip=True)
                if name and 'bet' not in name.lower() and len(name) > 1:
                    favourite = name
                    break

        return {
            'course': course,
            'date': date_str,
            'time': time_fmt,
            'race_name': race_name[:80],
            'going': going[:50],
            'runners': runners if runners > 1 else None,
            'favourite': favourite[:40],
            'odds': odds,
            'url': url
        }

    except Exception as e:
        print(f"Error parsing {url}: {e}")
        return None

def apply_filter(race):
    fails = []
    if is_handicap(race.get('race_name', '')):
        fails.append('handicap')
    runners = race.get('runners')
    if runners and runners > 10:
        fails.append(f'{runners} runners')
    
    if not going_ok(race.get('going', '')):
        fails.append(f'going: {race.get("going")}')
    return len(fails) == 0, fails

def get_result(race):
    result_url = race.get('url', '').replace('/racecard/', '/results/')
    try:
        r = requests.get(result_url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return None, None
        soup = BeautifulSoup(r.text, 'lxml')
        page_text = soup.get_text(separator=' ')

        winner = ''
        for cls in ['horse-name', 'runner-name', 'horseName', 'winnerName', 'winner']:
            el = soup.find(class_=cls)
            if el:
                winner = el.get_text(strip=True)
                break

        if not winner:
            m = re.search(r'(?:1st|Winner)[:\s]+([A-Z][a-zA-Z\s]{2,30}?)(?:\s{2,}|\d|$)', page_text)
            if m:
                winner = m.group(1).strip()

        fav = race.get('favourite', '').lower().strip()
        won = bool(winner and fav and len(fav) > 2 and fav[:6] in winner.lower())
        return winner[:40], 'won' if won else 'lost'

    except Exception as e:
        print(f"Error getting result: {e}")
        return None, None

def main():
    print(f"Running at {datetime.utcnow().isoformat()}")
    data = load_data()
    existing_ids = {r['id'] for r in data['races'] if 'id' in r}
    hour = datetime.utcnow().hour
    today = date.today().isoformat()

    if hour < 15:
        print("Morning run — scanning UK flat races")
        urls = get_todays_racecards()
        print(f"Found {len(urls)} UK race links")
        added = 0
        for url in urls[:60]:
            parts = url.rstrip('/').split('/')
            if len(parts) < 3:
                continue
            rid = '_'.join(parts[-3:]).lower()
            if rid in existing_ids:
                continue
            race = parse_racecard(url)
            if not race:
                continue
            if is_jumps(race.get('race_name', '')):
                print(f"Skip jumps: {race['course']} {race['time']}")
                continue
            qualified, fails = apply_filter(race)
            race['id'] = rid
            race['qualified'] = qualified
            race['filter_fail'] = fails if not qualified else []
            race['result'] = 'pending'
            race['winner'] = None
            data['races'].append(race)
            existing_ids.add(rid)
            added += 1
            status = '✓ QUALIFIED' if qualified else '✗ skip'
            print(f"{status}: {race['course']} {race['time']} — {race['race_name'][:50]} — {race['runners']} runners — {race['odds']} dec — {race['going']}")
        print(f"Added {added} races")
    else:
        print("Evening run — fetching results")
        updated = 0
        for race in data['races']:
            if race.get('result') != 'pending':
                continue
            if race.get('date', '') != today:
                continue
            winner, result = get_result(race)
            if result:
                race['result'] = result
                race['winner'] = winner
                updated += 1
                print(f"{race['course']} {race['time']} — {result.upper()} — winner: {winner}")
        print(f"Updated {updated} results")

    save_data(data)
    print("Done")

if __name__ == '__main__':
    main()