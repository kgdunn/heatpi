# heatpi: home energy and heating logger

Everything writes into one SQLite file, `p1.sqlite`, with all times in UTC.

Background, findings and open items: [NOTES.md](NOTES.md). Working notes for Claude Code: [CLAUDE.md](CLAUDE.md).

| File | What it does | How it runs | Table |
|---|---|---|---|
| `p1_logger.py` | Reads the HomeWizard P1 meter every 10 s | service `p1-logger` | `p1` |
| `outdoor_temp.py` | Hourly data of 8 nearby Luchtwachters sensors (RIVM API): temperature, PM2.5 raw and calibrated | cron, hourly | `street_temp`, `street_air` |
| `street_consensus.py` | One sun-robust outdoor temperature, plus sun on our facade | cron, after the above | `street_consensus` |
| `loudness_logger.py` | Grove loudness sensor on A0, one row per minute (optional) | service `loudness-logger` | `loudness` |
| `room_logger.py` | Grove temperature & humidity sensor (DHT22), one row per minute | service `room-logger` | `room` |
| `evohome_logger.py` | Every evohome zone's temperature, setpoint and mode, every 5 min, and its weekly schedule every 6 h (TCC cloud, read only) | service `evohome-logger` | `zones`, `schedule` |
| `knmi_temp.py` | KNMI Rotterdam every 10 min (EDR API, free key in `~/.knmi`): air temperature, and sunshine, wind, rain and soil temperature for the heat balance | cron, every 10 min | `knmi_temp`, `knmi_live` |
| `knmi_hourly.py` | KNMI Rotterdam per hour, no key: sunshine, wind, rain (about 2 days late) | cron, every 6 h | `knmi_hourly` |
| `compare_temps.py` | One-off check: our sensor vs KNMI Rotterdam, with a chart | by hand | – |
| `hp_check.py` | Heat pump runs, starts and kWh per day, with an hourly map | by hand | – |
| `steps.py` | Sudden jumps in power per phase, and matching pairs as appliance runs: which appliance is on which phase | by hand | – |
| `recent.py` | Last hours in steps: power, heat-pump share, room climate, loudness | by hand | – |
| `frost_rule.py` | On a frosty night, lifts the living room's low setpoint slot to 18 °C with a temporary evohome override that ends with the slot; notes each change in `events` | cron, every 15 min (`.venv`) | `events` |
| `note.py` | Log a settings change (`python3 note.py "..."`), or list them | by hand | `events` |
| `deviation.py` | Room minus setpoint per zone: summary and over time | by hand | – |
| `leadtime.py` | Warm-up lead time per setpoint step-up, with outdoor temperature and heat-pump power | by hand | – |
| `insights.py` | Loudness vs heat pump, and energy before/after each logged change | by hand | – |
| `balance.py` | Heat balance per day: heat in, the losses we can compute, and what is left over | by hand, and in the dashboard | – |
| `house.py` | The house for the balance: every part that loses heat, its area, U-value and where the heat goes | by hand (prints the table) | – |
| `dashboard.py` + `web/` | Web pages for the iPad: http://heatpi.lan:8080 (live view), `/balance.html` (heat balance) and `/schedule.html` (room schedules). Plotly from a CDN, data as JSON | service `dashboard` | – |
| `status.sh` | All-in-one check: loggers, data freshness, notes, heat pump, last 6 h | by hand | – |
| `dblocks.py` | Who holds the database's write lock, and for how long (when a logger says "database is locked") | by hand | – |
| `update.sh` | Pull the latest code from GitHub; restarts only the services whose code changed | by hand | – |

The loggers use only the Python standard library. `street_consensus.py` needs pandas,
and `compare_temps.py` needs pandas and matplotlib.

## Setup (on the Pi, in `~/p1-logger`)

```bash
# 1. Packages
sudo apt install -y python3-pandas python3-matplotlib python3-smbus2 i2c-tools avahi-daemon

# 2. Token for the P1 meter: press the meter's button when asked
python3 p1_logger.py token 192.168.86.38

# 3. Quick test: Ctrl-C after ~70 s, then check rows
python3 p1_logger.py log 192.168.86.38 --every 10
python3 -c "import sqlite3; print(sqlite3.connect('p1.sqlite').execute('select count(*), max(ts_utc), max(power_w) from p1').fetchone())"

# 4. Run the P1 logger permanently
sudo cp p1-logger.service /etc/systemd/system/
sudo systemctl enable --now p1-logger
systemctl status p1-logger --no-pager

# 5. Street temperatures: the first run backfills from Sep 2025 (a few minutes)
python3 outdoor_temp.py && python3 street_consensus.py

# 6. ...and every hour after that
(crontab -l 2>/dev/null; echo '15 * * * * cd ~/p1-logger && { python3 outdoor_temp.py && python3 street_consensus.py; } >> street.log 2>&1') | crontab -
```

