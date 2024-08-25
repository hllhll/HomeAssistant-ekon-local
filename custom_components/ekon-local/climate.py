#!/usr/bin/python
# Do basic imports
import asyncio
import copy
import logging
import socket

import homeassistant.helpers.config_validation as cv
import pyekonlib
import pyekonlib.Migration
import pyekonlib.Server
import voluptuous as vol
from homeassistant.components.climate import PLATFORM_SCHEMA, ClimateEntity
from homeassistant.components.climate.const import (
    FAN_AUTO,
    FAN_HIGH,
    FAN_LOW,
    FAN_MEDIUM,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, CONF_NAME, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from pyekonlib.Misc import AirconMode

REQUIREMENTS = [""]

_LOGGER = logging.getLogger(__name__)

SUPPORT_FLAGS = ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.FAN_MODE | ClimateEntityFeature.TURN_OFF | ClimateEntityFeature.TURN_ON

DEFAULT_NAME = "EKON Climate-Local"

# What I recall are the min and max for the HVAC
MIN_TEMP = 16
MAX_TEMP = 30

# fixed values in ekon mode lists
HVAC_MODES = [
    HVACMode.AUTO,
    HVACMode.COOL,
    HVACMode.DRY,
    HVACMode.FAN_ONLY,
    HVACMode.HEAT,
    HVACMode.OFF,
]

FAN_MODES = [FAN_AUTO, FAN_LOW, FAN_MEDIUM, FAN_HIGH]

CONF_UDP_SERVER_PORT = "udp_server_port"
CONF_UDP_SERVER_ADDR = "udp_server_addr"
CONF_DEVICE_ADDR = "device_addr"
CONF_FORWARD_IP = "forward_addr"
CONF_FORWARD_PORT = "forward_port"

MAP_MODE_EKONLIB_TO_HASS = {
    AirconMode.Cool: HVACMode.COOL,
    AirconMode.Auto: HVACMode.AUTO,
    AirconMode.Dry: HVACMode.DRY,
    AirconMode.Heat: HVACMode.HEAT,
    AirconMode.Fan: HVACMode.FAN_ONLY,
}

MAP_MODE_HASS_TO_EKONLIB = {
    HVACMode.COOL: AirconMode.Cool,
    HVACMode.AUTO: AirconMode.Auto,
    HVACMode.DRY: AirconMode.Dry,
    HVACMode.HEAT: AirconMode.Heat,
    HVACMode.FAN_ONLY: AirconMode.Fan,
}

MAP_FAN_EKONLIB_TO_HASS = {1: FAN_LOW, 2: FAN_MEDIUM, 3: FAN_HIGH, 0: FAN_AUTO}

MAP_FAN_HASS_TO_EKONLIB = {FAN_LOW: 1, FAN_MEDIUM: 2, FAN_HIGH: 3, FAN_AUTO: 0}
# Since we're creating a platform for integration `Climate` extend the schema
PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_UDP_SERVER_PORT): cv.positive_int,
        vol.Required(CONF_UDP_SERVER_ADDR): cv.string,
        vol.Optional(CONF_NAME, default=""): cv.string,
        vol.Optional(CONF_DEVICE_ADDR, default=""): cv.string,
        vol.Optional(CONF_FORWARD_IP, default=""): cv.string,
        vol.Optional(CONF_FORWARD_PORT, default=0): cv.positive_int,
    }
)

EKON_VALUE_FAN_LOW = 1
EKON_VALUE_FAN_MEDIUM = 2
EKON_VALUE_FAN_HIGH = 3


