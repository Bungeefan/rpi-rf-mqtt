#!/usr/bin/env python

import json
import re
import socket
import sys
import time
import uuid

import logging
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

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)

try:
    from rpi_rf import RFDevice
except Exception as importExc:
    logging.warning("Can't import RFDevice, actions won't work:", exc_info=importExc)


class MqttEntity:
    def __init__(self, entity_id: str, name: str, icon: str, component: str):
        self.name = name
        self.icon = icon
        self.state_topic = f"{config.mqtt_topic_prefix}/{hostname}/{entity_id}"
        self.command_topic = f"{config.mqtt_topic_prefix}/{hostname}/{entity_id}/set"
        self.discovery_topic = f"{config.mqtt_discovery_prefix}/{component}/{hostname}/{entity_id}/config"
        self.unique_id = f"{hostname}_{entity_id}"
        self.device = build_device_info()

    def initial_publish(self, client: paho.Client):
        if self.discovery_topic is not None:
            client.publish(self.discovery_topic, json.dumps(vars(self)))

    def subscribe(self, client: paho.Client):
        if self.command_topic is not None:
            client.subscribe(self.command_topic)


class Button(MqttEntity):
    def __init__(self, entity_id: str, name: str, icon: str):
        super().__init__(entity_id, name, icon, "button")


class Switch(MqttEntity):
    def __init__(self, entity_id: str, name: str, icon: str):
        super().__init__(entity_id, name, icon, "switch")


class LightSwitch(MqttEntity):
    def __init__(self, entity_id: str, name: str, icon: str,
                 brightness_scale: int = None, effects: list[str] = None):
        super().__init__(entity_id, name, icon, "light")
        self.brightness_state_topic = self.state_topic + "/brightness"
        self.brightness_command_topic = self.brightness_state_topic + "/set"
        if brightness_scale is not None:
            self.brightness_scale = brightness_scale
        if effects is not None and len(effects) > 0:
            self.effect = True
            effects.insert(0, "Off")
            self.effect_list = effects
            self.effect_state_topic = self.state_topic + "/effect"
            self.effect_command_topic = self.effect_state_topic + "/set"

    def subscribe(self, client: paho.Client):
        super().subscribe(client)
        client.subscribe(self.state_topic)
        client.subscribe(self.brightness_state_topic)
        client.subscribe(self.brightness_command_topic)
        client.subscribe(self.effect_command_topic)


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


def create_entities():
    global entities, light_switch, delay_off_button, brightness_plus_button, brightness_minus_button, plug_a, plug_b, plug_c

    # on_off_switch = Switch('on_off', "LED Element", "mdi:lightbulb")

    light_switch = LightSwitch('light_switch', "LED Element", "mdi:lightbulb", 6, ["Jump", "Fade", "Strobe"])

    delay_off_button = Button('delay_off_button', "60s Delay OFF", "mdi:lightbulb-off-outline")
    brightness_plus_button = Button('brightness_plus_button', "Brightness+", "mdi:brightness-7")
    brightness_minus_button = Button('brightness_minus_button', "Brightness-", "mdi:brightness-5")

    plug_a = Switch('wireless_plug_a', 'Plug A', 'mdi:power')
    plug_b = Switch('wireless_plug_b', 'Plug B', 'mdi:power')
    plug_c = Switch('wireless_plug_c', 'Plug C', 'mdi:power')

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
    logging.info("MQTT: Received message: %s %s, retain=%s", msg.topic, payload, msg.retain)

    if config.mqtt_ha_birth_topic is not None:
        if msg.topic == config.mqtt_ha_birth_topic and payload == config.mqtt_ha_birth_payload:
            # TODO add a randomized delay
            publish_entity_discovery_messages(client)

    if msg.topic == light_switch.state_topic:
        light_switch.state = payload

    if msg.topic == light_switch.brightness_state_topic:
        light_switch.brightness_state = int(payload)

    if msg.topic == light_switch.command_topic:
        if payload == "ON":
            send_led_action(RF_CODE_ON)
            if light_switch.state == "OFF" and light_switch.brightness_state is not None:
                send_brightness(light_switch.brightness_state)
            client.publish(light_switch.state_topic, payload, retain=True)

        elif payload == "OFF":
            send_led_action(RF_CODE_OFF)
            client.publish(light_switch.state_topic, payload, retain=True)

    if msg.topic == light_switch.brightness_command_topic:
        if light_switch.state != "ON":
            light_switch.state = "ON"  # otherwise there is a race-condition between publishing and receiving as HA first sends a brightness command THEN a state command
            send_led_action(RF_CODE_ON)
            client.publish(light_switch.state_topic, "ON", retain=True)
        brightness = int(payload)
        set_brightness(client, brightness)

    if msg.topic == light_switch.effect_command_topic:
        if payload == "Jump":
            send_led_action(RF_CODE_JUMP)
            client.publish(light_switch.effect_state_topic, payload, retain=True)
        elif payload == "Fade":
            send_led_action(RF_CODE_FADE)
            client.publish(light_switch.effect_state_topic, payload, retain=True)
        elif payload == "Strobe":
            send_led_action(RF_CODE_STROBE)
            client.publish(light_switch.effect_state_topic, payload, retain=True)
        elif payload == "Off":
            set_brightness(client, 1)
            client.publish(light_switch.effect_state_topic, "OFF", retain=True)

    if msg.topic == delay_off_button.command_topic:
        if payload == "PRESS":
            send_led_action(RF_CODE_60S_OFF)

    if msg.topic == brightness_plus_button.command_topic:
        if payload == "PRESS":
            send_led_action(RF_CODE_BRIGHTNESS_PLUS, 6)

    if msg.topic == brightness_minus_button.command_topic:
        if payload == "PRESS":
            send_led_action(RF_CODE_BRIGHTNESS_MINUS, 6)

    if msg.topic == plug_a.command_topic:
        if payload == "ON":
            send_plug_action(RF_CODE_PLUG_A_ON)
            client.publish(plug_a.state_topic, payload, retain=True)
        elif payload == "OFF":
            send_plug_action(RF_CODE_PLUG_A_OFF)
            client.publish(plug_a.state_topic, payload, retain=True)

    if msg.topic == plug_b.command_topic:
        if payload == "ON":
            send_plug_action(RF_CODE_PLUG_B_ON)
            client.publish(plug_b.state_topic, payload, retain=True)
        elif payload == "OFF":
            send_plug_action(RF_CODE_PLUG_B_OFF)
            client.publish(plug_b.state_topic, payload, retain=True)

    if msg.topic == plug_c.command_topic:
        if payload == "ON":
            send_plug_action(RF_CODE_PLUG_C_ON)
            client.publish(plug_c.state_topic, payload, retain=True)
        elif payload == "OFF":
            send_plug_action(RF_CODE_PLUG_C_OFF)
            client.publish(plug_c.state_topic, payload, retain=True)