### Optional: loudness sensor (Grove Base Hat, slot A0, hat switch on 3.3 V)

```bash
sudo raspi-config nonint do_i2c 0
i2cdetect -y -a 1              # expect "04" in the top row (-a: 0x03-0x07 are hidden otherwise)
python3 loudness_logger.py     # Ctrl-C after two minutes
sudo cp loudness-logger.service /etc/systemd/system/
sudo systemctl enable --now loudness-logger
```

### Optional: room temperature & humidity (Grove DHT22 on a digital port)

```bash
echo "dtoverlay=dht11,gpiopin=12" | sudo tee -a /boot/firmware/config.txt   # 12 = the Grove port number (D12)
sudo reboot
cat /sys/bus/iio/devices/iio:device*/in_temp_input   # e.g. 21300 = 21.3 °C (retry if it times out)
sudo cp room-logger.service /etc/systemd/system/
sudo systemctl enable --now room-logger
```

### Optional: evohome room temperatures and setpoints (needs the evohome on the TCC cloud)

```bash
python3 -m venv --system-site-packages ~/p1-logger/.venv
~/p1-logger/.venv/bin/pip install 'evohome-async[cli]'
printf '%s\n%s\n' 'your-tcc-email' 'your-tcc-password' > ~/.evohome && chmod 600 ~/.evohome
~/p1-logger/.venv/bin/python evohome_logger.py      # Ctrl-C after the first poll
sudo cp evohome-logger.service /etc/systemd/system/
sudo systemctl enable --now evohome-logger
```

### Optional: KNMI every 10 minutes (free API key): temperature, and live sunshine for the balance

```bash
nano ~/.knmi && chmod 600 ~/.knmi             # paste the EDR API key from developer.dataplatform.knmi.nl
python3 knmi_temp.py --find rotterdam         # station id, expect 0-20000-0-06344
python3 knmi_temp.py --show
(crontab -l; echo '*/10 * * * * cd ~/p1-logger && python3 knmi_temp.py >> knmi.log 2>&1') | crontab -
```

### Heat balance: sunshine, wind and rain from KNMI (no key)

```bash
python3 knmi_hourly.py                        # first run backfills from Sep 2025 (a few seconds)
(crontab -l; echo '25 */6 * * * cd ~/p1-logger && python3 knmi_hourly.py >> knmi.log 2>&1') | crontab -
python3 balance.py --days 7                   # the balance as a table; the page is /balance.html
python3 house.py                              # the areas and U-values it uses
```

The balance turns the heat pump's electricity into heat with a COP. Read the two "this month"
counters on the Vaillant controller now and then, and log them; the balance then uses the measured
COP between two readings instead of its default:

```bash
python3 note.py "counters: heating=253 yield=319"
```

### Frost rule: the first one that writes to evohome

On a night with frost in the forecast, a setpoint slot below 18 °C in the living room is lifted to
18 °C until the slot ends, so the room is not too cold to recover by morning. It only ever raises a
setpoint, only with an override that ends by itself, and leaves the zone alone after any manual
change or in away mode. Try it first; it writes nothing:

```bash
cd ~/p1-logger && .venv/bin/python frost_rule.py --dry-run
```

Then switch it on, every 15 minutes (remove the line again to switch it off):

```bash
(crontab -l; echo '*/15 * * * * cd ~/p1-logger && .venv/bin/python frost_rule.py >> frost.log 2>&1') | crontab -
```

## Updating

The code lives on GitHub; the data, logs and keys (`~/.p1_token`, `~/.evohome`, `~/.knmi`)
stay on the Pi and are never committed.

```bash
~/p1-logger/update.sh                         # git pull, then restart what changed
```

## Checking on things

```bash
./status.sh                                   # everything at once
journalctl -u p1-logger -n 20 --no-pager      # logger messages
tail street.log                               # hourly street-temperature runs
python3 -c "
import sqlite3; db = sqlite3.connect('p1.sqlite')
for t in ('p1', 'street_temp', 'street_air', 'street_consensus', 'loudness', 'room'):
    try: print(t, db.execute(f'select count(*), max(ts_utc) from {t}').fetchone())
    except sqlite3.OperationalError: print(t, '(not yet)')"
```

## Notes

- **Your sensor is de-4190.** de-1093 and de-4064 sit against warm walls: they are stored,
  but left out of the consensus.
- **`facade_excess`** in `street_consensus` is our sensor's reading minus the consensus.
  It is a free measure of sun on our facade, for the heat-loss model.
- **The P1 meter's address is fixed** at 192.168.86.38 by a DHCP reservation.
- **The meter's tokens are revocable.** Each device has its own: `local/pi-logger`
  on the Pi, `local/kevin-mac` on the Mac.
