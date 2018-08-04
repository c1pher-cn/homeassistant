"""
Support for Phicomm Air Detector M1 plant sensor.
Developer by lixinb & NETYJ
version 2.0.2
"""
import logging
import datetime
import requests,json
import voluptuous as vol
import hashlib

from homeassistant.components.sensor import PLATFORM_SCHEMA
from homeassistant.const import (CONF_NAME,)
from homeassistant.helpers.entity import Entity
import homeassistant.helpers.config_validation as cv


_LOGGER = logging.getLogger(__name__)
_INTERVAL = 3

SCAN_INTERVAL = datetime.timedelta(seconds=_INTERVAL)
DEFAULT_NAME = 'Phicomm M1'

ATTR_TEMPERATURE = 'temperature'
ATTR_HUMIDITY = 'humidity'
ATTR_PM25 = 'pm25'
ATTR_HCHO = 'hcho'
ATTR_BRIGHTNESS = 'brightness'

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
})


def setup_platform(hass, config, add_devices, discovery_info=None):
    """Set up the Phicomm M1 sensor."""

    name = config.get(CONF_NAME)
    phicommAccount = config['phicommAccount']
    phicommPassowrd = config['phicommPassowrd']
    devices = []
    if config['airDetectorMac']:
        devices.append(config['airDetectorMac'].upper())

    devs = []

    devs.append(PhicommM1Sensor(
        hass, name, phicommAccount, phicommPassowrd, devices))

    add_devices(devs)


