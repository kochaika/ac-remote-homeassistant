"""Adds support for AC Remote units."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from typing import Any

import requests
from requests.auth import HTTPBasicAuth
import json

import voluptuous as vol

from homeassistant.components.climate import (
    ATTR_PRESET_MODE,
    PLATFORM_SCHEMA,
    PRESET_ACTIVITY,
    PRESET_AWAY,
    PRESET_COMFORT,
    PRESET_ECO,
    PRESET_HOME,
    PRESET_NONE,
    PRESET_SLEEP,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_TEMPERATURE,
    CONF_NAME,
    CONF_UNIQUE_ID,
    EVENT_HOMEASSISTANT_START,
    PRECISION_HALVES,
    PRECISION_TENTHS,
    PRECISION_WHOLE,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfTemperature,
)
from homeassistant.core import (
    DOMAIN as HA_DOMAIN,
    CoreState,
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)

import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.reload import async_setup_reload_service
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import DOMAIN, PLATFORMS

_LOGGER = logging.getLogger(__name__)

DEFAULT_NAME = "AC Remote"
DEFAULT_TARGET_TEMPERATURE = 24

CONF_MIN_TEMP = "min_temp"
CONF_MAX_TEMP = "max_temp"
CONF_TARGET_TEMP = "target_temp"
CONF_AC_MODE = "ac_mode"
CONF_MIN_DUR = "min_cycle_duration"
CONF_KEEP_ALIVE = "keep_alive"
CONF_INITIAL_HVAC_MODE = "initial_hvac_mode"
CONF_PRECISION = "precision"
CONF_TEMP_STEP = "target_temp_step"
CONF_REST_URL = "rest_url"
CONF_REST_USERNAME = "rest_username"
CONF_REST_PASSWORD = "rest_password"

CONF_PRESETS = {
    p: f"{p}_temp"
    for p in (
        PRESET_AWAY,
        PRESET_COMFORT,
        PRESET_ECO,
        PRESET_HOME,
        PRESET_SLEEP,
        PRESET_ACTIVITY,
    )
}

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Optional(CONF_AC_MODE): cv.boolean,
        vol.Optional(CONF_MAX_TEMP): vol.Coerce(float),
        vol.Optional(CONF_MIN_DUR): cv.positive_time_period,
        vol.Optional(CONF_MIN_TEMP): vol.Coerce(float),
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
        vol.Optional(CONF_TARGET_TEMP): vol.Coerce(float),
        vol.Optional(CONF_KEEP_ALIVE): cv.positive_time_period,
        vol.Optional(CONF_INITIAL_HVAC_MODE): vol.In(
            [HVACMode.COOL, HVACMode.HEAT, HVACMode.OFF]
        ),
        vol.Optional(CONF_PRECISION): vol.In(
            [PRECISION_TENTHS, PRECISION_HALVES, PRECISION_WHOLE]
        ),
        vol.Optional(CONF_TEMP_STEP): vol.In(
            [PRECISION_TENTHS, PRECISION_HALVES, PRECISION_WHOLE]
        ),
        vol.Optional(CONF_UNIQUE_ID): cv.string,

        vol.Required(CONF_REST_URL): cv.string,
        vol.Required(CONF_REST_USERNAME): cv.string,
        vol.Required(CONF_REST_PASSWORD): cv.string,
    }
).extend({vol.Optional(v): vol.Coerce(float) for (k, v) in CONF_PRESETS.items()})


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up the ac_remote thermostat platform."""

    await async_setup_reload_service(hass, DOMAIN, PLATFORMS)

    name: str = config[CONF_NAME]
    min_temp: float | None = config.get(CONF_MIN_TEMP)
    max_temp: float | None = config.get(CONF_MAX_TEMP)
    target_temp: float | None = config.get(CONF_TARGET_TEMP)
    ac_mode: bool | None = config.get(CONF_AC_MODE)
    min_cycle_duration: timedelta | None = config.get(CONF_MIN_DUR)
    keep_alive: timedelta | None = config.get(CONF_KEEP_ALIVE)
    initial_hvac_mode: HVACMode | None = config.get(CONF_INITIAL_HVAC_MODE)
    presets: dict[str, float] = {
        key: config[value] for key, value in CONF_PRESETS.items() if value in config
    }
    precision: float | None = config.get(CONF_PRECISION)
    target_temperature_step: float | None = config.get(CONF_TEMP_STEP)
    rest_url: str = config.get(CONF_REST_URL)
    username: str = config.get(CONF_REST_USERNAME)
    password: str = config.get(CONF_REST_PASSWORD)
    unit = hass.config.units.temperature_unit
    unique_id: str | None = config.get(CONF_UNIQUE_ID)

    async_add_entities(
        [
            ACRemoteControl(
                name,
                min_temp,
                max_temp,
                target_temp,
                ac_mode,
                min_cycle_duration,
                keep_alive,
                initial_hvac_mode,
                presets,
                precision,
                target_temperature_step,
                rest_url,
                username,
                password,
                unit,
                unique_id,
            )
        ]
    )