class EkonMigrationContext:
    def __init__(self, dev_addr, udp_server_ip, udp_server_port):
        self._dev_addr = dev_addr
        self._udp_server_ip = udp_server_ip
        self._udp_server_port = udp_server_port

    def migrate(self):
        try:
            return pyekonlib.Migration.SetDeviceUDPServer(
                self._dev_addr, self._udp_server_ip, self._udp_server_port
            )
        except socket.error as e:
            _LOGGER.error("Error in migration " + str(e))
            return False


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    _LOGGER.info("Setting up Ekon-local climate platform")

    _LOGGER.info("Creating Ekon-local climate controller")
    controller = HAEkonLocalClimateController(hass, config, async_add_entities)
    _LOGGER.debug("Starting Ekon-local server")
    await controller.start()

    dev_addr = config.get(CONF_DEVICE_ADDR)
    udp_server_ip = config.get(CONF_UDP_SERVER_ADDR)
    udp_server_port = config.get(CONF_UDP_SERVER_PORT)

    if dev_addr is not None and dev_addr != "":
        _LOGGER.info("Migrating Ekon device")
        context = EkonMigrationContext(dev_addr, udp_server_ip, udp_server_port)
        result = await hass.async_add_executor_job(context.migrate)
        if result:
            _LOGGER.info(
                "Migrated Ekon device %s to server %s:%d"
                % (dev_addr, udp_server_ip, udp_server_port)
            )
        else:
            _LOGGER.error("Error migrating device %s" % dev_addr)

    _LOGGER.info("Finished setting up Ekon-local climate platform")


class HAEkonLocalClimateController:
    """Ekon user account, inside this account there are all the ACs"""

    def __init__(self, hass, config, async_add_entities: AddEntitiesCallback):
        self.hass = hass
        self._async_add_entities = async_add_entities
        self._name = config.get(CONF_NAME)
        self._udp_port = config.get(CONF_UDP_SERVER_PORT)
        self._forward = None
        self._devices = []
        f_ip = config.get(CONF_FORWARD_IP)
        f_port = config.get(CONF_FORWARD_PORT)
        if f_ip is not None and f_ip != "":
            self._forward = (f_ip, f_port)

        _LOGGER.info("HAEkonLocalClimateController - Creating UDP Server")
        self._server = pyekonlib.Server.UDPServerLoopIndependent(
            self._udp_port,
            None,
            self.on_hvac_connected,
            self.on_hvac_timeout,
            self.on_hvac_data,
            # self.my_call_later,
            # self.my_create_async_task, # From thread context
            # self.hass.async_create_task, # From event loop/async context
            self._forward,
        )

    # Create async task from a thread context
    def my_create_async_task(self, corutine):
        return asyncio.run_coroutine_threadsafe(corutine, self.hass.loop)

    def my_call_later(self, time, fn):
        self.hass.helpers.event.async_call_later(time, fn)

    async def add_device(self, newdev):
        self._async_add_entities([newdev])

    async def start(self):
        _LOGGER.info("Starting UDP Server on port %d" % self._udp_port)
        await self._server.start()
        _LOGGER.info("UDP Server started")

    async def on_hvac_connected(self, deviceSession, hvacState):
        if len(self._devices) == 0:
            _LOGGER.info("Ekon device connected")
            newdev = EkonLocalClimate(
                self, self.hass, hvacState, self._name, deviceSession
            )
            self._devices += [newdev]

            asyncio.run_coroutine_threadsafe(self.add_device(newdev), self.hass.loop)

            _LOGGER.info("After - Ekon device connected, device added to hass")
        else:
            _LOGGER.info("Ekon device re-connected")
            self._devices[0].timed_out = False
            self._devices[0].async_schedule_update_ha_state()

    async def on_hvac_timeout(self, deviceSession):
        if len(self._devices) == 0:
            _LOGGER.error(
                "HVAC Timed out while not in list? Maybe an issue in the underlying library"
            )
            return
        _LOGGER.info("HVAC Timed-out")
        self._devices[0].timed_out = True
        asyncio.run_coroutine_threadsafe(self._devices[0].async_schedule_update_ha_state(), self.hass.loop)


    async def on_hvac_data(self, deviceSession, newstate):
        _LOGGER.debug("Ekon HVAC data received - Scheduling update")
        asyncio.run_coroutine_threadsafe(self._devices[0].update_state(newstate), self.hass.loop)
        _LOGGER.debug("Ekon HVAC data received - update scheduled")

    async def apply_new_state(self, session, newState):
        _LOGGER.debug("Sending new state from HA to pyekonlib")
        await self._server.sendNewState(newState)

    async def turn_off(self, session):
        _LOGGER.debug("Turning off HVAC")
        await self._server.turnOff()

    async def turn_on(self, session):
        _LOGGER.debug("Turning on HVAC")
        await self._server.turnOn()


