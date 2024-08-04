# ac-remote-homeassistant
Remote control crutch for Hisense AC with HomeAssistant (via REST API).

## Hardware part
Hesense air conditioner remote control with `ESP8266`.

### Libs
Using [IRremoteESP8266](https://github.com/crankyoldgit/IRremoteESP826).

## Home Assistant part
Based on [Generic Thermostat](https://www.home-assistant.io/integrations/generic_thermostat/).

### Installation
Add folder `ac_rest_remote_control` to `config/custom_components`.

Example of usage in `configuration.yaml`:
```yaml
climate:
  - platform: ac_rest_remote_control
    name: AC Remote
    min_temp: 16
    max_temp: 30
    ac_mode: true
    target_temp: 24
    min_cycle_duration:
      seconds: 5
    keep_alive:
      seconds: 10
    initial_hvac_mode: "off"
    precision: 1
    rest_url: "http://<IP>/ac"
    rest_username: "???"
    rest_password: "???"
```
