"""
Support for the Hitachi AC.
Write By  hzcoolwind
2018.5.18
version:  1.0.0
For more details about this platform, please refer to the documentation at
https://home-assistant.io/components/climate./
"""
import logging
import re
from datetime import timedelta
from base64 import b64encode, b64decode
import asyncio
import binascii
import socket

import voluptuous as vol

from homeassistant.core import callback
from homeassistant.components.climate import (
    ATTR_CURRENT_TEMPERATURE, ATTR_FAN_MODE, ATTR_OPERATION_MODE,
    PLATFORM_SCHEMA, STATE_COOL, STATE_DRY,
    STATE_FAN_ONLY, STATE_HEAT, STATE_OFF, SUPPORT_FAN_MODE,
    SUPPORT_OPERATION_MODE, SUPPORT_TARGET_TEMPERATURE,
    ClimateDevice)

from homeassistant.const import (
    TEMP_CELSIUS, ATTR_TEMPERATURE, ATTR_UNIT_OF_MEASUREMENT,
    CONF_NAME, CONF_HOST, CONF_MAC, CONF_TIMEOUT)
from homeassistant.helpers import condition
from homeassistant.helpers.event import (
    async_track_state_change, async_track_time_interval)
import homeassistant.helpers.config_validation as cv

REQUIREMENTS = ['broadlink', 'sensor']

_LOGGER = logging.getLogger(__name__)

DEFAULT_TOLERANCE = 0.3
DEFAULT_NAME = 'Hitachi Thermostat'

DEFAULT_TIMEOUT = 10
DEFAULT_RETRY = 3

DEFAULT_MIN_TMEP = 17
DEFAULT_MAX_TMEP = 30
DEFAULT_STEP = 1
DEFAULT_TEMP = 25
DEFAULT_FAN = 'Low'

CONF_SENSOR = 'target_sensor'
CONF_TARGET_TEMP = 'target_temp'
CONF_TARGET_FAN = 'target_fan'


PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_HOST): cv.string,
    vol.Required(CONF_MAC): cv.string,
    vol.Optional(CONF_TIMEOUT, default=DEFAULT_TIMEOUT): cv.positive_int,
    vol.Required(CONF_SENSOR): cv.entity_id,
    vol.Optional(CONF_TARGET_TEMP, default=DEFAULT_TEMP): vol.Coerce(float),
    vol.Optional(CONF_TARGET_FAN, default=DEFAULT_FAN): cv.string
})

SUPPORT_FLAGS = (SUPPORT_TARGET_TEMPERATURE | SUPPORT_OPERATION_MODE | SUPPORT_FAN_MODE)


@asyncio.coroutine
def async_setup_platform(hass, config, async_add_devices, discovery_info=None):
    """Set up the generic thermostat platform."""
    import broadlink
    ip_addr = config.get(CONF_HOST)
    mac_addr = binascii.unhexlify(
        config.get(CONF_MAC).encode().replace(b':', b''))

    name = config.get(CONF_NAME)
    sensor_entity_id = config.get(CONF_SENSOR)
    target_temp = config.get(CONF_TARGET_TEMP)
    target_fan = config.get(CONF_TARGET_FAN)

    broadlink_device = broadlink.rm((ip_addr, 80), mac_addr, "")
    broadlink_device.timeout = config.get(CONF_TIMEOUT)
    try:
        broadlink_device.auth()
    except socket.timeout:
        _LOGGER.error("Failed to connect to device")
    async_add_devices([HitachiClimate(hass, name, target_temp, 'Low',  'off', broadlink_device, sensor_entity_id)])

