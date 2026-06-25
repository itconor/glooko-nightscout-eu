# glooko-nightscout-eu

A small, dependency-free uploader that syncs **carbs and insulin treatments** from a
**European Glooko** account into [Nightscout](https://github.com/nightscout/cgm-remote-monitor).

It exists because the official [`nightscout-connect`](https://github.com/nightscout/nightscout-connect)
Glooko source **does not work for EU accounts** — see
[nightscout-connect#14](https://github.com/nightscout/nightscout-connect/issues/14).

## Why the official bridge fails on EU accounts

`nightscout-connect` authenticates against Glooko's **JSON API**:

```
POST https://eu.api.glooko.com/api/v2/users/sign_in
```

Glooko's EU API now enforces **Rails CSRF protection** on that endpoint, so the request is
rejected before the credentials are even checked:

```
HTTP/1.1 422 Unprocessable Content
"The change you wanted was rejected (422)"   <-- ActionController::InvalidAuthenticityToken
```

The credentials are fine; the login mechanism is the problem.

## The fix — use the web login form instead

The browser doesn't hit the JSON API to log in — it submits the **HTML sign-in form**, which
carries a CSRF `authenticity_token`. Replicating that flow works perfectly:

1. `GET  https://<region>.my.glooko.com/users/sign_in?locale=en-GB`
   → scrape the hidden `authenticity_token` from the form, keep the session cookie
2. `POST https://<region>.my.glooko.com/users/sign_in?id=login_form&locale=en-GB`
   (form-encoded: `utf8=✓`, `authenticity_token`, `user[email]`, `user[password]`, `commit=Log in`)
   → `302` to `/` and an **authenticated session cookie**
3. The authenticated session now works on the API:
   - `GET /api/v3/session/users` → your profile incl. `glookoCode`
   - `GET /api/v2/pumps/normal_boluses?lastUpdatedAt=<ISO>&lastGuid=<guid>&limit=500`
     → `insulinDelivered`, `carbsInput`, `pumpTimestamp`, `guid`
   - same for `/api/v2/foods` and `/api/v2/insulins`

Those get mapped to Nightscout treatments (`Meal Bolus` / `Correction Bolus` / `Carb Correction`)
and POSTed to `/api/v1/treatments`, deduplicated by Glooko `guid`.

## Usage (Docker Compose)

```yaml
  glooko:
    image: python:3.12-slim
    container_name: nightscout-glooko
    restart: unless-stopped
    volumes:
      - ./glooko_uploader.py:/app/glooko_uploader.py:ro
      - glooko-state:/data
    environment:
      GLOOKO_EMAIL: you@example.com
      GLOOKO_PASSWORD: your-glooko-password
      GLOOKO_REGION: eu              # eu | default(US) | development
      NIGHTSCOUT_URL: http://nightscout:1337   # internal URL or https://your-ns-site
      NIGHTSCOUT_SECRET: your-nightscout-api-secret   # raw secret; the script SHA1-hashes it
      INTERVAL_MIN: "10"
      LOOKBACK_DAYS: "3"
    command: python /app/glooko_uploader.py

volumes:
  glooko-state:
```

No build step, no pip packages — pure Python standard library.

### Environment variables

| Var | Required | Default | Notes |
|-----|----------|---------|-------|
| `GLOOKO_EMAIL` | yes | | Your Glooko login email |
| `GLOOKO_PASSWORD` | yes | | Your Glooko password |
| `GLOOKO_REGION` | no | `eu` | `eu` = `eu.api.glooko.com`. Use `default` for US |
| `NIGHTSCOUT_URL` | yes | | e.g. `http://nightscout:1337` or `https://yoursite` |
| `NIGHTSCOUT_SECRET` | yes | | Your Nightscout `API_SECRET` (script hashes it to SHA-1) |
| `INTERVAL_MIN` | no | `10` | Poll interval (minutes) |
| `LOOKBACK_DAYS` | no | `3` | How far back to look each poll |

## Notes & caveats

- **Glooko is not real-time.** Treatments arrive on Glooko's own (batched) schedule, so this is a
  logbook sync, not a live loop feed. Pair it with a real-time CGM source (e.g. LibreLinkUp) for glucose.
- Tested against an **EU patient** Glooko account with pump bolus data (`normalBoluses`).
- Dedup is by Glooko `guid`, persisted to `/data/uploaded.json`.
- This is unofficial and reverse-engineered from observed traffic; Glooko could change their site at any time.


## Timezone note (important for UK/EU)

Glooko returns the **pump's local clock time** but labels it `Z` (UTC) with a
`pumpTimestampUtcOffset` of `+00:00`, even when your pump is on a non-UTC timezone.
If uploaded as-is, treatments land an hour (or more) ahead of your glucose curve.

This uploader corrects for **UK time (Europe/London, DST-aware)** in `to_utc()` —
i.e. it subtracts 1h during BST and 0h during GMT. **If you're not in the UK**, edit
`to_utc()` to apply your own local→UTC offset (or set your pump's clock to true UTC).

## License

MIT
