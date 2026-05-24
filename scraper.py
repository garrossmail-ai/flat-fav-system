import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime, date
import re
import os

DATA_FILE = 'data/races.json'

GOING_SKIP = ['soft', 'heavy', 'soft (good to soft in places)', 'heavy (soft in places)']

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1'
}

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r') as f:
            return json.load(f)
    return {'last_updated': None, 'races': []}

def save_data(data):
    data['last_updated'] = datetime.utcnow().isoformat()
    with open(DATA_FILE, 'w') as f:
        json.dump(data, f, indent=2)

def is_handicap(race_name):
    name = race_name.lower()
    return 'handicap' in name or ' hcap' in name or '(0-' in name

def going_ok(going):
    if not going:
        return True
    g = going.lower().strip()
    for skip in GOING_SKIP:
        if skip in g:
            return False
    return 'soft' not in g and 'heavy' not in g

def get_todays_racecards():
    url = 'https://www.attheraces.com/racecards'
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, 'lxml')
    except Exception as e:
        print(f"Error fetching racecards: {e}")
        return []

    links = soup.find_all('a', href=True)
    race_urls = []
    seen = set()
    for link in links:
        href = link['href']
        if '/racecard/' in href:
            full = 'https://www.attheraces.com' + href if not href.startswith('http') else href
            if full not in seen:
                seen.add(full)
                race_urls.append(full)
    return race_urls

def parse_racecard(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, 'lxml')

        parts = url.rstrip('/').split('/')
        course = parts[-3].replace('-', ' ').title()
        date_str = parts[-2]
        time_raw = parts[-1]
        time_fmt = time_raw[:2] + ':' + time_raw[2:] if len(time_raw) == 4 else time_raw

        race_name = ''
        for tag in ['h1', 'h2']:
            el = soup.find(tag)
            if el:
                race_name = el.get_text(strip=True)
                break

        going = ''
        text = soup.get_text(separator=' ')
        m = re.search(r'Going[:\s]+([A-Za-z\s\/\(\)]{3,40}?)(?:\s{2,}|Going|Course|Distance|Class)', text)
        if m:
            going = m.group(1).strip()

        runners = 0
        rows = soup.find_all('tr')
        for row in rows:
            cells = row.find_all('td')
            if cells and cells[0].get_text(strip=True).isdigit():
                runners += 1
        if runners < 2:
            nums = [s.strip() for s in soup.strings if s.strip().isdigit()]
            valid = [int(n) for n in nums if 1 <= int(n) <= 25]
            if valid:
                runners = max(valid)

        favourite = ''
        odds = None
        prices = []
        frac_pattern = re.compile(r'\b(\d{1,3})/(\d{1,2})\b')
        for m2 in frac_pattern.finditer(text):
            num, den = int(m2.group(1)), int(m2.group(2))
            dec = round(num / den + 1, 2)
            if 1.1 < dec < 20:
                prices.append(dec)
        if prices:
            odds = min(prices)

        for cls in ['horse-name', 'runner-name', 'horseName', 'runnerName']:
            el = soup.find(class_=cls)
            if el:
                favourite = el.get_text(strip=True)
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
    odds = race.get('odds')
    if odds:
        if odds < 3.0:
            fails.append(f'odds {odds} too short')
        elif odds > 5.0:
            fails.append(f'odds {odds} too long')
    else:
        fails.append('odds unknown')
    if not going_ok(race.get('going', '')):
        fails.append(f'going {race.get("going")}')
    return len(fails) == 0, fails

def get_result(race):
    result_url = race.get('url', '').replace('/racecard/', '/results/')
    try:
        r = requests.get(result_url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, 'lxml')
        text = soup.get_text(separator=' ')

        winner = ''
        for cls in ['horse-name', 'runner-name', 'horseName', 'winnerName']:
            el = soup.find(class_=cls)
            if el:
                winner = el.get_text(strip=True)
                break

        if not winner:
            m = re.search(r'1st[:\s]+([A-Z][a-zA-Z\s]{2,30})', text)
            if m:
                winner = m.group(1).strip()

        fav = race.get('favourite', '').lower().strip()
        won = bool(winner and fav and fav[:5] in winner.lower())
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
        print("Morning run — scanning races")
        urls = get_todays_racecards()
        print(f"Found {len(urls)} race links")
        added = 0
        for url in urls[:50]:
            parts = url.rstrip('/').split('/')
            if len(parts) < 3:
                continue
            rid = '_'.join(parts[-3:]).lower()
            if rid in existing_ids:
                continue
            race = parse_racecard(url)
            if not race:
                continue
            name = race.get('race_name', '').lower()
            if any(x in name for x in ['hurdle', 'chase', 'bumper', 'national hunt']):
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
            print(f"{status}: {race['course']} {race['time']} — {race['race_name'][:50]}")
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
                print(f"{race['course']} {race['time']} — {result.upper()}")
        print(f"Updated {updated} results")

    save_data(data)
    print("Done")

if __name__ == '__main__':
    main()