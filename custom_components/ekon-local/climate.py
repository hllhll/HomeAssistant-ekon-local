#!/usr/bin/python
# Do basic imports
import time
import importlib.util
import sys
import random
import string
import asyncio
import logging
import ssl
import binascii
import os.path
import voluptuous as vol
import threading
import pyekonlib
import pyekonlib.Server
import copy
import pyekonlib.Migration
from pyekonlib.Misc import AirconMode, AirconStateData

import homeassistant.helpers.config_validation as cv

from homeassistant.components.climate import (ClimateEntity, PLATFORM_SCHEMA)

from homeassistant.components.climate.const import (
    HVAC_MODE_OFF, HVAC_MODE_AUTO, HVAC_MODE_COOL, HVAC_MODE_DRY,
    HVAC_MODE_FAN_ONLY, HVAC_MODE_HEAT, SUPPORT_FAN_MODE,
    SUPPORT_TARGET_TEMPERATURE, FAN_AUTO, FAN_LOW, FAN_MEDIUM, FAN_HIGH)

from homeassistant.const import (
    ATTR_UNIT_OF_MEASUREMENT, ATTR_TEMPERATURE, 
    CONF_NAME, CONF_HOST, CONF_PORT, CONF_MAC, CONF_TIMEOUT, CONF_CUSTOMIZE, 
    STATE_ON, STATE_OFF, STATE_UNKNOWN, 
    TEMP_CELSIUS, PRECISION_WHOLE, PRECISION_TENTHS, )

from homeassistant.helpers.event import (async_track_state_change)
from homeassistant.core import callback
from homeassistant.helpers.restore_state import RestoreEntity
from configparser import ConfigParser

REQUIREMENTS = ['']

_LOGGER = logging.getLogger(__name__)

SUPPORT_FLAGS = SUPPORT_TARGET_TEMPERATURE | SUPPORT_FAN_MODE

DEFAULT_NAME = 'EKON Climate-Local'

# What I recall are the min and max for the HVAC
MIN_TEMP = 16
MAX_TEMP = 30

# fixed values in ekon mode lists
HVAC_MODES = [HVAC_MODE_AUTO, HVAC_MODE_COOL, HVAC_MODE_DRY, HVAC_MODE_FAN_ONLY, HVAC_MODE_HEAT, HVAC_MODE_OFF]

FAN_MODES = [FAN_AUTO, FAN_LOW, FAN_MEDIUM, FAN_HIGH]

CONF_UDP_SERVER_PORT = 'udp_server_port'
CONF_UDP_SERVER_ADDR = 'udp_server_addr'
CONF_DEVICE_ADDR = 'device_addr'
CONF_FORWARD_IP = 'forward_addr'
CONF_FORWARD_PORT = 'forward_port'

MAP_MODE_EKONLIB_TO_HASS = {
    AirconMode.Cool: HVAC_MODE_COOL,
    AirconMode.Auto: HVAC_MODE_AUTO,
    AirconMode.Dry: HVAC_MODE_DRY,
    AirconMode.Heat: HVAC_MODE_HEAT,
    AirconMode.Fan: HVAC_MODE_FAN_ONLY
}

MAP_MODE_HASS_TO_EKONLIB = {
    HVAC_MODE_COOL: AirconMode.Cool,
    HVAC_MODE_AUTO: AirconMode.Auto,
    HVAC_MODE_DRY: AirconMode.Dry,
    HVAC_MODE_HEAT: AirconMode.Heat,
    HVAC_MODE_FAN_ONLY: AirconMode.Fan
}

MAP_FAN_EKONLIB_TO_HASS = {
    1: FAN_LOW,
    2: FAN_MEDIUM,
    3: FAN_HIGH,
    0: FAN_AUTO
}