class PhicommM1Sensor(Entity):
    """Implementing the Phicomm M1 sensor."""
    def __init__(self, hass, name,phicommAccount,phicommPassowrd, devices):
        """Initialize the sensor."""
        #_LOGGER.warning("name:%s, account:%s, pass:%s, devices lenght:%d, devices:%s",name, phicommAccount,phicommPassowrd,len(devices),devices)
        
        self._hass = hass
        self._name = name
        self._phicommAccount = phicommAccount
        self._phicommPassowrd = phicommPassowrd
        self._devices = devices
        self._state = None
        self.data = []
        self.fIsLogon = False
        self.retryCountDown = 0
        self.slowDownStep = 0
        self.access_token = None
        self.lastResponeMsg = ''
        self._state_attrs = {
            ATTR_PM25: None,
            ATTR_TEMPERATURE: None,
            ATTR_HUMIDITY: None,
            ATTR_HCHO: None,
            ATTR_BRIGHTNESS: 50,
        }
        #self.update()
        
    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state_attrs[ATTR_PM25]
    
    @property
    def state_attributes(self):
        """Return the state of the sensor."""
        return self._state_attrs
   
    def update(self):
        """
        Update current conditions.
        """
        if self._hass.states.is_state('input_boolean.phicomm_m1_reset','on'):
            states_attrs = {
               'friendly_name':'重试',
               'icon':'mdi:lock-reset'
            }
            self._hass.states.set('input_boolean.phicomm_m1_reset', 'off',states_attrs)
            self.slowDownStep = 0
            self.retryCountDown = 0
            _LOGGER.warning('reset login prcess!')
            
        if self.fIsLogon:
            
            headers = {'User-Agent': 'zhilian/5.7.0 (iPhone; iOS 10.0.2; Scale/3.00)',
                       'Authorization': self.access_token }
            brightness_state = 50
            brightness = self._hass.states.get('input_number.phicomm_m1_led')
            if brightness is not None:
                brightness_state = round(float(brightness.state))
            if self._state_attrs[ATTR_BRIGHTNESS] != brightness_state:
                if brightness_state == 50:
                    _brightness_state = 100
                else:
                    _brightness_state = brightness_state
                post_data = {'brightness':_brightness_state,'deviceId': '1-' + self._devices[0]}
                control_resp = requests.post('https://aircat.phicomm.com/connectserverv1/lightControl', data=post_data, headers=headers,timeout=3)
                if control_resp.status_code == 200:
                    control_obj = control_resp.json() 
                    if int(control_obj['error']) != 0: 
                        _LOGGER.error('Phicomm lightControl fail!: %s', control_obj)
    
            payload = {'deviceId': '1-' + self._devices[0]}
            resp = requests.get('https://aircat.phicomm.com/connectserverv1/lightControl', params=payload, headers=headers,timeout=3)
            if resp.status_code == 200:
                obj = resp.json() 
                error = obj['error']
                if int(error) == 0:
                    brightness_state = obj['brightness']
                    if brightness_state is 100:
                      brightness_state = 50
                    #_LOGGER.warning('input_number.phicomm_m1_led.state, %s',self._hass.states.get('input_number.phicomm_m1_led'))
                    states_attrs = {
                       'min':'0.0',
                       'max':'50.0',
                       'step':'25.0',
                       'mode':'slider',
                       'friendly_name':'屏幕亮度',
                       'icon':'mdi:led-on'
                    }
                    self._hass.states.set('input_number.phicomm_m1_led',float(brightness_state),states_attrs)
                    #_LOGGER.warning('input_number.phicomm_m1_led.state, %s',self._hass.states.get('input_number.phicomm_m1_led'))
                else:
                    _LOGGER.warning('get lightness error, %s',obj)

            resp = requests.get('https://aircleaner.phicomm.com/aircleaner/getIndexData', headers=headers,timeout=3)
            if resp.status_code == 200:
                obj = resp.json() 
                error = obj['error']
                if int(error) == 0:
                    jsonData = obj['data']['devs'][0]['catDev']
                    self._state_attrs = {
                        ATTR_PM25: jsonData['pm25'],
                        ATTR_TEMPERATURE: jsonData['temperature'],
                        ATTR_HUMIDITY: jsonData['humidity'],
                        ATTR_HCHO: jsonData['hcho'],
                        ATTR_BRIGHTNESS: brightness_state,
                    }
                else:
                    self.fIsLogon = False
                    _LOGGER.error(obj['msg']+"  Sleep 300 seconds before retry!")
                    
        elif self.retryCountDown <= 0:
            if self.slowDownStep < 15:
                self.slowDownStep += 1
                if self.slowDownStep % 3 == 0:
                    md5 = hashlib.md5()
                    md5.update(str(self._phicommPassowrd).encode("utf8"))
                    payload = {'authorizationcode' : 'feixun.SH_1', 
                               'password' : md5.hexdigest().upper(),
                                'phonenumber' : self._phicommAccount}
                    #_LOGGER.warning("payload:%s",payload)
                    headers = {'User-Agent': 'zhilian/5.7.0 (iPhone; iOS 10.0.2; Scale/3.00)'}
                    resp = requests.post('https://accountsym.phicomm.com/v1/login', headers=headers,params=payload,timeout=3)
                    if resp.status_code == 200:
                        obj = resp.json() 
                        error = obj['error']
                        if int(error) == 0:
                            self.access_token = obj['access_token']
                            #_LOGGER.warning("access_token:%s",self.access_token)
                            self.fIsLogon = True
                            self.retryCountDown = 300
                            self.slowDownStep = 0
                            self.lastResponeMsg = '' 
                        elif int(error) == 8:
                            _LOGGER.error('account login error: ' + obj['error'] + obj['message'])
                            self.lastResponeMsg = obj['message']
                            self.slowDownStep += 100
                            states_attrs = {
                               'friendly_name':'重试. Last error: '+ self.lastResponeMsg,
                               'icon':'mdi:lock-reset'
                            }
                            self._hass.states.set('input_boolean.phicomm_m1_reset', 'off',states_attrs)
                        else:
                            self.lastResponeMsg = obj['message']
                            _LOGGER.error('account login error: ' + obj['error'] + obj['message'])
            else:
                states_attrs = {'friendly_name':'重试. Last error: '+ self.lastResponeMsg,
                                'icon':'mdi:lock-reset'
                }
                self._hass.states.set('input_boolean.phicomm_m1_reset', 'off',states_attrs)
        else:
            self.retryCountDown -= _INTERVAL
            #_LOGGER.error("retryCountDown:%d", self.retryCountDown)
            states_attrs = {
               'friendly_name':'重试. Last error: Logged on at other place!',
               'icon':'mdi:lock-reset'
            }
            self._hass.states.set('input_boolean.phicomm_m1_reset', 'off',states_attrs)