@dataclass
class ACState:
    temperature: int | None = None
    mode: HVACMode | None = None

class ACRemoteControl(ClimateEntity, RestoreEntity):
    """Representation of an ac_remote device."""

    _attr_should_poll = False
    _enable_turn_on_off_backwards_compatibility = False

    def __init__(
            self,
            name: str,
            min_temp: float | None,
            max_temp: float | None,
            target_temp: float | None,
            ac_mode: bool | None,
            min_cycle_duration: timedelta | None,
            keep_alive: timedelta | None,
            initial_hvac_mode: HVACMode | None,
            presets: dict[str, float],
            precision: float | None,
            target_temperature_step: float | None,
            rest_url: str,
            username: str,
            password: str,
            unit: UnitOfTemperature,
            unique_id: str | None,
    ) -> None:
        """Initialize the thermostat."""
        self._attr_name = name
        self.ac_mode = ac_mode
        self.min_cycle_duration = min_cycle_duration
        self._keep_alive = keep_alive
        self._hvac_mode = initial_hvac_mode
        self._saved_target_temp = target_temp or next(iter(presets.values()), None)
        self._temp_precision = precision
        self._temp_target_temperature_step = target_temperature_step
        if self.ac_mode:
            self._attr_hvac_modes = [HVACMode.COOL, HVACMode.OFF, HVACMode.HEAT]
        else:
            self._attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
        self._active = False
        self._temp_lock = asyncio.Lock()
        self._min_temp = min_temp
        self._max_temp = max_temp
        self._attr_preset_mode = PRESET_NONE
        self._target_temp = target_temp
        self._attr_temperature_unit = unit
        self._attr_unique_id = unique_id
        self._attr_supported_features = (
                ClimateEntityFeature.TARGET_TEMPERATURE
                | ClimateEntityFeature.TURN_OFF
                | ClimateEntityFeature.TURN_ON
        )
        if len(presets):
            self._attr_supported_features |= ClimateEntityFeature.PRESET_MODE
            self._attr_preset_modes = [PRESET_NONE, *presets.keys()]
        else:
            self._attr_preset_modes = [PRESET_NONE]
        self._presets = presets

        # REST Remote part:
        self._is_last_send_succeed = False
        self._last_send_state = None
        self._last_control_action_time = datetime.now()
        self._test_counter = 0
        self._last_state: ACState | None = None
        self._rest_url = rest_url
        self._username = username
        self._password = password

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added."""
        await super().async_added_to_hass()

        if self._keep_alive:
            self.async_on_remove(
                async_track_time_interval(
                    self.hass, self._async_control_heating, self._keep_alive
                )
            )

        @callback
        def _async_startup(_: Event | None = None) -> None:
            """Init on startup."""
            # self.hass.async_create_task(
            #     self._check_initial_state(), eager_start=True
            # )
            pass


        if self.hass.state is CoreState.running:
            _async_startup()
        else:
            self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_START, _async_startup)

        # Check If we have an old state
        if (old_state := await self.async_get_last_state()) is not None:
            # If we have no initial temperature, restore
            if self._target_temp is None:
                # If we have a previously saved temperature
                if old_state.attributes.get(ATTR_TEMPERATURE) is None:
                    # if self.ac_mode:
                    self._target_temp = DEFAULT_TARGET_TEMPERATURE
                    _LOGGER.warning(
                        "Undefined target temperature, falling back to %s",
                        self._target_temp,
                    )
                else:
                    self._target_temp = float(old_state.attributes[ATTR_TEMPERATURE])
            if (
                    self.preset_modes
                    and old_state.attributes.get(ATTR_PRESET_MODE) in self.preset_modes
            ):
                self._attr_preset_mode = old_state.attributes.get(ATTR_PRESET_MODE)
            if not self._hvac_mode and old_state.state:
                self._hvac_mode = HVACMode(old_state.state)

        else:
            # No previous state, try and restore defaults
            if self._target_temp is None:
                self._target_temp = DEFAULT_TARGET_TEMPERATURE
            _LOGGER.warning(
                "No previously saved temperature, setting to %s", self._target_temp
            )
        # Set default state to off
        if not self._hvac_mode:
            self._hvac_mode = HVACMode.OFF

        self._last_state = ACState(self.target_temperature, self.hvac_mode)


    @property
    def precision(self) -> float:
        """Return the precision of the system."""
        if self._temp_precision is not None:
            return self._temp_precision
        return super().precision

    @property
    def target_temperature_step(self) -> float:
        """Return the supported step of target temperature."""
        if self._temp_target_temperature_step is not None:
            return self._temp_target_temperature_step
        # if a target_temperature_step is not defined, fallback to equal the precision
        return self.precision

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Return current operation."""
        return self._hvac_mode

    @property
    def hvac_action(self) -> HVACAction:
        """Return the current running hvac operation if supported.
        Need to be one of CURRENT_HVAC_*.
        """
        if not self._is_last_send_succeed:
            return HVACAction.IDLE
        if self._hvac_mode == HVACMode.HEAT:
            return HVACAction.HEATING
        if self._hvac_mode == HVACMode.COOL:
            return HVACAction.COOLING
        return HVACAction.OFF

    @property
    def target_temperature(self) -> float | None:
        """Return the temperature we try to reach."""
        return self._target_temp

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set hvac mode."""
        if hvac_mode == HVACMode.HEAT:
            self._hvac_mode = HVACMode.HEAT
            await self._async_control_heating(force=True)
        elif hvac_mode == HVACMode.COOL:
            self._hvac_mode = HVACMode.COOL
            await self._async_control_heating(force=True)
        elif hvac_mode == HVACMode.OFF:
            self._hvac_mode = HVACMode.OFF
            await self._async_control_heating(force=True)
            # if self._is_device_active:
            #     await self._async_heater_turn_off()
        else:
            _LOGGER.error("Unrecognized hvac mode: %s", hvac_mode)
            return
        # Ensure we update the current operation after changing the mode
        self.async_write_ha_state()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature."""
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is None:
            return
        self._target_temp = temperature
        await self._async_control_heating(force=True)
        self.async_write_ha_state()

    @property
    def min_temp(self) -> float:
        """Return the minimum temperature."""
        if self._min_temp is not None:
            return self._min_temp

        # get default temp from super class
        return super().min_temp

    @property
    def max_temp(self) -> float:
        """Return the maximum temperature."""
        if self._max_temp is not None:
            return self._max_temp

        # Get default temp from super class
        return super().max_temp

    async def _check_initial_state(self) -> None:
        """Prevent the device from keep running if HVACMode.OFF."""
        if self._hvac_mode == HVACMode.OFF and self._is_device_active:
            _LOGGER.warning(
                (
                    "The climate mode is OFF, but the switch device is ON. Turning off"
                    " device"
                )
            )
            await self._async_heater_turn_off()

    async def _async_control_heating(
            self, time: datetime | None = None, force: bool = False
    ) -> None:
        """Check if we need to turn heating on or off."""
        async with self._temp_lock:
            if not self._active and None not in (
                    # self._cur_temp,
                    self._target_temp,
            ):
                self._active = True
                _LOGGER.info(
                    (
                        "Obtained current and target temperature. "
                        "AC Remote  active. %s"
                    ),
                    self._target_temp,
                )

            if not self._active:  # or self._hvac_mode == HVACMode.OFF:
                return

            # If the `force` argument is True, we
            # ignore `min_cycle_duration`.
            # If the `time` argument is not none, we were invoked for
            # keep-alive purposes, and `min_cycle_duration` is irrelevant.
            if force and time is None and self.min_cycle_duration:
                if self._is_device_active:
                    long_enough = (datetime.now() - self._last_control_action_time) > self.min_cycle_duration
                    if not long_enough:
                        return

            if self._is_device_active:
                await self._async_control_heating_command_sender()

    @property
    def _is_device_active(self) -> bool | None:
        """Since there is no feedback from the air conditioner, we consider it to be always active."""
        return True

    def _send_rest_command(self, payload: dict) -> None:
        """Sending payload to AC control device with REST handler"""
        credentials = HTTPBasicAuth(self._username, self._password)
        headers = {'Content-Type': 'application/json'}
        try:
            _LOGGER.info("Sending request: [%s]: %s", str(self._rest_url), str(payload))
            response = requests.post(self._rest_url, data=json.dumps(payload), headers=headers,
                                     auth=credentials, timeout=3)
            response.raise_for_status()
            self._is_last_send_succeed = True
        except Exception as e:
            _LOGGER.warning("A requests error occurred: %s", e)
            self._is_last_send_succeed = False

    async def _async_control_heating_command_sender(self):
        """Build and send new json-command to AC if configuration was changed since last sending"""
        cur_state = ACState(self.target_temperature, self.hvac_mode)
        if self._last_state != cur_state:
            _LOGGER.info("Something changed: %s", str(cur_state))
            power_toggle = ((self._last_state.mode == HVACMode.OFF and cur_state.mode != HVACMode.OFF) or
                            (self._last_state.mode != HVACMode.OFF and cur_state.mode == HVACMode.OFF))
            payload = {
                "power_toggle": power_toggle,
                "power": self._hvac_mode != HVACMode.OFF,
                "mode": "COOL_MODE" if self._hvac_mode == HVACMode.COOL else "HEAT_MODE",
                "fan": "FAN_AUTO",
                "temperature": self.target_temperature
            }
            await self.hass.async_add_executor_job(self._send_rest_command, payload)
            self._last_state = cur_state
            self._last_control_action_time = datetime.now()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set new preset mode."""
        _LOGGER.info("Setting new preset mode: %s", preset_mode)
        if preset_mode not in (self.preset_modes or []):
            raise ValueError(
                f"Got unsupported preset_mode {preset_mode}. Must be one of"
                f" {self.preset_modes}"
            )
        if preset_mode == self._attr_preset_mode:
            # I don't think we need to call async_write_ha_state if we didn't change the state
            return
        if preset_mode == PRESET_NONE:
            self._attr_preset_mode = PRESET_NONE
            self._target_temp = self._saved_target_temp
            await self._async_control_heating(force=False)
        else:
            if self._attr_preset_mode == PRESET_NONE:
                self._saved_target_temp = self._target_temp
            self._attr_preset_mode = preset_mode
            self._target_temp = self._presets[preset_mode]
            await self._async_control_heating(force=False)
        self.async_write_ha_state()