MAP_FAN_HASS_TO_EKONLIB = {
    FAN_LOW: 1,
    FAN_MEDIUM: 2,
    FAN_HIGH: 3,
    FAN_AUTO: 0
}
# Since we're creating a platform for integration `Climate` extend the schema
PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_UDP_SERVER_PORT): cv.positive_int,
    vol.Required(CONF_UDP_SERVER_ADDR): cv.string,

    vol.Optional(CONF_NAME, default=''): cv.string,
    vol.Optional(CONF_DEVICE_ADDR, default=''): cv.string,
    vol.Optional(CONF_FORWARD_IP, default=''): cv.string,
    vol.Optional(CONF_FORWARD_PORT, default=0): cv.positive_int,
})

EKON_VALUE_FAN_LOW = 1
EKON_VALUE_FAN_MEDIUM = 2
EKON_VALUE_FAN_HIGH = 3

@asyncio.coroutine
async def async_setup_platform(hass, config, async_add_devices, discovery_info=None):
    _LOGGER.info('Setting up Ekon-local climate platform')

    dev_addr = config.get(CONF_DEVICE_ADDR)
    udp_server_ip = config.get(CONF_UDP_SERVER_ADDR)
    udp_server_port = config.get(CONF_UDP_SERVER_PORT)
    if dev_addr is not None and dev_addr!='':
        # def SetDeviceUDPServer(deviceAddr, serverAddr, serverPort):
        _LOGGER.info ("Migrated Ekon device")
        migrate_lambda = lambda: pyekonlib.Migration.SetDeviceUDPServer(dev_addr, udp_server_ip, udp_server_port)
        result = await hass.async_add_executor_job(migrate_lambda)
        if result:
            _LOGGER.info ("Migrated Ekon device %s to server %s:%d" % (dev_addr,udp_server_ip,udp_server_port))
        else:
            _LOGGER.error ("Error migrating device %s" % dev_addr)
    _LOGGER.info('Creating Ekon-local climate controller')
    controller = HAEkonLocalClimateController(hass, config, async_add_devices)
    await controller.start()
    _LOGGER.info('Finished setting up Ekon-local climate platform')


class HAEkonLocalClimateController():
    """Ekon user account, inside this account there are all the ACs""" 
    def __init__(self, hass, config, async_add_devices):
        self.hass = hass
        self._async_add_devices = async_add_devices
        self._name = config.get(CONF_NAME)
        self._udp_port = config.get(CONF_UDP_SERVER_PORT)
        self._forward = None
        self._devices = []
        f_ip = config.get(CONF_FORWARD_IP)
        f_port = config.get(CONF_FORWARD_PORT)
        if f_ip is not None and f_ip != '':
            self._forward = ((f_ip, f_port))

        _LOGGER.info("HAEkonLocalClimateController - Creating UDP Server")
        self._server = pyekonlib.Server.UDPServer(self._udp_port, None, self.on_hvac_connected, self.on_hvac_timeout, self.on_hvac_data, asyncio.sleep, self.ha_async_create_task_wrapper, self._forward )

    async def ha_async_create_task_wrapper(self, corutine):
        # There's some issue with the caller not invoking currectly, try this sorcery
        self.hass.async_create_task(corutine)

    async def start(self):
        _LOGGER.info('Starting UDP Server on port %d' % self._udp_port)
        await self._server.start()
        _LOGGER.info('UDP Server started')

    @asyncio.coroutine
    async def on_hvac_connected(self, deviceSession, hvacState):
        _LOGGER.info("Ekon device connected")
        newdev = EkonLocalClimate(self, self.hass, hvacState, self._name, deviceSession)
        self._devices += [newdev]
        self._async_add_devices([newdev])
        _LOGGER.info("After - Ekon device connected, device added to hass")

    @asyncio.coroutine
    async def on_hvac_timeout(self, deviceSession):
        #TODO: MAKE HVAC UNAVIALALBE
        _LOGGER.info("HVAC Timed-out")
        pass

    @asyncio.coroutine
    async def on_hvac_data(self, deviceSession, newstate):
        _LOGGER.debug("Ekon HVAC data recived - Scheduling update")
        await self._devices[0].update_state(newstate)
        _LOGGER.debug("Ekon HVAC data recived - update scheduled")

    async def apply_new_state(self, session, newState):
        _LOGGER.debug("Sending new state from HA to pyekonlib")
        await self._server.sendNewState(newState)


