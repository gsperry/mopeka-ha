# Mopeka HA

A Home Assistant custom integration that reads Mopeka tank sensors from Mopeka Cloud, the
same service the Tank Check mobile app uses.

Use it for sensors that report through a Mopeka gateway and are out of Bluetooth range of
your Home Assistant host. For sensors within Bluetooth range, use Home Assistant's built-in
[Mopeka integration](https://www.home-assistant.io/integrations/mopeka/) instead: it is
local, faster, and does not depend on a cloud service.

> **Unofficial.** Mopeka does not publish or support this API. The integration signs in
> the same way the mobile app does, and it can stop working without notice if Mopeka
> changes its service.

## Contents

- [Supported hardware](#supported-hardware)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Entities](#entities)
- [Limitations](#limitations)
- [Troubleshooting](#troubleshooting)
- [Data and privacy](#data-and-privacy)
- [Removal](#removal)
- [Contributing](#contributing)
- [Credits and license](#credits-and-license)

## Supported hardware

The integration reads whatever your Mopeka account has in the cloud. A sensor is supported
when a Mopeka gateway uploads its readings, for example:

- a Mopeka Wi-Fi gateway, or
- a Mopeka cellular bridge.

A quick check: if the Tank Check app shows fresh readings for a tank while your phone is
nowhere near it, that sensor reports to the cloud and will work here.

The integration is developed against Mopeka Pro Check propane sensors. Other Mopeka sensor
models that report to the same account may work, but they are untested.

## Requirements

- Home Assistant with [HACS](https://hacs.xyz/) installed.
- A Mopeka account that signs in with an email address and password. Accounts that use
  only Sign in with Apple or Google have no password to enter here.
- At least one sensor reporting to Mopeka Cloud through a gateway.
- Outbound internet access from Home Assistant to Amazon Cognito (`us-east-1`) and
  `gateway.mopeka.cloud`.

## Installation

### HACS (custom repository)

1. In Home Assistant, open **HACS**.
2. Open the menu (three dots, top right) and choose **Custom repositories**.
3. Enter `https://github.com/gsperry/mopeka-ha`, set the type to **Integration**, and
   select **Add**.
4. Search HACS for **Mopeka HA**, open it, and select **Download**.
5. Restart Home Assistant.

[![Open this repository in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=gsperry&repository=mopeka-ha&category=integration)

### Manual

1. Download the latest release from the
   [releases page](https://github.com/gsperry/mopeka-ha/releases).
2. Copy `custom_components/mopeka_ha` into the `custom_components` folder of your Home
   Assistant configuration directory.
3. Restart Home Assistant.

Home Assistant installs the integration's Python requirements automatically on first load.

## Configuration

All configuration is done in the UI. There is no YAML configuration.

1. Go to **Settings > Devices & services** and select **Add integration**.
2. Search for **Mopeka HA**.
3. Enter the email address and password you use for the Tank Check app.
4. Choose which measurements to create as entities. The form shows the current value of
   each one so you can see what your sensors report. Select at least one.

Each tank on the account becomes a device, named as it is in the Tank Check app. Each
selected measurement becomes a sensor entity on that device.

You can add more than one Mopeka account. Each account is a separate integration entry.

### Options

Open the integration entry and select **Configure** to change:

| Option | Description | Default |
|---|---|---|
| Update interval | How often to poll Mopeka Cloud, in minutes (1 to 60). | 5 |
| Tanks to leave out | Tanks not to add to Home Assistant, for example tanks the built-in Mopeka Bluetooth integration already provides. Their devices and entities are removed. | None |
| Measurements | Which measurements to create as entities. Deselecting one removes its entity. | As chosen during setup |

### Changing your password or email

- If you change your password in the Tank Check app, Home Assistant notices the next
  time it polls. **Settings > Devices & services** then shows Mopeka HA as needing
  attention; select **Reconfigure** on it and enter the new password.
- To change the email address for the same Mopeka account, use **Reconfigure**. To add a
  different account, add the integration again.

## Entities

Entities are created only for the measurements you select. Each is created on every tank
that reports that measurement.

| Measurement | Unit | Description |
|---|---|---|
| `fill_percent` | % | How full the tank is. Calculated by the integration from the liquid level and the tank size and orientation. It can differ slightly from the Tank Check app. |
| `level_cm` | cm | Liquid level. |
| `level_inches` | in | Liquid level. |
| `volume_liters` | L | Estimated liquid volume, based on the tank type set in the app. |
| `volume_gallons_us` | gal | Estimated liquid volume, based on the tank type set in the app. |
| `volume_original_unit` | | Unit of the tank capacity set in the app, such as `lb` or `l`. |
| `tank_type` | | Tank type set in the app, such as `20lb`. |
| `tank_height` | m | Tank height used for the level calculation. |
| `vertical` | | `True` for a vertical tank, `False` for a horizontal tank. |
| `propaneButaneRatio` | | Propane/butane mix set in the app. |
| `temperature_c` | °C | Temperature at the sensor. |
| `temperature_f` | °F | Temperature at the sensor. |
| `battery_voltage` | V | Sensor battery voltage. |
| `signal_quality` | % | Quality of the sensor's ultrasonic reading. Low values mean the level reading may be unreliable. |
| `updated_human_readable` | | How long ago the sensor last reported, as text (for example `5 min ago`). |

## Limitations

- **Unofficial API.** See the note at the top. Changes on Mopeka's side can break the
  integration until it is updated.
- **Cloud dependency.** If Mopeka Cloud or your internet connection is down, entities
  become unavailable. Nothing is read locally.
- **Polling.** The integration asks the cloud for new data on a fixed interval. A new
  value appears in Home Assistant only after the gateway has uploaded it *and* the next
  poll has run, so readings can lag behind the sensor. Gateways upload on their own
  schedule, and a cellular bridge may upload less often than a Wi-Fi gateway.
- **Calculated values.** Fill percentage and volume are calculated by the integration,
  not reported by Mopeka, and depend on the tank settings in the app being correct.
- **Bluetooth duplicates.** A sensor that is in your cloud account *and* in Bluetooth range
  of Home Assistant appears twice: once from this integration and once from the built-in
  Mopeka integration. Use the **Tanks to leave out** option to drop it from this one.
- **Tanks without cloud readings.** Sensors registered to your account that have never
  uploaded a reading through a gateway are skipped.

## Troubleshooting

### Turn on debug logging

Debug logs are the first thing to collect for any problem.

1. Go to **Settings > Devices & services > Mopeka HA**.
2. Select **Enable debug logging** from the menu (three dots).
3. Reproduce the problem.
4. Select **Disable debug logging**. Home Assistant downloads the log file.

To log from Home Assistant startup instead, add this to `configuration.yaml` and restart:

```yaml
logger:
  logs:
    custom_components.mopeka_ha: debug
```

The integration removes passwords, tokens, and your email address from its log
messages. Tank names and sensor addresses are not removed; edit them out before sharing if
you prefer.

### Errors when adding the integration

Each error is also written to the Home Assistant log with the reason Mopeka gave, even
without debug logging.

| Message | Meaning | What to do |
|---|---|---|
| Mopeka did not accept this email and password | Mopeka's sign-in service rejected the credentials, or the account needs a step such as a password reset or multi-factor code. | Sign in to the Tank Check app with the same details. Complete any step it asks for, then try again. |
| Cannot reach Mopeka Cloud | A network error, a timeout, or a temporary problem or rate limit at Mopeka. | Check Home Assistant's internet access and try again in a few minutes. |
| Unexpected error | Mopeka responded in a way the integration does not understand. | Collect a debug log and open an issue. |

The email address is matched without regard to capitalization or surrounding spaces.

### A tank is missing

- Check that the tank appears in the Tank Check app and shows recent readings.
- Tanks that have never uploaded a reading to the cloud are skipped.
- Entities are created only for measurements you selected. Open **Configure** to add more.

### Values are not updating

- Compare the `updated_human_readable` value with the app. If the app is also out of
  date, the gateway is not uploading. Check the gateway's power and connection.
- Check the Home Assistant log for errors from `custom_components.mopeka_ha`.

### Checking your account outside Home Assistant

The repository includes `scripts/live_check.py`, which signs in with the integration's own
client and prints what Mopeka Cloud returns for each tank, using placeholder names. It needs
Python and [uv](https://docs.astral.sh/uv/). Copy `.env.example` to `.env`, enter your
credentials, then run:

```bash
uv run --with aiohttp --with pycognito scripts/live_check.py
```

### Reporting a bug

Open an [issue](https://github.com/gsperry/mopeka-ha/issues) and include your Home
Assistant version, the integration version, your sensor and gateway models, and a debug
log with personal details removed.

## Data and privacy

Your Mopeka email and password are stored in Home Assistant's configuration storage, like
any other integration's credentials. They are sent only to Amazon Cognito, which Mopeka
uses for sign-in. Sensor data is requested only from `gateway.mopeka.cloud`. The
integration sends no data anywhere else.

## Removal

1. Go to **Settings > Devices & services > Mopeka HA**.
2. For each entry, open the menu (three dots) and select **Delete**.
3. To remove the files as well, uninstall **Mopeka HA** in HACS and restart Home
   Assistant.

## Contributing

Bug reports and pull requests are welcome. Please open an issue to discuss larger changes
before starting work.

## Credits and license

This project is a fork of [MedVedZot/mopeka-ha](https://github.com/MedVedZot/mopeka-ha)
by Aleksandr Zhuravlev. See [CREDITS.md](CREDITS.md) for details.

Released under the [MIT License](LICENSE).

Mopeka, Pro Check, and Tank Check are trademarks of their respective owners. This project
is not affiliated with, endorsed by, or supported by Mopeka.
