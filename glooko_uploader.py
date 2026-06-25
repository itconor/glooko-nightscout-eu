#!/usr/bin/env python3
# Glooko (EU) -> Nightscout treatments uploader. Web-form login (CSRF) + API fetch.
import os, re, json, time, hashlib, urllib.parse, urllib.request
from http.cookiejar import CookieJar

EMAIL    = os.environ["GLOOKO_EMAIL"]
PASSWORD = os.environ["GLOOKO_PASSWORD"]
REGION   = os.environ.get("GLOOKO_REGION", "eu")
NS_URL   = os.environ["NIGHTSCOUT_URL"].rstrip("/")
NS_SECRET= os.environ["NIGHTSCOUT_SECRET"]
INTERVAL = int(os.environ.get("INTERVAL_MIN", "10"))
DAYS     = int(os.environ.get("LOOKBACK_DAYS", "3"))
STATE    = "/data/uploaded.json"

MY  = f"https://{REGION}.my.glooko.com"
API = f"https://{REGION}.api.glooko.com"
UA  = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Safari/605.1.15"
GUID= "1e0c094e-1e54-4a4f-8e6a-f94484b53789"
NSH = hashlib.sha1(NS_SECRET.encode()).hexdigest()

def _last_sunday(y, m):
    import datetime
    d = datetime.date(y, m, 31)
    while d.weekday() != 6:
        d -= datetime.timedelta(days=1)
    return d

def to_utc(ts):
    """Glooko reports pump-local UK time labelled as 'Z'. Convert to true UTC (DST-aware)."""
    import datetime
    if not ts:
        return ts
    try:
        naive = datetime.datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")
    except Exception:
        return ts
    bst = _last_sunday(naive.year, 3) <= naive.date() < _last_sunday(naive.year, 10)
    utc = naive - datetime.timedelta(hours=1 if bst else 0)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")

def log(m): print(f"[glooko] {m}", flush=True)

def load_state():
    try: return set(json.load(open(STATE)))
    except Exception: return set()

def save_state(s):
    try:
        os.makedirs("/data", exist_ok=True)
        json.dump(sorted(s), open(STATE, "w"))
    except Exception as e: log(f"state save error: {e}")

def login():
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
    req = urllib.request.Request(f"{MY}/users/sign_in?locale=en-GB", headers={"User-Agent": UA})
    html = op.open(req, timeout=30).read().decode("utf-8", "ignore")
    m = re.search(r'name="authenticity_token"\s+value="([^"]+)"', html)
    if not m: raise Exception("no authenticity_token on sign_in page")
    data = urllib.parse.urlencode({
        "utf8": "✓", "authenticity_token": m.group(1),
        "user[email]": EMAIL, "user[password]": PASSWORD, "commit": "Log in",
    }).encode()
    req = urllib.request.Request(f"{MY}/users/sign_in?id=login_form&locale=en-GB", data=data,
        headers={"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded"})
    resp = op.open(req, timeout=30)
    if "sign_in" in resp.geturl():
        raise Exception("login rejected (check credentials)")
    return op

def api_get(op, path):
    req = urllib.request.Request(f"{API}{path}", headers={"User-Agent": UA, "Accept": "application/json"})
    return json.load(op.open(req, timeout=30))

def ns_post(t):
    req = urllib.request.Request(f"{NS_URL}/api/v1/treatments", data=json.dumps([t]).encode(),
        method="POST", headers={"Content-Type": "application/json", "api-secret": NSH})
    urllib.request.urlopen(req, timeout=20).read()

def run_once(state):
    op = login()
    since = time.strftime("%Y-%m-%dT00:00:00.000Z", time.gmtime(time.time() - DAYS*86400))
    P = f"lastUpdatedAt={since}&lastGuid={GUID}&limit=500"
    new = 0
    # pump boluses (carbs + insulin)
    for b in api_get(op, f"/api/v2/pumps/normal_boluses?{P}").get("normalBoluses", []):
        g = b.get("guid")
        if not g or g in state: continue
        carbs = b.get("carbsInput") or 0
        ins   = b.get("insulinDelivered") or 0
        if not (carbs or ins):
            state.add(g); continue
        t = {"created_at": to_utc(b.get("pumpTimestamp")), "enteredBy": "glooko-bridge", "glookoGuid": g,
             "eventType": "Meal Bolus" if carbs > 0 else "Correction Bolus"}
        if ins:   t["insulin"] = round(float(ins), 2)
        if carbs: t["carbs"]   = round(float(carbs), 1)
        ns_post(t); state.add(g); new += 1
    # manually-logged carbs
    for f in api_get(op, f"/api/v2/foods?{P}").get("foods", []):
        g = f.get("guid")
        if not g or g in state: continue
        carbs = f.get("carbs") or f.get("carbsInput") or 0
        if carbs:
            ns_post({"created_at": to_utc(f.get("timestamp") or f.get("pumpTimestamp")), "eventType": "Carb Correction",
                     "carbs": round(float(carbs),1), "enteredBy": "glooko-bridge", "glookoGuid": g}); new += 1
        state.add(g)
    # manual insulin injections
    for i in api_get(op, f"/api/v2/insulins?{P}").get("insulins", []):
        g = i.get("guid")
        if not g or g in state: continue
        u = i.get("value") or i.get("units") or i.get("insulin") or 0
        if u:
            ns_post({"created_at": to_utc(i.get("timestamp") or i.get("pumpTimestamp")), "eventType": "Correction Bolus",
                     "insulin": round(float(u),2), "enteredBy": "glooko-bridge", "glookoGuid": g}); new += 1
        state.add(g)
    log(f"uploaded {new} new treatment(s)")
    return state

if __name__ == "__main__":
    log(f"starting (region={REGION}, every {INTERVAL} min, lookback {DAYS}d)")
    st = load_state()
    while True:
        try:
            st = run_once(st); save_state(st)
        except Exception as e:
            log(f"error: {e}")
        time.sleep(INTERVAL * 60)