class EkonLocalClimate(ClimateEntity):
    def __init__(self, controller, hass , state, name, deviceSession):
        self.hass = hass
        _LOGGER.info('Initialize the Ekon climate device')
        self._controller = controller
        self._name = name
        self._current_state = state
        self._session = deviceSession

    BLHA = 0
    async def update_state(self, newstate):
        _LOGGER.debug("EkonLocalClimate-update_state")
        self._current_state = newstate
        EkonLocalClimate.BLHA += 1
        if EkonLocalClimate.BLHA > 1:
            self.async_schedule_update_ha_state()

    @property
    def should_poll(self):
        return False

    async def async_update(self):
        # TODO: Sending heartbeat from UDP server to device, cuases it to resend values
        #   This might be what we'd like to do here
        _LOGGER.info('update()')
        pass

    @property
    def name(self):
        _LOGGER.info('name(): ' + str(self._name))
        # Return the name of the climate device.
        return self._name

    @property
    def temperature_unit(self):
        return TEMP_CELSIUS

    @property
    def current_temperature(self):
        _LOGGER.info('current_temperature(): ' + str(self._current_state.currentTemp/10))
        # Return the current temperature.
        return self._current_state.currentTemp/10

    @property
    def min_temp(self):
        return MIN_TEMP
        
    @property
    def max_temp(self):
        return MAX_TEMP
        
    @property
    def target_temperature(self):
        _LOGGER.info('target_temperature(): ' + str(self._current_state.targetTemp/10))
        # Return the temperature we try to reach.
        return self._current_state.targetTemp/10
        
    @property
    def target_temperature_step(self):
        return 1

    @property
    def hvac_mode(self):
        mode = MAP_MODE_EKONLIB_TO_HASS[self._current_state.mode]
        _LOGGER.info('hvac_mode(): ' + str(mode))
        return mode

    @property
    def hvac_modes(self):
        return HVAC_MODES

    @property
    def fan_mode(self):
        fan = MAP_FAN_EKONLIB_TO_HASS[self._current_state.fanSpeed]
        _LOGGER.info('fan_mode(): ' + str(self._current_state.fanSpeed))
        return fan

    @property
    def fan_modes(self):
        return FAN_MODES
        
    @property
    def supported_features(self):
        return SUPPORT_FLAGS        


    async def async_set_temperature(self, **kwargs):
        _LOGGER.info('set_temperature(): ' + str(kwargs.get(ATTR_TEMPERATURE)))
        if kwargs.get(ATTR_TEMPERATURE) is not None:
            tt = int(kwargs.get(ATTR_TEMPERATURE))
            newState = copy.deepcopy(self._current_state)
            newState.targetTemp = tt*10
            await self._controller.apply_new_state(self._session, newState)

    # Ho why, why! HA hardned the param to be fan mode ... ??? 
    # Wierd choices, they make
    # Like: https://github.com/syssi/xiaomi_airconditioningcompanion/issues/41
    async def async_set_fan_mode(self, fan_mode):
        _LOGGER.info('set_fan_mode(): ' + str(fan_mode))
        newState = copy.deepcopy(self._current_state)
        newState.fanSpeed = MAP_FAN_HASS_TO_EKONLIB[fan_mode]
        await self._controller.apply_new_state(self._session, newState)


    async def async_set_hvac_mode(self, hvac_mode):
        _LOGGER.info('set_hvac_mode(): ' + str(hvac_mode))
        newState = copy.deepcopy(self._current_state)
        
        if hvac_mode == HVAC_MODE_OFF:
            newState.onoff = False
        else:
            newState.onoff = True  # Make sure it's on
            newState.mode = MAP_MODE_HASS_TO_EKONLIB[hvac_mode]
        await self._controller.apply_new_state(self._session, newState)

    @asyncio.coroutine
    def async_added_to_hass(self):
        _LOGGER.info('Ekon-local climate device added to hass()')

