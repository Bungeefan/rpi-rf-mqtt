#!/usr/bin/env python

import json
import re
import socket
import sys
import time
import typing
import uuid

import paho.mqtt.client as paho

import config

RF_GPIO_PIN = 17
RF_REPEAT = 10

RF_LED_PROTOCOL = 1
RF_LED_PULSE_LENGTH = 385

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

RF_PLUG_PROTOCOL = 4
RF_PLUG_PULSE_LENGTH = 340

RF_CODE_PLUG_A_ON = 3323996
RF_CODE_PLUG_A_OFF = 4099212

RF_CODE_PLUG_B_ON = 3513605
RF_CODE_PLUG_B_OFF = 3667925

RF_CODE_PLUG_C_ON = 3466030
RF_CODE_PLUG_C_OFF = 4005998

try:
    from rpi_rf import RFDevice
except Exception as importExc:
    print("Can't import RFDevice, actions won't work:", importExc)


class MqttEntity(dict):
    def __init__(self, data=None, **kwargs):
        if data is not None:
            super().__init__(**data)  # Unpack the dictionary into the MqttEntity
        super().__init__(**kwargs)

    def initial_publish(self, client: paho.Client):
        if self["discovery_topic"] is not None:
            client.publish(self["discovery_topic"], json.dumps(self))

    def subscribe(self, client: paho.Client):
        if self["command_topic"] is not None:
            client.subscribe(self["command_topic"])


class Switch(MqttEntity):
    def __init__(self, data=None, **kwargs):
        super().__init__(data, **kwargs)


class LightSwitch(MqttEntity):
    def __init__(self, data=None, **kwargs):
        super().__init__(data, **kwargs)

    def initial_publish(self, client: paho.Client):
        super().initial_publish(client)
        # client.publish(self["availability_topic"], "online")
        # client.publish(self["brightness_command_topic"], "OFF")
        # time.sleep(1)
        # client.publish(self["state_topic"], "OFF")
        # client.publish(self["brightness_state_topic"], "0")

    def subscribe(self, client: paho.Client):
        super().subscribe(client)
        client.subscribe(self["brightness_command_topic"])
        client.subscribe(self["effect_command_topic"])


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


def create_base_entity(id: str, name: str, icon: str, component: str, command: bool = False) -> dict[str, typing.Any]:
    entity = {
        "state_topic": f"{config.mqtt_topic_prefix}/{hostname}/{id}",
        "discovery_topic": f"{config.mqtt_discovery_prefix}/{component}/{hostname}/{id}/config",
        "unique_id": f"{hostname}_{id}",
        "device": build_device_info()
    }

    if name:
        entity["name"] = name
    if icon:
        entity["icon"] = icon
    if command:
        entity["command_topic"] = f"{config.mqtt_topic_prefix}/{hostname}/{id}/set"

    # entity["availability_topic"] = f"{entity['state_topic']}_availability"

    return entity


def create_button(id: str, name: str, icon: str):
    return MqttEntity(create_base_entity(id, name, icon, "button", True))


def create_switch(id: str, name: str, icon: str):
    return Switch(create_base_entity(id, name, icon, "switch", True))


def create_light_switch(id: str, name: str, icon: str, brightness_scale: int = None,
                        effects: list[str] = None) -> LightSwitch:
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

    return LightSwitch(data)


def create_entities():
    global entities, light_switch, delay_off_button, brightness_plus_button, brightness_minus_button, plug_a, plug_b, plug_c

    # on_off_switch = create_switch('on_off', "LED Element", "mdi:lightbulb")

    light_switch = create_light_switch('light_switch', "LED Element", "mdi:lightbulb", 6, ["Jump", "Fade", "Strobe"])

    delay_off_button = create_button('delay_off_button', "60s Delay OFF", "mdi:lightbulb-off-outline")
    brightness_plus_button = create_button('brightness_plus_button', "Brightness+", "mdi:brightness-7")
    brightness_minus_button = create_button('brightness_minus_button', "Brightness-", "mdi:brightness-5")

    plug_a = create_switch('wireless_plug_a', 'Plug A', 'mdi:power')
    plug_b = create_switch('wireless_plug_b', 'Plug B', 'mdi:power')
    plug_c = create_switch('wireless_plug_c', 'Plug C', 'mdi:power')

    entities = [
        light_switch,
        delay_off_button,
        brightness_plus_button,
        brightness_minus_button,

        plug_a,
        plug_b,
        plug_c,
    ]


