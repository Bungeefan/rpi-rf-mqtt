#!/usr/bin/env python

import json
import re
import socket
import sys
import time
import uuid

import paho.mqtt.client as paho

import config

RF_CODE_ON = 13684993
RF_CODE_OFF = 13684994

RF_CODE_BRIGHTNESS_PLUS = 13684996
RF_CODE_BRIGHTNESS_MINUS = 13684997

RF_CODE_60S_OFF = 13684998

RF_CODE_BRIGHTNESS_10 = 13684999
RF_CODE_BRIGHTNESS_20 = 13685000
RF_CODE_BRIGHTNESS_40 = 13685001
RF_CODE_BRIGHTNESS_60 = 13685002
RF_CODE_BRIGHTNESS_80 = 13685003
RF_CODE_BRIGHTNESS_100 = 13685004

RF_CODE_JUMP = 13685005
RF_CODE_FADE = 13685006
RF_CODE_STROBE = 13685007

try:
    from rpi_rf import RFDevice
except Exception as importExc:
    print("Can't import RFDevice, actions won't work:", importExc)


def get_mac_address():
    mac_num = uuid.getnode()
    mac = '-'.join((('%012X' % mac_num)[i:i + 2] for i in range(0, 12, 2)))
    return mac


def build_device_info():
    return {
        "identifiers": [hostname],
        "name": hostname,
        "connections": [["mac", get_mac_address()]]
    }


def on_message(client: paho.Client, userdata, msg: paho.MQTTMessage):
    payload = msg.payload.decode()
    print("Received message:", msg.topic, payload)

    if msg.topic == light_switch["command_topic"]:
        if payload == "ON":
            send_action(RF_CODE_ON)
            client.publish(light_switch['state_topic'], msg.payload)
        elif payload == "OFF":
            send_action(RF_CODE_OFF)
            client.publish(light_switch['state_topic'], msg.payload)

    if msg.topic == light_switch["brightness_command_topic"]:
        brightness = int(payload)
        set_brightness(client, brightness)

    if msg.topic == light_switch["effect_command_topic"]:
        if payload == "Jump":
            send_action(RF_CODE_JUMP)
            client.publish(light_switch["effect_state_topic"], payload)
        elif payload == "Fade":
            send_action(RF_CODE_FADE)
            client.publish(light_switch["effect_state_topic"], payload)
        elif payload == "Strobe":
            send_action(RF_CODE_STROBE)
            client.publish(light_switch["effect_state_topic"], payload)
        elif payload == "Off":
            set_brightness(client, 1)
            client.publish(light_switch["effect_state_topic"], "OFF")

    if msg.topic == delay_off_button["command_topic"]:
        if payload == "PRESS":
            send_action(RF_CODE_60S_OFF)

    if msg.topic == brightness_plus_button["command_topic"]:
        if payload == "PRESS":
            send_action(RF_CODE_BRIGHTNESS_PLUS)

    if msg.topic == brightness_minus_button["command_topic"]:
        if payload == "PRESS":
            send_action(RF_CODE_BRIGHTNESS_MINUS)


def set_brightness(client: paho.Client, brightness):
    match brightness:
        case 1:
            send_action(RF_CODE_BRIGHTNESS_10)
        case 2:
            send_action(RF_CODE_BRIGHTNESS_20)
        case 3:
            send_action(RF_CODE_BRIGHTNESS_40)
        case 4:
            send_action(RF_CODE_BRIGHTNESS_60)
        case 5:
            send_action(RF_CODE_BRIGHTNESS_80)
        case 6:
            send_action(RF_CODE_BRIGHTNESS_100)
        case _:
            return
    client.publish(light_switch['brightness_state_topic'], brightness)
    client.publish(light_switch["effect_state_topic"], "OFF")


def on_connect(client: paho.Client, userdata, flags, reason_code, properties):
    if reason_code != 0:
        print("Error: Unable to connect to MQTT broker, return code:", reason_code)


def on_log(client: paho.Client, userdata, level, buf):
    # print("MQTT log:", buf)
    if level == paho.MQTT_LOG_ERR:
        print("MQTT error:", buf)


def on_subscribe(client: paho.Client, userdata, mid, reason_codes, properties: paho.Properties):
    print("Listening to topic:", userdata, mid, reason_codes, properties.json())


def send_action(rf_code):
    send_code(17, rf_code, 1, 385)


def send_code(gpio_pin, rf_code, rf_protocol: int = 1, rf_pulse_length=None):
    rf_device = None
    try:
        # Configure the RF transmitter
        rf_device = RFDevice(gpio_pin)
        rf_device.enable_tx()

        # Send the code
        rf_device.tx_code(rf_code, rf_protocol, rf_pulse_length)
    except NameError as e:
        print("'RFDevice' not accessible", e)
    finally:
        if rf_device is not None:
            rf_device.cleanup()


