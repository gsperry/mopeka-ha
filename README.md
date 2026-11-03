# Mopeka HA

A Home Assistant custom integration that reads Mopeka tank sensors from Mopeka Cloud, the
same service Mopeka's mobile apps use. It works with accounts from the **Mopeka Co-Pilot**
app and from the older **Tank Check** app, and detects which one you have.

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
when its readings reach Mopeka Cloud, for example through:

- a Mopeka cellular bridge or Wi-Fi gateway, or
- the Co-Pilot app, which uploads readings from sensors near your phone.

A quick check: if the Mopeka app shows fresh readings for a tank while your phone is nowhere
near it, that sensor reports to the cloud and will work here.

The integration is developed against Mopeka Pro Check propane sensors. Level readings are
converted using propane's speed of sound, so other liquids will read incorrectly. Other
Mopeka sensor models that report to the same account may work, but they are untested.

## Requirements

- Home Assistant 2026.9 or newer, with [HACS](https://hacs.xyz/) installed.
- A Mopeka Co-Pilot or Tank Check account that signs in with an email address and
  password. Accounts that use only Sign in with Apple or Google have no password to enter
  here.
- At least one sensor reporting to Mopeka Cloud through a gateway.
- Outbound internet access from Home Assistant to Amazon Cognito (`us-east-1`) and to
  `dev.mopeka.cloud` (Co-Pilot accounts) or `gateway.mopeka.cloud` (Tank Check accounts).

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
3. Enter the email address and password you use for the Mopeka app.

Each tank on the account becomes a device. Tank Check accounts use the names from the app.
Co-Pilot keeps tank names on your phone rather than in the cloud, so those tanks are named
`Mopeka` plus the last four characters of the sensor's address, such as `Mopeka 6F01`.
Rename them in Home Assistant if you like. Each tank's sensors are listed under its device.

You can add more than one Mopeka account. Each account is a separate integration entry.

### Options

Open the integration entry and select **Configure** to change:

| Option | Description | Default |
|---|---|---|
| Update interval | How often to poll Mopeka Cloud, in minutes (1 to 60). | 5 |
| Tanks to leave out | Tanks not to add to Home Assistant, for example tanks the built-in Mopeka Bluetooth integration already provides. Their devices and entities are removed. | None |

### Changing your password or email

- If you change your password in the Mopeka app, Home Assistant notices the next
  time it polls. **Settings > Devices & services** then shows Mopeka HA as needing
  attention; select **Reconfigure** on it and enter the new password.
- To change the email address for the same Mopeka account, use **Reconfigure**. To add a
  different account, add the integration again.

## Entities

Each tank with a cloud reading gets these sensors. Units follow your Home Assistant unit
system, so level shows in inches and temperature in °F on a US system. You can change the
unit of any sensor in its settings.

| Sensor | Description |
|---|---|
| Level | Height of the liquid in the tank, calculated from the sensor's reading. |
| Fill level | How full the tank is, in percent, from the level and the tank's size and orientation. Tank Check accounts only. |
| Volume | Estimated liquid volume, from the fill level and the tank type set in the app. Tank Check accounts only. |
| Temperature | Temperature at the sensor. |
| Battery | Sensor battery level, estimated from its voltage. Diagnostic. |
| Last reading | When the sensor took its latest reading. Diagnostic. |
| Battery voltage | Sensor battery voltage. Diagnostic, disabled by default. |
| Reading quality | Mopeka's quality value for the reading. Its scale depends on the platform. Diagnostic, disabled by default. |

Disabled sensors can be enabled from the device page.

For **Co-Pilot accounts**, Mopeka Cloud stores the readings but not the tank settings, so
fill level and volume are not available.

### Upgrading from version 1.x

Version 2.0 replaces the list of measurements chosen during setup with the sensors above.
Existing entities keep their entity IDs and history:

- `level_cm` or `level_inches` becomes Level, and `temperature_c` or `temperature_f`
  becomes Temperature, still shown in the unit you had. If you had both units, the one
  matching your unit system is kept and the other removed.
- `volume_liters` or `volume_gallons_us` becomes Volume, and `updated_human_readable`
  becomes Last reading.
- `tank_type`, `tank_height`, `vertical`, `propaneButaneRatio`, and
  `volume_original_unit` are removed.

Long-term statistics for temperature and reading quality may report a unit change once,
because version 1.x used `C` and `F` instead of `°C` and `°F`, and showed reading quality
as a percentage. Home Assistant offers to fix this under **Developer tools > Statistics**.

## Limitations

- **Unofficial API.** See the note at the top. Changes on Mopeka's side can break the
  integration until it is updated.
- **Cloud dependency.** If Mopeka Cloud or your internet connection is down, entities
  become unavailable. Nothing is read locally.
- **Polling.** The integration asks the cloud for new data on a fixed interval. A new
  value appears in Home Assistant only after the gateway has uploaded it *and* the next
  poll has run, so readings can lag behind the sensor. Gateways upload on their own
  schedule, and a cellular bridge may upload less often than a Wi-Fi gateway.
- **Calculated values.** Level is calculated from the sensor's raw echo time, using
  the same propane conversion as Home Assistant's built-in Mopeka integration. Fill
  percentage and volume are calculated from the level and, for Tank Check accounts, the
  tank settings in the app.
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
| Mopeka did not accept this email and password | Mopeka's sign-in service rejected the credentials, or the account needs a step such as a password reset or multi-factor code. | Sign in to the Mopeka app with the same details. Complete any step it asks for, then try again. |
| Cannot reach Mopeka Cloud | A network error, a timeout, or a temporary problem or rate limit at Mopeka. | Check Home Assistant's internet access and try again in a few minutes. |
| Unexpected error | Mopeka responded in a way the integration does not understand. | Collect a debug log and open an issue. |

The email address is matched without regard to capitalization or surrounding spaces.

### A tank is missing

- Check that the tank appears in the Mopeka app and shows recent readings.
- Tanks that have never uploaded a reading to the cloud are skipped.
- Check that the tank is not selected under **Tanks to leave out** in **Configure**.

### Values are not updating

- Compare the tank's **Last reading** sensor with the app. If the app is also out of
  date, the sensor's readings are not reaching the cloud. Check the bridge's power and
  connection, or open the app near the sensor.
- Check the Home Assistant log for errors from `custom_components.mopeka_ha`.

### Checking your account outside Home Assistant

The repository includes `scripts/live_check.py`, which signs in with the integration's own
client and prints what Mopeka Cloud returns for each tank, using placeholder names. It needs
Python and [uv](https://docs.astral.sh/uv/). Copy `.env.example` to `.env`, enter your
credentials, then run:

```bash
uv run scripts/live_check.py
```

In the Home Assistant OS terminal add-on, add `--managed-python --python 3.13` after
`uv run`. The add-on's package index only carries packages built for Home Assistant's own
Python version, so uv must download a matching Python itself.

### Reporting a bug

Open an [issue](https://github.com/gsperry/mopeka-ha/issues) and include your Home
Assistant version, the integration version, your sensor and gateway models, and a debug
log with personal details removed.

## Data and privacy

Your Mopeka email and password are stored in Home Assistant's configuration storage, like
any other integration's credentials. They are sent only to Amazon Cognito, which Mopeka
uses for sign-in. Sensor data is requested only from Mopeka Cloud (`dev.mopeka.cloud` or
`gateway.mopeka.cloud`). Requests to Mopeka Cloud identify themselves as Mopeka's apps,
because the service is built for them. The integration only reads data: it never changes
devices or settings on your account, and sends no data anywhere else.

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