class HitachiClimate(ClimateDevice):
    """Representation of a Hitachi AC."""

    def __init__(self, hass, name, target_temperature,
                 current_fan_mode, current_operation,
                 broadlink_device, sensor_entity_id):

        """Initialize the climate device."""
        self.hass = hass
        self._name = name if name else DEFAULT_NAME
        self._target_temperature = target_temperature
        self._current_fan_mode = current_fan_mode
        self._current_operation = current_operation
        self._last_operation = current_operation
        self._fan_list = ['Low', 'Middle', 'High']
        self._operation_list = [STATE_COOL, STATE_HEAT, STATE_DRY, STATE_FAN_ONLY, STATE_OFF]
        self._max_temp = DEFAULT_MAX_TMEP
        self._min_temp = DEFAULT_MIN_TMEP
        self._target_temp_step = DEFAULT_STEP

        self._unit_of_measurement = TEMP_CELSIUS
        self._current_temperature = None

        self._device = broadlink_device

        async_track_state_change(
            hass, sensor_entity_id, self._async_sensor_changed)

        sensor_state = hass.states.get(sensor_entity_id)
        if sensor_state:
            self._async_update_temp(sensor_state)

    @callback
    def _async_update_temp(self, state):
        """Update thermostat with latest state from sensor."""
        unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)

        try:
            self._current_temperature = self.hass.config.units.temperature(
                float(state.state), unit)
        except ValueError as ex:
            _LOGGER.error('Unable to update from sensor: %s', ex)

    @asyncio.coroutine
    def _async_sensor_changed(self, entity_id, old_state, new_state):
        """Handle temperature changes."""
        if new_state is None:
            return

        self._async_update_temp(new_state)
        yield from self.async_update_ha_state()

    @property
    def supported_features(self):
        """Return the list of supported features."""
        return SUPPORT_FLAGS

    @property
    def name(self):
        """Return the name of the thermostat, if any."""
        return self._name

    @property
    def temperature_unit(self):
        """Return the unit of measurement which this thermostat uses."""
        return TEMP_CELSIUS

    @property
    def current_temperature(self):
        """Return the current temperature."""
        return self._current_temperature

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        return self._target_temperature

    @property
    def target_temperature_step(self):
        return self._target_temp_step

    @property
    def current_operation(self):
        """Return current operation ie. heat, cool, idle."""
        return self._current_operation

    @property
    def operation_list(self):
        """Return the list of available operation modes."""
        return self._operation_list

    @property
    def current_fan_mode(self):
        """Return the fan setting."""
        return self._current_fan_mode

    @property
    def fan_list(self):
        """Return the list of available fan modes."""
        return self._fan_list

    def set_fan_mode(self, fan):
        """Set new target temperature."""
        self._current_fan_mode = fan
        self._sendpacket('fan')
        self.schedule_update_ha_state()

    def set_operation_mode(self, operation_mode):
        """Set new target temperature."""
        self._current_operation = operation_mode
        self._sendpacket('operation')
        if self._current_operation != STATE_OFF:
           self._sendpacket('temperature')
           self._sendpacket('fan')
        self.schedule_update_ha_state()
        self._last_operation = operation_mode

    def set_temperature(self, **kwargs):
        """Set new target temperatures."""
        if kwargs.get(ATTR_TEMPERATURE) is not None:
            self._target_temperature = kwargs.get(ATTR_TEMPERATURE)
        if self._target_temperature < self._min_temp:
            self._current_operation = 'off'
            self._target_temperature = self._min_temp
        elif self._target_temperature > self._max_temp:
            self._current_operation = 'off'
            self._target_temperature = self._max_temp

        self._sendpacket('temperature')
        self.schedule_update_ha_state()


    def _sendpacket(self, mode, retry=2):
        """Send packet to device."""
        temp = (
            "JgAWAXA3ECsODw4PDhANEA4PDg8OEA0QDRAODw4PECsODw4PDhANEA0QDg8OEA0QDg8ODw4QDRAODw4PDg8OEA4PDysOEA8rECoQKw8rECsPKw4QDysPKxArDysQKw8rECsPKxArDRAODw4PDhANEA0QDg8ODxArDysOEA0QDg8QKw8rDysOEA0QDysQKw8rDRAOEA0QDysOEA0QDysOEA0QDRAPKw4QDysPKw4QDysQKw8rDg8OEA0QDg8PKw4QDRAODw4PECsPKxArDRAPKxArDysQKw8rECsPKxArDysQKw0QDg8ODw4QDRAODw4PDg8QKw8rECsODxArDRAPKw4QDRAODw4PECsNEA8rDg8QKxAqECsOAA0FAAA=",
            "JgAWAXA3ECsODw4PDhANEA4PDg8OEA0QDRAODw0QECsODw4PDhANEA4PDg8OEA0QDg8ODw4PDhANEA4PDg8OEA0QDysODxArDysQKw8rECsPKw4PECsPKxArDysQKw8rDywPKw8rDhANEA4PDg8OEA0QDg8NEBArDysODw4QDRAPKxArDysOEA0QDiwPLA4sDRAOEAwRDysNEA0RDiwNEQwRDBEPKw0QECsPKw0RDiwPKxArDRANEA4QDBEOLA0RDRAMEQ4PDywPKxArDBEOLA8sDysPLA8rDywOLA4sECsPKw0RDBEMEQ0QDhAMEQ0QDg8PLA4sDi0OLA8rDhAOLA4PDhANEA0QDg8OEA4sDg8QKw8rDysOAA0FAAA=",
            "JgAWAXA4DysOEA0QDg8ODw4QDRAODw4PDhANEA4PDysOEA4PDg8OEA0QDRAODw8PDRANEA4PDg8PDw0QDg8PDg4QDysODxArDysQKw8rDysQKw4PECsPKw8sDysPLA8rDysQKw4sDhANEA4PDg8OEA0QDg8ODxArDysODw4QDRENLBArDysODw4QDysQKw8rDg8ODw4QDysOEA0QDysODw8PDRAOLA4QDysPLA0QDysPLA8rDg8OEA0QDg8PKw4QDg8ODw4PECsPKxArDRAPKxArDysQKw8rECsPKxArDysQKw0QDg8ODw4QDRANEA4PDg8QKw8rECsNEA4PDywOLA4PDhANEA0QDysQKw4PDg8QKw8rECsNAA0FAAA=",
            "JgAWAW84ECsNEA4PDg8OEA0QDRAODw4PDhANEA4PDywNEA0QDg8OEA0QDg8ODw4QDRAODw4QDBAPDw0QDg8ODw4QDysODxArDysQKw8rDywPKw4PECsPKxArDysQKw8rDywPKw8sDg8ODw4PDhANEA0RDBAODw8sDiwOEAwRDBEOLQ4sDiwNEQwRDiwPLA8rDREMEQ0QDysOEAwRDiwOEQsRDRAOLQwRDiwPLAwSDisPLA8rDhAMEQwRDRAPLAwRCxINEA0SDisPLA4sDRAQKw8rDywPKw8sDiwPKw8sDiwQKw0QDRANEQwRDRANEAwSDBIOKxArDysPLAwSDisQKwwSDBEMEQwRDBEOLA0RDBEPKxArDysNAA0FAAA=",
            "JgAWAXA4DysNEA0RDRAMEg0PDREMEgwQDRANEQ0RDSwNEA0RDRENEA0PDhELEQ0QDg8NEQ0QDRANEQ0QDBINDw4QDiwMEQ8rECsPKxArDysPLA0QDi0OLA8rDywOLA8sDysQKw8rDg8NEQwRDRANEA0RDBEMEQ8rDywODw0RDRAOLA8sDysNEQwRDiwPKxArDRANEQ0QDiwNEA0RDiwNEA0RDRAOLA0RDysPKw0RDiwPKxArDRANEA0RDRENLA0RDRANEA0QDywOLA4tDBEPKxArDysQKw8rDysQKw8rECsOLA4QDRANEQwRDRAMEgwRDQ8PLA8rECsMEg0sDywPKw0RDRAMEQ0QDywMEg0PDRAQKw8rECsNAA0FAAA=",
            "JgAWAW84ECsNEA0QDREMEQwRDRANEA0RDBIMEA0QECsMEQ0QDREMEQwRDRAODw0RDBINDw0RDRAMEQwRDRENEAwRDysODxArDysQKw8rECsPKw0RDiwPKxArDiwQKw8rDi0PKw8rDRENEA0RDBANEQ0RDRAMEBArDiwNEA0RDBEPKxArDysNEQwRDysPLA8rDRENEAwSDSwNEQ0QDysNEA0RDBIOKw4QDysPLA0QDysPLA8rDhANEA0RDQ8PKw0SDBEMEA0RDiwPKw8sDBEOLBArDiwPLA8rDywPKw4sDywOLA0RDRANEQ0PDRANEgwQDRAPLA8rDi0OLA8rECsOLA0RDBEMEQ0RDQ8NEQwSDBAPLA8rDysNAA0FAAA=",
            "JgAWAW85DywNEQ0PDRENEA0QDBINEA0PDREMEQ0RDiwNEA0QDhANEAwSDBANEA0RDRAMEg0PDRANEQ0RDBAOEAwRDiwODxArDiwPLA4sDywOLA0QDywPKw8sDysPLA8rDi0OLA8rDRENEAwRDg8NEQwSDBENDw8sDiwOEA0QDRAPKxArDiwNEA4RDSwPLA4sDg8NEQ0RDSwOEAwSDC0NEA0RDBEOLA4QDiwPKw4QDywNLQ4sDRANEQwSDBENLA4QDBENEA0QDywOLA8sDRAOLA8sDiwQKw4sDywPKw8sDysOLQ0RDBANEA0QDRENEQwQDRAPLA8rECwMEQ0QDRAMEg0sDRANEQwSDSwPLA4sDywMEQ8rDywMAA0FAAA=",
            "JgAWAW85DysNEQwRDBENEA0RDBEMEQ0QDRANEQ0QDiwNEQwRDRANEA0RDBEMEgwRDBEMEQwSDBANEA4RDBAMEQ0RECoMEg4rDywOLBArDysQKw0QDiwPLA4sDywPKxArDysPLA8rDRANEQwRDREMEA4QDBEMEg4rDywMEQ0QDg8PLA8rECsMEQ0QECsOLA8sDBENEA0QDywMEQ0QDywMEQ4PDg8PLAwRDysQKw0QDywOLA8rDhAMEQ0QDg8PLA0QDRANEA0SDisPLA8rDRAPLA4sDywOLA8rECsPKw8sDysQKwwRDRANEA0RDRAMEg0PDRAQKw8rDywOLA0RDBEMEQ8rDREMEgwQDRAPLA8rECsMEQ4sDywMAA0FAAA=",
            "JgAWAW84ECsNEA4PDg8OEA0QDRAODw4PDhANEA4PECsNEA4PDg8ODw4QDRAODw4PDhANEA4PDg8OEA0QDRAODw4PECsODxArDysQKhArDysQKw0QDysQKw8rECsPKxArDysQKhArDg8ODw4QDRAODw4PDhANEA8rECsNEA4PDg8QKw8rECsODw4PECsPKxArDRAODw4PECsODw4PECsNEA4PDg8QKw4PECoQKw4PECsPKxArDRAODw4PDg8QKw4PDg8OEA0QDiwQKw8rDg8QKw8rECsPKxArDysQKw8rECsPKw4PDg8OEA0QDg8ODw0RDRAPKxArDysODxArDg8ODxArDRAODw4QDysODxArDysODxArDysOAA0FAAA=",
            "JgAWAXA4DysODw0RDBENEA4PDRENEA0QDRANEQwRDiwODw0RDRANEA0QDhANEA0QDg8ODw4QDRANEA4PDhANEA0QDywNEA8rECsPKw8sDysPLA0QDysQKw8rECsPKw8rECsPKxArDRANEA0RDBEMEQ4PDg8OEA8rDysOEA0QDg8PLA8rECsMEQ0QDysQKw4sDhANEA0QDysOEAwRDysNEQ0QDRAPKw4QDiwPKw4QDiwQKw8rDg8NEQwRDBEPKw4QDBENEA0QECsOLBArDRAOLBArDysQKw8rDysQKw8rECsPKw4QDRANEA4PDRANEQ0QDRAPLA8rDysQKw8rDhAMEQ8rDg8NEQ0QDRANEBArDysODxArDysOAA0FAAA=",
            "JgAWAXA3ECsNEA4PDhANEA0QDg8ODw4QDRAODw4PECsNEA4PDg8OEA0QDg8ODw4QDRANEA4PDg8OEA0QDg8ODw4QDysNEBArDysPLA8rDysQKw0QDywPKw8rECsPKxArDysQKw8rDg8OEA0QDRANEA4PDhANEA8rDywNEA4PDg8QKw8rECsNEA4PECsPKxArDRAODw0QECsNEA4PECsNEA4PDg8QKw0QDysQKw0QDysQKw8rDhANEA0QDg8PLA0QDg8ODw4QDysPLA4sDg8QKw8rDywPKw8rECsPKxArDysQKw0QDRAODw4QDRANEA4PDhAOLA4sECsODw0QDywNEA8rDREMEQ0QDysQKw4PECsMEQ8rECsNAA0FAAA=",
            "JgAWAXA4DysODw4QDRAODw4PDhANEA0QDg8OEA0QDysOEA0QDg8ODw4QDRAODw4PDhANEA0QDg8OEA0QDg8ODw4QDysODxArDysQKw8rECsPKw4PECsPKxArDysQKw8rECsPKxArDg8ODw4QDRAODw4PDhANEA8rECsNEA4PDg8QKw8rECsODw4QDysPKxArDg8ODw4QDysODw4QDysODw4QDRAPKw4QDysQKg4QDysQKw8rDg8OEA0QDg8QKw0QDg8ODw4QDysQKw8rDg8QKw8rECsPKxArDysQKw8rECsPKw4PDhANEA0QDg8OEA0QDg8QKw8rECoQKw4PECsNEA8rDhANEA4PDg8QKw4PECsNEA8rECsNAA0FAAA=",
            "JgAWAXA4DysOEA0QDg8ODw4QDRAODw4PDhANEA4PECsNEA4PDg8OEA0QDg8ODw4QDRANEA4PDhANEA4PDg8OEA0QDysOEA8rDywPKw8rECsPKw4QDysQKw8rECsPKxArDysQKw8rDg8OEA0QDRAODw4QDRAODw8rECsODw4PDhAPKxArDysODw4QDysQKw8rDg8OEA0QDysOEA0QDysODw4QDRAPKw4QDysQKw0QDysQKw8rDhAPKw4PDhAPKw4PDhANEA0QDg8QKw8rDg8QKw8rECsPKxArDysQKw8rECsPKw4QDRANEA4PDhANEA0QDg8QKw8rECsNEA8rECsODxArDRAODw4PECsODw4PECsNEBArDysOAA0FAAA=",
            "JgAWAXA3ESoNEA0QDRENEAwSDQ8NEA0RDBINDw4QDiwNEA0RDRAMEQwRDRANEA0SDBANEQ0QDRAMEQ0RDRANEAwRDysODw8sDysQKw4sECsPKw0QDywOLBArDysQKw8rDywOLA8sDRANEA0RDg4NEQwRDRENEA4sDiwNEQwSDBENLBArDywMEQ0RDSwPLA8rDhAMEQ0QDiwNEA0RDiwNEQwRDBIMLQ0QECsPKw0RDiwPKxArDRAOLA4QDBEOLA0RDRANEA0RDRAOLA8rDhAPKw8sDysPKxArDiwQKw8rDywOLA0QDRENEQwQDREMEQ0RCxEOLBArDiwQKw8rDywNEA4sDhANEA0RDRANDw4QDiwNEQ4sDysOAA0FAAA="
        )

        oper_off = {
            STATE_COOL: "JgB2AXA4DysODw4PDhANEA4PDg8OEA0QDg8ODw4PECsODw4PDhANEA4PDg8OEA0QDg8ODw4PDhANEA4PDg8OEA0QDysPDw8rDysQKw4sECsPKw4PDywPKw8sDysQKw8rDywOLA8sDBENEA4PDg8OEA0QDRAODw4PDywPKw4QDRAPKxArDiwPLA0QDg8OLBArDg8ODw4QDiwODw4PECsODw4PDhAPKw4PECsPKw4PDywPKxArDRAPKw4QDRAODw4PDhANEA0QDg8PLA8rDysQKw8rDywPKxArDysQKw8rDywPKw4PDhANEA0QDg8OEA0QDRAPKxArDysODxArDg8ODw8sDRAODw4QDiwODw4tDysODw8sDysODw8sDysODw8sDRAODw4QDysODw0RDiwNEA8sDiwOLA4QDRAODw4PDhANEA0QDRAPLA4sDywOLA8sDysOLBArDg8ODw4QDRANEA4sDhAPKw8sDiwPKxArDysOEA8rDg8OAA0FAAA=",
            STATE_HEAT: "JgB2AXA4Di0JFAwSCxIMEQsSDBEMEgwRCxIMEQwRDi0MEgoSDBILEgsTCxILEQwTCRMLEg0QDRELEwoSDBILEgsSDSwNEQ4tDSwQLA0tDiwOLA4QDi0OKxArDywNLQ8sDS0OLQ4uChMKEgsSCRQNEQsTChMLEQ0QDiwPKw0RDBIOLA0uDS0NLA0SCxINLQ4tCxENEQsSDiwMEgwQDy0LEgwSDBAKMA0RDS0PLAwRDS4NLA4tChQNLQwRDBIMEQsSCRQNEAwSCxMKLw4sDysPLQ4rDy0PKw4sDiwPKxArDiwOLgsSCRQNEA0RCxMKEgwTCRMNLQkwDy0MEQ4sChQLEg0tDBEMEgkVDSsNEg0sDi0MEg0tDi0LEgsSDBIJMA4tDQ8NEgsTDSwOLQ0tDRAMEg0sDi0OLQkUDBIMEQsTCBQMEA0RDBINLQ4sDi0OLA8sDiwOLA8tCxEMEgsSDBEKEw8rDRMLLQ8rDi0PLA0tDi0LEg4sChQJAA0FAAA=",
            STATE_DRY: "JgB2AW84DysNEQwRDRANEA0QDg8OEAwRDRANEQ0PECsMEQ0QDRAODw4PDhAMEQwRDg8NEA0QDRENEQwQDRAODw4PDywNEA4sDywOLA8rDysPLA0QDiwPLA8rDysPLA8rDysPLA8sDRENEA0QDg8NEA4QDBENEA0RDisQKwwRDRAPKxArDiwOLA4PDREOLA8rDRAOEAwRDiwNEA4PECsNEAwRDhAOKw4QDysPKw4PDywPKw8rDhAPKw0QDRAODw4QDRELEQ0QDg8QKg8sDiwPKxArDysPKw8sDysPKw8sDysPKw4QDRANEA0QDRAODw4PDhAPKw8rECsNEA8rDg8OEA4sDg8ODw4PECoOEA8rDysODxArDysODw4PECsODw8rDg8OEA0QDiwOLA4QDysNEA8rECsPKw4PDg8ODw4QDRANEA4PDg8QKw8rDysPKxArDysPKxArDRAODw4PDg8OEA8rDg8PKxArDiwPKw8sDysODw8sDRANAA0FAAA=",
            STATE_FAN_ONLY: "JgB2AW84DywNEA4PDRENEA0QDg8NEA0RDRANEA0QDywNEA0QDhAMEQ0QDRAOEAwRDBEODw0QDRENEA0QDg8NEQwRDiwNEQ8rDiwPLA4sDywOLA4QDiwPKw8sDiwQKw4sDywPKw8tDBANEA4PDREMEQ0QDRANEQ0QDiwPLA0QDRAPLA4sDiwPLA4PDhAOLA4sDREMEQ0QDywNEA0QDiwNEQwRDRAPLAwRDiwPLA0QDiwPLA8rDhAOLA0QDRENEA0QDg8NEQwRDRAOLA8sDiwPLA4sDywOLA8sDiwPLA4sDiwPLA0QDg8OEAwRDBENEA0QDREOLA8sDiwODw8sDBENEA8sDBENEA0QDywNEA8sDiwODw8sDysOEA8rDRAODw0RDiwNEA0RDiwNEA8sDiwPLAwSDSwQKwwRDRANEQwRDRANEAwRDREOLA8sDiwPLA4sDysQKw4sDhAMEQ4PDg8NEQ4sDRAPLA4sDywOLA8sDiwNEQ4sDRANAA0FAAA="
        }

        oper = {
            STATE_COOL: "JgB2AW85DysODw4QDBEMEQ4QDRANEAwSCxINDw4QDi0MEQ0QDRAMEgwRDBAOEAwSDBAMEgwRDRANEA0RDBAODw4QDiwNEA8sDiwOLA8sDiwQKw0RDSwQKw8rDywOLA4tDysPKw8tCxIMEQwRDRANEA0RDBEMEA4QDysOLQ0QDRAOLBArDiwPLA0QDRENLA8sDREMEQ0QDiwNEA4QDiwMEgwRDQ8PLA0RDSwPLA0RDiwOLA8rDREOLA0RDBEMEQwSCxIMEA0RDBIOKw8sDiwOLA8sDiwPLA4sDywOLA4sDywOLA0RDBENEQwRDBEMEgsSDBENLA8sDiwNEQ8rDhANEA4tDBEMEQ0QDiwODw8sDiwNEA8sDiwOEA8rDiwODw8sDRANEA4QDysODw0QDywNEA4sDywOLQ0QDBILEg0QDBIMEQwRDBEOLA4sDi0OLA4sDywOLA8sDRELEgwQDhAOLA4PDhAPLA0sDywOLA4tDRENLA8sDBENAA0FAAA=",
            STATE_HEAT: "JgB2AXA3DywODw4PDhANEA4PDg8ODw4QDg8ODw4PECsODw4PDhANEA0QDg8ODw4QDRAODw4PDg8NEQ0QDg8ODw4QDysODxArDysQKhArDysQKw4PDysQKw8rECsPKw8rECsPKxAsDBAODw4PDhANEA0QDg8OEA0QDysQKw0QDg8QKhArECoQKw0QDg8QKw8rDg8ODw4QDysODw4QDysODw4PDhAPKw4PECsPKw4PECsPKxArDRAPKw4PDhANEA4PDg8NEQ0QDg8PKxArDysQKw8rECsPKw8rECsPKxArDysQKw0QDg8ODw4PDhANEA4PDg8QKw8rECsNEA8rDhANEA8rDg8OEA4PDysODxArDysOEA8rECoOEA0QDg8QKw8rDg8OEA0QDysPLA8rDg8ODxArDysQKw0QDg8ODw4QDRAODw4PDhAPKw8rECsPKxArDysQKhArDg8ODw4QDRAPKw4QDRAPKxArDysPKxArDg8QKhArDg8OAA0FAAA=",
            STATE_DRY: "JgB2AW84DywMEgsRDRENEA0RDBEMEQwRDRAMEgsRDi0NEA0RDBENEA0QDBILEgwQDhANEQsRDRAOEAwRDBILEgwRDiwMEQ4sDywOLA8sDiwOLA0RDiwOLA8sDysQKw4sDywOLA4tDRANEQwQDhANEA0RDBEMEQwRDiwOLA4QDRENLA4sDywOLQwRDRAOLA8sDREMEQwQDywNEQ0QDiwMEgsSDRAOLA0RDSwPLQwRDisPLA4sDhAOLA0QDRENEAwSDBANEQ0QDRAPLA4sDiwOLA8sDiwQKw4sDi0OLQ4sDi0NLQ0QDREMEQ0QDRANDw0RDREOKw8sDiwOEA8rDRENEA4sDREMEQ0PDywNEQ0tDywMEQ4sDysNEQ0QDi0MEA4sDhANEQsSDiwOLA0RDiwMEQ8rDywPKw4PDREMEgwRDBENEAwRDRENLA8sDiwOLQ4sDiwPLA8rDhANEQsSDBEOLAwSDBENLQ8rDiwPLA8rDhAOLQ0sDg8OAA0FAAA=",
            STATE_FAN_ONLY: "JgB2AW85DysNEQ0QDRAMEg0QDQ8NEQwSCxENEQ0PECsNEA4PDREMEQ0QDREMEA0SDBANEQ0PDRENEA0RDQ8NEA0RDiwNEQ4sDysQKw8rDysPLA0RDiwPKw8rECsOLBArDysPLA8sDBANEA0RDRANEQ0QDBENEAwRDywOLAwRDRAQKw4sDywOLA0QDRAQKw8rDRENEQsSDSwNEQ0QDysNEA0SDBENLA0RDiwOLA0RDysPLA8rDRAPLAwSDRANEA0QDRAMEg0PDREOLA4sDywPKw8sDiwOLQ4sDywOLA4sDywOLA0QDRENEQwRDRAMEQ0QDBINLQ4sDiwNEQ4sDRENEA4sDRENEAwRDiwODw8sDysOEA4tDSwNEQ4sDRENEA4sDREMEA0QDywNEA4tDysOEA8rDiwPLA0RCxENEQ0QDREMEQwQDREOLA4sDywOLA4tDiwOLQ8rDRAODw4QDRENLA0RDRAOLA8sDiwPKxArDREOLA4sDRENAA0FAAA="
        }

        fan = {
            STATE_COOL: {
                "Low": "JgAWAW85DiwOEA0RDBEMEQ0QDRANEAwSDRANEA0RDSwNEQ0RCxENEA4QDBENEQwRDBENEAwSDBENEAwRDREMEQwRDS0NEA8sDiwOLA8tDisPLAwSDSwQKw8rDywOLA8sDiwPLA4sDg8NEQwRDBIMEQ0PDhAMEQ4sDywNEA0QDREPKw8sDiwNEQwRDS0OLA8sDhALEQ4PDywMEgsSDS0NEA0RDBAPLQsRDysQKw0QDi0PKw8rDhAOLA4QDS0MEgwRDQ8PEAsSDBENLQ0RDisPLBAqDywOLA8sDiwOLA8sDysPLA0QDg8OEA0QDBEOEA0PDRINLA8sDBEOLA8sDRENLQ0QDBENEA8sDRANEA8sDBINLA8sDiwNAA0FAAA=",
                "Middle": "JgAWAXA3DywODw0QDhAMEQ8PDRAMEQwRDRANEQ0QDi0NDw4QDREMEQwRDBEMEgsSDRANEA0RDBEMEQwRDRANEQwQDi0NEA4sDywOLQ4sDiwPLA0QDiwQKw4sDywPKw8sDiwPLA4sDhANEA0RDBENDw4QDBEMEQ8rDywNEA0RDBEOLA4sECsNEA0QECsPKw8sDRENEA0QDiwNEA0QDy0MEA0QDhAPKw4PDywOLA0QDywOLBArDREOLAwSDS0NEA0QDBEOEA0QDBEOLQwQDywOLA8sDysOLBArDysPLA8rDywOLA0QDRIMEA4PDRENEA0RDBAPLA4sDhAOLQ0sDw8NEA8rDRENDw8sDRENEA4sDiwOEA4tDSwOAA0FAAA=",
                "High": "JgAWAW84DywMEgwRDBENEQwRDBENEAwRDRAOEA0PECsOEA0QDRANEQwRDQ8OEA0RDBANEQ0QDBEMEQ0QDhAMEQ0QDiwOEA4sDywOLA4sDywPKw4QDiwOLQ8rDiwPLA4tDiwPLA4sDRANEA4PDhAMEQ0QDhAMEQ4sDiwNEQ0QDREOLA4sDi0NEQsSDSwPLA4sDw8MEgwRDiwNEQsSDS0MEQ0RDBAPLA0RDSwPLA0RDSwPLA8rDhAPKw4PDywNEA0QDRENEQwRDBENLQ0RDC0PLA4sDywOLA4tDiwPKw8sDysPLA0QDRENEA0RCxINDw4QDBINLA8sDRENLQ4sDBENEQwRDiwNEA8sDRAOEA4sDysOLQ0QDiwOAA0FAAA="
            },
            STATE_HEAT: {
                "Low": "JgAWAW84ECsODw4PDhANEA4PDg8ODw4QDRANEA4PECsODw4PDhANEA0QDg8NEQ0QDRAODw4PDhANEA4PDg8OEA0QDysOEA8rDysQKw8rECsPKw4QDysPKxArDysQKw8rECsPKxArDRAODw4PDhANEA4PDg8OEA8rECoOEA0QDg8QKw8rECoOEA0QDysQKw8rDhANEA0QDywNEA0QDysOEA0QDg8QKw0QDysQKw0QDysQKw8rDhAPKw4PECsNEA4PDg8OEA0QDg8QKg4QDysQKw8rECsPKw8rECsPKxArDysQKw0QDg8OEA0QDRAODw4QDRAPKxAqDhAODw4PECsPKw4PDhANEA8rECsPKw4PDhAPKxArDysOAA0FAAA=",
                "Middle": "JgAWAXA4DysODw0RDRAODw4PDhANEA4PDg8OEAwRDysNEQwRDRANEA0QDhANEA4PDg8OEA0QDRAODw4QDRANEA0QECsMEQ8rECsPKxArDysQKwwRDysQKw4sECsPKxArDysQKw8rDg8NEQwRDRAODw0RDBENEA4sECsNEA0QDREPKw8rECsODw0QECsPKxArDRAODw0QECsODw4PECsNEA4PDg8QKw0QECsPKw0QECsPKxArDRAQKg4QDysNEA4QDRAODw0QDhAPKw4PECsPKxArDysQKw8rDysQKw8rECsPKw4PDhAMEQ4PDg8OEA0QDRAQKhArDg8ODw4QDysODxArDg8ODxArDysQKg4QDysODxArDysOAA0FAAA=",
                "High": "JgAWAXA3ECsNEA0QDRANEQ0QDg8ODw4QDBEODw0QDywNEA0QDREMEQwRDRANEA0RDBENEA0QDhAMEQ0QDRANEQ0QDysNEBArDiwQKw8rECsPKw4PECsPKxArDysQKw8rECsPKxArDRANEA0QDhAMEQ0QDRANEBArDysOEAwRDRAQKw8rDysOEAwRDysQKw8rDhAMEQ0QDysOEAwRDiwOEAwRDRAPKw4QDysQKw0QDysQKw8rDhAPKw4PECsNEA0QDRANEA0RDBEPKw0RDysPKxArDysPLA8rECsPKxArDysPKw4QDBENEA4PDRENEA4PDRAPLA8rDREMEQ0QDysOEA0QDysOEA8rDysQKw4PDysQKw0QECsNAA0FAAA="
            },
            STATE_FAN_ONLY: {
                "Low": "JgAWAXA4ECoNEQ0QDg8NEA4QDRAODw4PDREODw0QECoOEAwRDg8ODw0RDRAODw4PDREMEQ0QDg8NEA4QDBENEA0QECsNEBArDysPKxArDysQKw4PDysQKxAqECsPKw8sDysQKw8rDRANEQwRDRAODw4PDhANEA8rECsODw4PDREPKw8rECsNEA4PECsPKxArDRAODw4PECsNEA4PECsNEA0QDhAPKw0QDywPKw4PDywPKxArDBEPKw4QDysODw0RDRANEA4PDRAQKw0QECsPKw8rECsPKxArDysQKw8rECsOLA0QDREMEQ4PDRANEQwRDRAPKxArDg8PLAwRDRAQKg4QDRAODxArDBEPKxArDRAPKxArDysOAA0FAAA=",
                "Middle": "JgAWAXA4DysODw4QDRAODw4PDg8OEA0QDg8ODw4QDysODw4QDRAODw4PDhANEA4PDg8ODw4QDRAODw4PDhANEA4PECsNEA8rECsPKxArDysQKw0QDysQKw8rECsPKxArDysQKw8rDg8OEA0QDg8ODw4PDhAMEQ8rECsNEA4PDhAPKxAqECsODw0QECsPKxArDRAODw4QDysODw4PECsODw4PDhAPKw4PECsPKw4QDysPKxArDg8QKwwRDysOEA0QDg8ODw4PDhAPKw4PECsPKxArDysQKw8rECsPKxArDysPKw4QDRAODw4PDhANEA4PDg8QKw8rDRAQKw0QDg8OEA8rDg8OEA8rDRAQKw8rECsNEA8rECsOAA0FAAA=",
                "High": "JgAWAW85DiwOEAwRDBENEA4QDBEMEgsSDRAMEQwRDi0MEQ0RCxENEA4PDREMEgwRDBANEQwSDBEMEQwRDRANEQwRDiwNEQ0sDywPKw8sDysPLA0QDiwQKw8rDywOLA8sDiwPLA4sDhAMEA4QDBILEQ0QDRENEQ0sDywMEgwRDRAQKg8rDywNEA0RDS0OLA4sDRILEgsRDywNEA0QDysOEAwSDRAOLA0RDSwPLA0QDywPKw4sDhAOLA4QDiwNEQwRDRAMEgwQDREOLAwRDiwPLA4sDywPKw4sDywOLA8sDiwPLA0QDRENEAwSCxILEgwRDBEOLA4sDhAOLA0RDRAMEgsSDS0MEg0sDREOLA8sDiwQKg4QDiwNAA0FAAA="
            }
        }

        packet = None
        if mode=='fan':
           if self._current_operation==STATE_DRY or self._current_operation==STATE_OFF:
               return True
           packet = fan[self._current_operation][self._current_fan_mode]

        if mode=='operation':
           if self._current_operation==STATE_OFF:
              if self._last_operation==STATE_OFF:
                  return TRUE
              packet = oper_off[self._last_operation]
           else :
              packet = oper[self._current_operation]

        if mode=='temperature':
           packet = temp[int(self._target_temperature) - self._min_temp]

        if packet is None:
            _LOGGER.debug("Empty packet")
            return True
        try:
            self._device.send_data(b64decode(packet))
        except (socket.timeout, ValueError) as error:
            if retry < 1:
                _LOGGER.error(error)
                return False
            if not self._auth():
                return False
            return self._sendpacket(packet, retry-1)
        return True

    def _auth(self, retry=2):
        try:
            auth = self._device.auth()
        except socket.timeout:
            auth = False
        if not auth and retry > 0:
            return self._auth(retry-1)
        return auth