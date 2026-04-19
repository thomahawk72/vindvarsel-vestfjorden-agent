# Vindvarsel Vestfjorden

Agent som varsler når vinden i Vestfjorden (rundt Konglungen og Bjerkøya) er
østlig og over 4 m/s. Kjøres gratis av GitHub Actions hvert 30. minutt og
sender push-varsel via [ntfy.sh](https://ntfy.sh).

## Hva varsles?

Tre typer push-varsel:

- **Nå-varsel** (prioritet `high`, tittel "Østlig vind … NÅ"): når neste
  prognose-time (0–1,5 t) har vindretning 45°–135° og vind eller kast > 4 m/s.
- **Heads-up-varsel** (prioritet `default`, tittel "Heads-up: …"): når prognosen
  6–24 t fram viser et sammenhengende vindu som oppfyller samme kriterium.
- **Observert-varsel** (prioritet `high`, tittel "OBS: …"): når en faktisk
  måling fra nærmeste værstasjon (via MET Frost) oppfyller kriteriet nå. Fanger
  opp når prognosen bommer. Krever `FROST_CLIENT_ID` (se nedenfor) — hvis den
  ikke er satt, hopper agenten over observasjoner og sender kun prognose-varsel.

Samme event varsles kun én gang per type (dedup via `wind_agent/state.json`).

## Oppsett

### 1. Sett opp ntfy

1. Installer [ntfy-appen](https://ntfy.sh/) på mobilen (iOS/Android).
2. Velg et hemmelig topic-navn, f.eks. `vestfjorden-vind-xk29ab` (sånn at
   ikke andre kan sende varsler til deg).
3. Trykk "Subscribe" i appen og tast inn topicet.

### 2. Fork/klon dette repoet til din egen GitHub-konto

```bash
git clone <dette-repoet> vindvarsel-vestfjorden
cd vindvarsel-vestfjorden
# push til din egen GitHub-konto
```

### 3. Sett GitHub Secrets

Gå til `Settings → Secrets and variables → Actions` og legg til:

| Secret             | Verdi (eksempel)                                       | Obligatorisk |
| ------------------ | ------------------------------------------------------ | ------------ |
| `NTFY_TOPIC`       | `vestfjorden-vind-xk29ab`                              | Ja           |
| `MET_USER_AGENT`   | `VestfjordenVindAgent/1.0 din.epost@example.com`       | Ja           |
| `FROST_CLIENT_ID`  | UUID fra frost.met.no (se under)                       | Nei          |

MET krever en beskrivende `User-Agent` med kontakt-info —
se deres [ToS](https://api.met.no/doc/TermsOfService).

### Valgfritt: Sanntidsobservasjoner via Frost

For å aktivere "OBS:"-varsler basert på faktiske målinger fra nærmeste
værstasjon:

1. Registrer en gratis konto på
   https://frost.met.no/auth/requestCredentials.html (tar ~2 min).
2. Kopier `client_id` (en UUID).
3. Legg den inn som GitHub Secret `FROST_CLIENT_ID`.

Uten denne secret kjører agenten fortsatt, men bruker kun prognose.

### 4. Aktiver workflow

Workflowen er konfigurert i `.github/workflows/weather-check.yml` og kjøres:

- automatisk hvert 30. minutt (cron `*/30 * * * *`)
- manuelt via `Actions → Vindvarsel Vestfjorden → Run workflow`

Etter første vellykkede kjøring kan du teste varslingen ved å midlertidig
sette `WIND_THRESHOLD_MS = 0.0` i [`wind_agent/config.py`](wind_agent/config.py).

## Konfigurasjon

Alle terskler og koordinater ligger i
[`wind_agent/config.py`](wind_agent/config.py):

- `LOCATIONS`: Konglungen og Bjerkøya (finjuster koordinatene om ønskelig).
- `EAST_MIN_DEG` / `EAST_MAX_DEG`: 45°–135° (NØ–SØ). Stram inn til f.eks.
  70–110 for "strengt øst".
- `WIND_THRESHOLD_MS`: 4.0 m/s (gjelder både middelvind og kast). Kan
  overstyres uten kodeendring via GitHub Variable (se nedenfor).
- `FORECAST_WINDOW_START_H` / `FORECAST_WINDOW_END_H`: 6–24 t.

### Endre vindterskel uten å endre koden

1. Gå til `Settings → Secrets and variables → Actions → Variables-fanen`.
2. Klikk **"New repository variable"**.
3. Navn: `WIND_THRESHOLD_MS`, verdi: f.eks. `6.5`.
4. Lagre.

Neste kjøring (hver halvtime eller via manuell dispatch) bruker den nye verdien
automatisk. Scriptet logger gjeldende terskel ved starten av hver kjøring slik
at du kan verifisere i Actions-loggen.

Variabelen er valgfri — hvis den ikke er satt, brukes 4.0 fra `config.py`.

## Kjør lokalt

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export MET_USER_AGENT="VestfjordenVindAgent/1.0 din.epost@example.com"
export NTFY_TOPIC="vestfjorden-vind-xk29ab"

python -m wind_agent.check_wind
```

Scriptet logger til stdout og skriver oppdatert `wind_agent/state.json`.

## Filstruktur

```
wind_agent/
  __init__.py
  check_wind.py     # hovedinngang
  config.py         # koordinater, terskler, vinduer
  met_client.py     # MET Locationforecast + enkel cache
  evaluator.py      # kriterielogikk, now + forecast event
  notifier.py       # ntfy.sh push
  state.py          # dedup
  state.json        # persistert dedup-tilstand (commites)
.github/workflows/
  weather-check.yml # cron */30
requirements.txt
```

## Datakilde

[MET Norway Locationforecast 2.0](https://api.met.no/weatherapi/locationforecast/2.0/documentation)
(compact). Gratis, krever kun `User-Agent` med kontakt-info.

## Lisens

MIT — se `LICENSE` hvis du legger til en.