if hasattr(config, 'ha_device_name') and config.ha_device_name:
    hostname = re.sub(r'[^a-zA-Z0-9_-]', '_', config.ha_device_name)
else:
    hostname = re.sub(r'[^a-zA-Z0-9_-]', '_', socket.gethostname())


def create_base_entity(id, name, icon, component, command: bool = False):
    data = {
        "state_topic": f"{config.mqtt_topic_prefix}/{hostname}/{id}",
        "discovery_topic": f"{config.mqtt_discovery_prefix}/{component}/{hostname}/{id}/config",
        "unique_id": f"{hostname}_{id}",
        "device": build_device_info()
    }

    if name:
        data["name"] = name
    if icon:
        data["icon"] = icon
    if command:
        data["command_topic"] = f"{config.mqtt_topic_prefix}/{hostname}/{id}/set"

    # data["availability_topic"] = f"{data['state_topic']}_availability"

    return data


def create_on_off_switch():
    data = create_base_entity('on_off', "LED Element", "mdi:lightbulb", 'switch', True)
    # data["platform"] = "switch"

    return data


def create_light_switch(id, name, icon, brightness_scale: int = None, effects: list[str] = None):
    data = create_base_entity(id, name, icon, 'light', True)
    data["brightness_state_topic"] = data['state_topic'] + "/brightness"
    data["brightness_command_topic"] = data["brightness_state_topic"] + "/set"
    if brightness_scale is not None:
        data["brightness_scale"] = brightness_scale
    if effects is not None and len(effects) > 0:
        data["effect"] = True
        effects.insert(0, "Off")
        data["effect_list"] = effects
        data["effect_state_topic"] = data["state_topic"] + "/effect"
        data["effect_command_topic"] = data["effect_state_topic"] + "/set"

    return data


if __name__ == '__main__':
    client = paho.Client(paho.CallbackAPIVersion.VERSION2,
                         client_id="rpi-rf-mqtt-" + hostname + "_" + str(int(time.time())))
    client.username_pw_set(config.mqtt_user, config.mqtt_password)
    client.on_log = on_log
    client.on_connect = on_connect
    # client.on_subscribe = on_subscribe
    client.on_message = on_message
    client.will_set(config.mqtt_topic_prefix + "/" + hostname + "/status", "0",
                    qos=config.qos, retain=config.retain)
    try:
        client.connect(config.mqtt_host, int(config.mqtt_port))
    except Exception as e:
        print("Error connecting to MQTT broker:", e)
        sys.exit(1)

    # on_off_switch = create_on_off_switch()
    # client.publish(on_off_switch["discovery_topic"], json.dumps(on_off_switch))
    # client.publish(on_off_switch["availability_topic"], "online")
    # client.publish(on_off_switch["state_topic"], "OFF")

    light_switch = create_light_switch('light_switch', "LED Element", "mdi:lightbulb", 6, ["Jump", "Fade", "Strobe"])
    client.publish(light_switch["discovery_topic"], json.dumps(light_switch))
    client.subscribe(light_switch["command_topic"])
    client.subscribe(light_switch["brightness_command_topic"])
    client.subscribe(light_switch["effect_command_topic"])
    # client.publish(light_switch["availability_topic"], "online")
    # client.publish(light_switch["brightness_command_topic"], "OFF")
    # TODO
    time.sleep(1)
    client.publish(light_switch["state_topic"], "OFF")
    # client.publish(light_switch["brightness_state_topic"], "0")

    delay_off_button = create_base_entity('delay_off_button', "60s Delay OFF", "mdi:lightbulb-off-outline", 'button',
                                          True)
    client.publish(delay_off_button["discovery_topic"], json.dumps(delay_off_button))
    client.subscribe(delay_off_button["command_topic"])

    brightness_plus_button = create_base_entity('brightness_plus_button', "Brightness+", "mdi:brightness-7", 'button',
                                                True)
    client.publish(brightness_plus_button["discovery_topic"], json.dumps(brightness_plus_button))
    client.subscribe(brightness_plus_button["command_topic"])

    brightness_minus_button = create_base_entity('brightness_minus_button', "Brightness-", "mdi:brightness-5", 'button',
                                                 True)
    client.publish(brightness_minus_button["discovery_topic"], json.dumps(brightness_minus_button))
    client.subscribe(brightness_minus_button["command_topic"])

    try:
        client.loop_forever()
    except KeyboardInterrupt:
        print("Ctrl+C pressed. Exiting...")