def set_brightness(client: paho.Client, brightness: int):
    success = send_brightness(brightness)
    if success:
        client.publish(light_switch.brightness_state_topic, brightness, retain=True)
        client.publish(light_switch.effect_state_topic, "OFF", retain=True)


def send_brightness(brightness: int) -> bool:
    rf_code: int | None = None
    match brightness:
        case 1:
            rf_code = RF_CODE_BRIGHTNESS_10
        case 2:
            rf_code = RF_CODE_BRIGHTNESS_20
        case 3:
            rf_code = RF_CODE_BRIGHTNESS_40
        case 4:
            rf_code = RF_CODE_BRIGHTNESS_60
        case 5:
            rf_code = RF_CODE_BRIGHTNESS_80
        case 6:
            rf_code = RF_CODE_BRIGHTNESS_100
    if rf_code is not None:
        send_led_action(rf_code)
        return True
    return False


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
        logging.info("Sending code: %s with protocol: %s, pulse_length: %s, repetitions: %s", rf_code, rf_protocol,
                     rf_pulse_length, rf_repeat)
        success = rf_device.tx_code(rf_code, rf_protocol, tx_pulselength=rf_pulse_length)
        if not success:
            logging.error("Failed to send code")
    except NameError as e:
        logging.error("Unable to send code: 'RFDevice' not accessible", exc_info=e)
    finally:
        if rf_device is not None:
            rf_device.cleanup()


if hasattr(config, 'ha_device_name') and config.ha_device_name:
    hostname = re.sub(r'[^a-zA-Z0-9_-]', '_', config.ha_device_name)
else:
    hostname = re.sub(r'[^a-zA-Z0-9_-]', '_', socket.gethostname())


def publish_entity_discovery_messages(client: paho.Client):
    for entity in entities:
        entity.initial_publish(client)
    logging.info("MQTT: Published discovery messages")


def on_connect(client: paho.Client, userdata, flags: paho.ConnectFlags, reason_code: paho.ReasonCode,
               properties: paho.Properties):
    if reason_code != 0:
        logging.error("MQTT: Unable to connect to broker, reason code: %s", reason_code)
    else:
        logging.info("MQTT: Connected to broker")
        if config.mqtt_ha_birth_topic is not None and config.mqtt_ha_birth_payload is not None:
            client.subscribe(config.mqtt_ha_birth_topic)
        else:
            publish_entity_discovery_messages(client)

        for entity in entities:
            entity.subscribe(client)


def on_disconnect(client: paho.Client, userdata, flags: paho.DisconnectFlags, reason_code: paho.ReasonCode,
                  properties: paho.Properties):
    if reason_code != 0:
        logging.info("MQTT: Disconnected from broker, reason code: %s", reason_code)
    else:
        logging.info("MQTT: Disconnected from broker")


def on_log(client: paho.Client, userdata, level: int, msg: str):
    if level == paho.MQTT_LOG_DEBUG and "Connection" in msg:
        logging.info("MQTT: %s", msg)


if __name__ == '__main__':
    create_entities()

    client = paho.Client(paho.CallbackAPIVersion.VERSION2,
                         client_id="rpi-rf-mqtt-" + hostname + "_" + str(int(time.time())))
    client.enable_logger(logging.root)
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
        logging.info("Exiting...")