def on_message(client: paho.Client, userdata, msg: paho.MQTTMessage):
    payload = msg.payload.decode()
    print("Received message:", msg.topic, payload)

    if msg.topic == light_switch["command_topic"]:
        if payload == "ON":
            send_led_action(RF_CODE_ON)
            client.publish(light_switch['state_topic'], payload, retain=True)
        elif payload == "OFF":
            send_led_action(RF_CODE_OFF)
            client.publish(light_switch['state_topic'], payload, retain=True)

    if msg.topic == light_switch["brightness_command_topic"]:
        brightness = int(payload)
        set_brightness(client, brightness)

    if msg.topic == light_switch["effect_command_topic"]:
        if payload == "Jump":
            send_led_action(RF_CODE_JUMP)
            client.publish(light_switch["effect_state_topic"], payload, retain=True)
        elif payload == "Fade":
            send_led_action(RF_CODE_FADE)
            client.publish(light_switch["effect_state_topic"], payload, retain=True)
        elif payload == "Strobe":
            send_led_action(RF_CODE_STROBE)
            client.publish(light_switch["effect_state_topic"], payload, retain=True)
        elif payload == "Off":
            set_brightness(client, 1)
            client.publish(light_switch["effect_state_topic"], "OFF", retain=True)

    if msg.topic == delay_off_button["command_topic"]:
        if payload == "PRESS":
            send_led_action(RF_CODE_60S_OFF)

    if msg.topic == brightness_plus_button["command_topic"]:
        if payload == "PRESS":
            send_led_action(RF_CODE_BRIGHTNESS_PLUS, 6)

    if msg.topic == brightness_minus_button["command_topic"]:
        if payload == "PRESS":
            send_led_action(RF_CODE_BRIGHTNESS_MINUS, 6)

    if msg.topic == plug_a["command_topic"]:
        if payload == "ON":
            send_plug_action(RF_CODE_PLUG_A_ON)
            client.publish(plug_a['state_topic'], payload, retain=True)
        elif payload == "OFF":
            send_plug_action(RF_CODE_PLUG_A_OFF)
            client.publish(plug_a['state_topic'], payload, retain=True)

    if msg.topic == plug_b["command_topic"]:
        if payload == "ON":
            send_plug_action(RF_CODE_PLUG_B_ON)
            client.publish(plug_b['state_topic'], payload, retain=True)
        elif payload == "OFF":
            send_plug_action(RF_CODE_PLUG_B_OFF)
            client.publish(plug_b['state_topic'], payload, retain=True)

    if msg.topic == plug_c["command_topic"]:
        if payload == "ON":
            send_plug_action(RF_CODE_PLUG_C_ON)
            client.publish(plug_c['state_topic'], payload, retain=True)
        elif payload == "OFF":
            send_plug_action(RF_CODE_PLUG_C_OFF)
            client.publish(plug_c['state_topic'], payload, retain=True)


def set_brightness(client: paho.Client, brightness: int):
    match brightness:
        case 1:
            send_led_action(RF_CODE_BRIGHTNESS_10)
        case 2:
            send_led_action(RF_CODE_BRIGHTNESS_20)
        case 3:
            send_led_action(RF_CODE_BRIGHTNESS_40)
        case 4:
            send_led_action(RF_CODE_BRIGHTNESS_60)
        case 5:
            send_led_action(RF_CODE_BRIGHTNESS_80)
        case 6:
            send_led_action(RF_CODE_BRIGHTNESS_100)
        case _:
            return
    client.publish(light_switch['brightness_state_topic'], brightness, retain=True)
    client.publish(light_switch["effect_state_topic"], "OFF", retain=True)


def send_led_action(rf_code, rf_repeat=RF_REPEAT):
    send_code(rf_code, RF_LED_PROTOCOL, RF_LED_PULSE_LENGTH, rf_repeat)


def send_plug_action(rf_code, rf_repeat=RF_REPEAT):
    send_code(rf_code, RF_PLUG_PROTOCOL, RF_PLUG_PULSE_LENGTH, rf_repeat)


def send_code(rf_code, rf_protocol: int = 1, rf_pulse_length: int = None, rf_repeat: int = RF_REPEAT):
    rf_device = None
    try:
        # Configure the RF transmitter
        rf_device = RFDevice(RF_GPIO_PIN)
        rf_device.enable_tx()
        rf_device.tx_repeat = rf_repeat

        # Send the code
        print("Sending code:", rf_code, "with protocol:", rf_protocol, "pulse length:", rf_pulse_length, "repetitions:",
              rf_repeat)
        success = rf_device.tx_code(rf_code, rf_protocol, tx_pulselength=rf_pulse_length)
        if not success:
            print("Error: Failed to send code")
    except NameError as e:
        print("'RFDevice' not accessible", e)
    finally:
        if rf_device is not None:
            rf_device.cleanup()


if hasattr(config, 'ha_device_name') and config.ha_device_name:
    hostname = re.sub(r'[^a-zA-Z0-9_-]', '_', config.ha_device_name)
else:
    hostname = re.sub(r'[^a-zA-Z0-9_-]', '_', socket.gethostname())


def on_connect(client: paho.Client, userdata, flags: paho.ConnectFlags, reason_code: paho.ReasonCode,
               properties: paho.Properties):
    if reason_code != 0:
        print("Error: Unable to connect to MQTT broker, reason code:", reason_code)
    else:
        print("Connected to MQTT broker")
        for entity in entities:
            entity.subscribe(client)
        for entity in entities:
            entity.initial_publish(client)


def on_disconnect(client: paho.Client, userdata, flags: paho.DisconnectFlags, reason_code: paho.ReasonCode,
                  properties: paho.Properties):
    if reason_code != 0:
        print("Disconnected from MQTT broker, reason code:", reason_code)
    else:
        print("Disconnected from MQTT broker")


def on_log(client: paho.Client, userdata, level: int, msg: str):
    if level == paho.MQTT_LOG_INFO:
        print("MQTT info:", msg)
    if level == paho.MQTT_LOG_WARNING:
        print("MQTT warn:", msg)
    if level == paho.MQTT_LOG_ERR:
        print("MQTT error:", msg)


if __name__ == '__main__':
    create_entities()

    client = paho.Client(paho.CallbackAPIVersion.VERSION2,
                         client_id="rpi-rf-mqtt-" + hostname + "_" + str(int(time.time())))
    client.username_pw_set(config.mqtt_user, config.mqtt_password)
    client.on_log = on_log
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    # client.will_set(config.mqtt_topic_prefix + "/" + hostname + "/status", "0",
    #                 qos=config.qos, retain=config.retain)
    client.connect_async(config.mqtt_host, int(config.mqtt_port))

    try:
        client.loop_forever(retry_first_connection=True)
    except KeyboardInterrupt:
        print("Ctrl+C pressed. Exiting...")