class EkonLocalClimate(ClimateEntity):
    def __init__(self, controller, hass, state, name, deviceSession):
        self.hass = hass
        _LOGGER.info("Initialize the Ekon climate device")
        self._controller = controller
        self._name = name
        self._current_state = state
        self._session = deviceSession
        self.timed_out = False
        self._added_to_hass = False

    BLHA = 0

    async def update_state(self, newstate):
        if self.timed_out:
            _LOGGER.error(
                "EkonLocalClimate.update_state EkonLocalClimate-update_state while device is timed-out!"
            )
            return
        _LOGGER.debug("EkonLocalClimate.update_state, ", newstate)
        self._current_state = newstate

        if self._added_to_hass:
            self.async_schedule_update_ha_state()

    @property
    def unique_id(self):
        return str(self._session.device.deviceData)

    @property
    def available(self):
        return not self.timed_out

    @property
    def should_poll(self):
        return False

    async def async_update(self):
        # TODO: Sending heartbeat from UDP server to device, cuases it to resend values
        #   This might be what we'd like to do here
        _LOGGER.info("update()")
        pass

    @property
    def name(self):
        _LOGGER.info("name(): " + str(self._name))
        # Return the name of the climate device.
        return self._name

    @property
    def temperature_unit(self):
        return UnitOfTemperature.CELSIUS

    @property
    def current_temperature(self):
        _LOGGER.info(
            "current_temperature(): " + str(self._current_state.currentTemp / 10)
        )
        # Return the current temperature.
        return self._current_state.currentTemp / 10

    @property
    def min_temp(self):
        return MIN_TEMP

    @property
    def max_temp(self):
        return MAX_TEMP

    @property
    def target_temperature(self):
        _LOGGER.info(
            "target_temperature(): " + str(self._current_state.targetTemp / 10)
        )
        # Return the temperature we try to reach.
        return self._current_state.targetTemp / 10

    @property
    def target_temperature_step(self):
        return 1

    @property
    def hvac_mode(self):
        if self._current_state.onoff == False:
            mode = HVACMode.OFF
        else:
            mode = MAP_MODE_EKONLIB_TO_HASS[self._current_state.mode]
        _LOGGER.info("hvac_mode(): " + str(mode))
        return mode

    @property
    def hvac_modes(self):
        return HVAC_MODES

    @property
    def fan_mode(self):
        fan = MAP_FAN_EKONLIB_TO_HASS[self._current_state.fanSpeed]
        _LOGGER.info("fan_mode(): " + str(self._current_state.fanSpeed))
        return fan

    @property
    def fan_modes(self):
        return FAN_MODES

    @property
    def supported_features(self):
        return SUPPORT_FLAGS

    async def async_set_temperature(self, **kwargs):
        _LOGGER.info("set_temperature(): " + str(kwargs.get(ATTR_TEMPERATURE)))
        if kwargs.get(ATTR_TEMPERATURE) is not None:
            tt = int(kwargs.get(ATTR_TEMPERATURE))
            newState = copy.deepcopy(self._current_state)
            newState.targetTemp = tt * 10
            await self._controller.apply_new_state(self._session, newState)

    # Ho why, why! HA hardned the param to be fan mode ... ???
    # Wierd choices, they make
    # Like: https://github.com/syssi/xiaomi_airconditioningcompanion/issues/41
    async def async_set_fan_mode(self, fan_mode):
        _LOGGER.info("set_fan_mode(): " + str(fan_mode))
        newState = copy.deepcopy(self._current_state)
        newState.fanSpeed = MAP_FAN_HASS_TO_EKONLIB[fan_mode]
        await self._controller.apply_new_state(self._session, newState)

    async def async_set_hvac_mode(self, hvac_mode):
        _LOGGER.info("set_hvac_mode(): " + str(hvac_mode))
        newState = copy.deepcopy(self._current_state)

        if hvac_mode == HVACMode.OFF:
            newState.onoff = False
            await self._controller.turn_off(self._session)
        else:
            newState.onoff = True  # Make sure it's on
            newState.mode = MAP_MODE_HASS_TO_EKONLIB[hvac_mode]
            await self._controller.apply_new_state(self._session, newState)

    async def async_turn_on(self):
        await self._controller.turn_on(self._session)

    async def async_turn_off(self):
        await self._controller.turn_off(self._session)

    async def async_added_to_hass(self):
        _LOGGER.info("Ekon-local climate device added to hass()")
        self._added_to_hass = True
