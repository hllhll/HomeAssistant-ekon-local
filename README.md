# ~~NOTE: AUTO-CONFIGURATION Currently seem to not work, make the [manual method](https://github.com/hllhll/pyekonlib/blob/master/README.md#configuring-the-device)~~ Autoconfig now works!
# This is the "local-server" varient of the [Cloud-based Ekon HA integration](https://github.com/hllhll/HomeAssistant-EKON-iAircon)
*Library will auto-onstall, but [PLEASE READ THE LIBRARY README](https://github.com/hllhll/pyekonlib).*
 
# This is not fully tested, no responsibility whatsoever - READ Fully before installing
Using this component may effect your hass installation stability, may report falsly the state of your HVAC, commands may seem to be working but they might not (such situation where u think you turned off the ac, but it didn't)

IT WORKS ON THE BASIS OF Blocking the original app, and currently intedended for intermidary-level users as it's in beta

# What types of HVACs? / ACs?
Short: Tadiran mini-central ACs, other iAircon/EKON/Airconet Based ACs
Long:
There's a compony out there Called EKON, They have a product named iAircon which is a esp8266 hardware device, or the marketing name 
"Airconet+" (available on play store, formerly "Airconet") that connects to the HVAC system

Israeli HVACs manefacturer "TADIRAN" Uses this ekon solution for their "Tadiran connect" product, designated for some of their "mini central hvacs"

<img src="https://g-rafa.co.il/wp-content/uploads/2017/06/tadiran1-e1498462193178-1024x609.jpg" width="512px" height="305px" />
<img src="https://lh3.googleusercontent.com/43-jwjJFMF1Q1ft6P7e6Su8wxygdrlRe1B5cY3o2dZAgACU-kYZ9Uql4cFVAuiGgdg=w1396-h686-rw" width="193px" height="343px" />

# HomeAssistant-ekon-local
EKON iAircon / Tadiran climate component written in Python3 for Home Assistant.
Built as a result of research into the Ekon apps and device communication with the server

## Features
- Currently, only supports ONE CONCURRENT DEVICE 
- Local control of your HVAC, even without internet
- No flashing/burning of the device needed
- The device will communicate with a server that would setup on you're HA installation
- (Expirimental) Auto-configuration of the device to communicate with the HA server
- Optioanl - keep using your cloud-based app! :D - See configuration

## How it works
ekon-local integration would run a UDP server on your HA host. You would have to configure
the device to communicate with this server, instead of the genuine one (Either Airconet/Tadiran connect or Airconet+). Works on the basis of [pyekonlib](https://github.com/hllhll/pyekonlib)


## Custom Component Installation

Note that configuration or auto-configuration need to only work once, so if the device is using DHCP and switches IP address it shouldn't be a problem, it saves the server in it's configuration.

1. Copy the custom_components folder to your own hassio /config folder.
2. configuration.yaml - 
   ```yaml
   climate: 
   - platform: ekon-local
     name: MyHVAC                   # Please, no spaces
     udp_server_port: 6343          # Can be anything, make sure it's accessible
     udp_server_addr: 192.168.1.10  # Optional, HA ip address
     device_addr: 192.168.1.25      # Optional, for auto-configuring the device
   ```
   - Configure `name` as your liking
   - `udp_server_port` is a UDP port you need to make sure that the device can talk with HA, bidirectional. - Can be any valid port number
   - `udp_server_addr` - Optional, for auto-configure method, the ip address of your HA host.
   - `device_addr` - Optional, for auto-configure method, the ip address of the EKON Device.
3. If you would like to keep working with the app, you would need the server details of the server for the proper application you are using, [see here for](https://github.com/hllhll/HomeAssistant-EKON-iAircon/issues/19#issuecomment-713386325) a list of ip addresses and ports for the original apps-udp server. \
  Add this to the `climate ekon-local` configuration entry (Same ident, just below)
    ```yaml
    forward_addr: 185.28.152.215
    forward_port: 6343
    ```
3. OPTIONAL: Add info logging to this component (to see if/how it works)
  
   ```yaml
   logger:
     default: error
     logs:
       custom_components.ekon-local: debug
   ```
## Known issues:
- Currently, only supports ONE CONCURRENT DEVICE 
- See all [known issues in the pyekonlib](https://github.com/hllhll/pyekonlib/blob/master/README.md)


## Troubleshooting (old, maybe usefull)
- No AC Shows up on the Frontend
  - Disconnect your internet, and restart HA or Reset the device (power cycle)
    - The device's server *might* not switch if the device is currently connected to the real backend
  - Activate debugging, did you see the device connecting to the server in the logs? \
    if not, try to [manually configure it to use your server](https://github.com/hllhll/pyekonlib/blob/master/README.md#configuring-the-device)
- Again, try the [library page](https://github.com/hllhll/pyekonlib/blob/master/README.md) if further issues
