#!/usr/bin/env python

import json
import re
import socket
import sys
import time
import tomllib
import typing
import uuid

import logging
from abc import ABC, abstractmethod

import paho.mqtt.client as paho

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


class MqttEntity(ABC):
    def __init__(self, entity_id: str, name: str, icon: str, component: str):
        self.name = name
        self.icon = icon
        self.state_topic = f'{config["mqtt"]["topic_prefix"]}/{hostname}/{entity_id}'
        self.command_topic = f'{config["mqtt"]["topic_prefix"]}/{hostname}/{entity_id}/set'
        self.discovery_topic = f'{config["ha"]["discovery_prefix"]}/{component}/{hostname}/{entity_id}/config'
        self.unique_id = f'{hostname}_{entity_id}'
        self.device = build_device_info()

    def __str__(self):
        return str(vars(self))

    def build_discovery(self) -> dict[str, typing.Any]:
        return {
            "name": self.name,
            "icon": self.icon,
            "unique_id": self.unique_id,
            "device": self.device,
        }

    def initial_publish(self, client: paho.Client):
        if self.discovery_topic is not None:
            client.publish(self.discovery_topic, json.dumps(self.build_discovery()))

    def subscribe(self, client: paho.Client):
        if self.command_topic is not None:
            client.subscribe(self.command_topic)

    @abstractmethod
    def handle_message(self, client: paho.Client, topic: str, payload):
        pass


class Button(MqttEntity):
    """
    https://www.home-assistant.io/integrations/button.mqtt/
    """

    def __init__(self, entity_id: str, name: str, icon: str, rf_code: int, rf_protocol: int = 1,
                 rf_pulse_length: int = None,
                 rf_repeat: int = None):
        super().__init__(entity_id, name, icon, "button")
        self.rf_code = rf_code
        self.rf_protocol = rf_protocol
        self.rf_pulse_length = rf_pulse_length
        self.rf_repeat = rf_repeat

    def build_discovery(self) -> dict[str, typing.Any]:
        return super().build_discovery() | {
            "command_topic": self.command_topic,
        }

    def handle_message(self, client: paho.Client, topic: str, payload):
        if topic == self.command_topic:
            if payload == "PRESS":
                self.send_action(self.rf_code)

    def send_action(self, rf_code: int):
        send_code(rf_code, self.rf_protocol, self.rf_pulse_length, self.rf_repeat)


class Switch(MqttEntity):
    """
    https://www.home-assistant.io/integrations/switch.mqtt/
    """

    def __init__(self, entity_id: str, name: str, icon: str, rf_code_on: int, rf_code_off: int, rf_protocol: int = 1,
                 rf_pulse_length: int = None, rf_repeat: int = None):
        super().__init__(entity_id, name, icon, "switch")
        self.rf_code_on = rf_code_on
        self.rf_code_off = rf_code_off
        self.rf_protocol = rf_protocol
        self.rf_pulse_length = rf_pulse_length
        self.rf_repeat = rf_repeat

    def build_discovery(self) -> dict[str, typing.Any]:
        return super().build_discovery() | {
            "state_topic": self.state_topic,
            "command_topic": self.command_topic,
        }

    def handle_message(self, client: paho.Client, topic: str, payload):
        if topic == self.command_topic:
            if payload == "ON":
                self.send_action(self.rf_code_on)
                client.publish(self.state_topic, payload, retain=True)
            elif payload == "OFF":
                self.send_action(self.rf_code_off)
                client.publish(self.state_topic, payload, retain=True)

    def send_action(self, rf_code: int):
        send_code(rf_code, self.rf_protocol, self.rf_pulse_length, self.rf_repeat)


class LightSwitch(MqttEntity):
    """
    https://www.home-assistant.io/integrations/light.mqtt/
    """

    def __init__(self, entity_id: str, name: str, icon: str, rf_code_on: int, rf_code_off: int,
                 brightness_codes: list[int] = None, effects: dict[str, int] = None,
                 rf_protocol: int = 1, rf_pulse_length: int = None, rf_repeat: int = None):
        super().__init__(entity_id, name, icon, "light")
        self.state = None
        self.brightness_state = None

        self.rf_code_on = rf_code_on
        self.rf_code_off = rf_code_off
        self.rf_protocol = rf_protocol
        self.rf_pulse_length = rf_pulse_length
        self.rf_repeat = rf_repeat

        self.brightness_state_topic = self.state_topic + "/brightness"
        self.brightness_command_topic = self.brightness_state_topic + "/set"
        self.brightness_codes = brightness_codes
        if effects is not None and len(effects) > 0:
            effects.update({"Off": -1})
            self.effects = effects
            self.effect_state_topic = self.state_topic + "/effect"
            self.effect_command_topic = self.effect_state_topic + "/set"

    def build_discovery(self) -> dict[str, typing.Any]:
        return super().build_discovery() | {
            "state_topic": self.state_topic,
            "command_topic": self.command_topic,
            "brightness_state_topic": self.brightness_state_topic,
            "brightness_command_topic": self.brightness_command_topic,
            "brightness_scale": len(self.brightness_codes),
            "effect_list": list(self.effects.keys()),
            "effect_state_topic": self.effect_state_topic,
            "effect_command_topic": self.effect_command_topic,
        }

    def subscribe(self, client: paho.Client):
        super().subscribe(client)
        client.subscribe(self.state_topic)
        client.subscribe(self.brightness_state_topic)
        client.subscribe(self.brightness_command_topic)
        client.subscribe(self.effect_command_topic)

    def handle_message(self, client: paho.Client, topic: str, payload):
        if topic == self.state_topic:
            self.state = payload
        if topic == self.brightness_state_topic:
            self.brightness_state = int(payload)

        if topic == self.command_topic:
            if payload == "ON":
                self.send_action(self.rf_code_on)
                if self.state == "OFF" and self.brightness_state is not None:
                    self.send_brightness(self.brightness_state)
                client.publish(self.state_topic, payload, retain=True)

            elif payload == "OFF":
                self.send_action(self.rf_code_off)
                client.publish(self.state_topic, payload, retain=True)

        if topic == self.brightness_command_topic:
            if self.state != "ON":
                self.state = "ON"  # otherwise there is a race-condition between publishing and receiving as HA first sends a brightness command THEN a state command
                self.send_action(self.rf_code_on)
                client.publish(self.state_topic, "ON", retain=True)
            brightness = int(payload)
            self.set_brightness(client, brightness)

        if topic == self.effect_command_topic:
            if payload == "Off":
                self.set_brightness(client, self.brightness_state or 1)
                client.publish(self.effect_state_topic, "OFF", retain=True)
                return

            rf_code = self.effects[payload]
            if rf_code is not None:
                self.send_action(rf_code)
                client.publish(self.effect_state_topic, payload, retain=True)

    def send_brightness(self, brightness: int) -> bool:
        brightness -= 1
        if len(self.brightness_codes) > brightness >= 0:
            rf_code = self.brightness_codes[brightness]
            self.send_action(rf_code)
            return True
        return False

    def set_brightness(self, client: paho.Client, brightness: int):
        success = self.send_brightness(brightness)
        if success:
            client.publish(self.brightness_state_topic, brightness, retain=True)
            client.publish(self.effect_state_topic, "OFF", retain=True)

    def send_action(self, rf_code: int):
        send_code(rf_code, self.rf_protocol, self.rf_pulse_length, self.rf_repeat)


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


def send_code(rf_code, rf_protocol: int = 1, rf_pulse_length: int = None, rf_repeat: int = None):
    rf_device = None
    try:
        if rf_repeat is None:
            rf_repeat = default_rf_repeat

        # Configure the RF transmitter
        rf_device = RFDevice(rf_gpio_pin)
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


def create_entities(config_entities):
    configured_entities = []
    for key, value in config_entities.items():
        if isinstance(value, dict):
            match value.get("type"):
                case "light":
                    configured_entities.append(LightSwitch(
                        key,
                        value.get("name"),
                        value.get("icon"),
                        value.get("rf_code_on"),
                        value.get("rf_code_off"),
                        value.get("brightness_levels"),
                        value.get("effects"),
                        value.get("rf_protocol", default_rf_protocol),
                        value.get("rf_pulse_length"),
                        value.get("rf_repeat", default_rf_repeat),
                    ))
                case "switch":
                    configured_entities.append(Switch(
                        key,
                        value.get("name"),
                        value.get("icon"),
                        value.get("rf_code_on"),
                        value.get("rf_code_off"),
                        value.get("rf_protocol", default_rf_protocol),
                        value.get("rf_pulse_length"),
                        value.get("rf_repeat", default_rf_repeat),
                    ))
                case "button":
                    configured_entities.append(Button(
                        key,
                        value.get("name"),
                        value.get("icon"),
                        value.get("rf_code"),
                        value.get("rf_protocol", default_rf_protocol),
                        value.get("rf_pulse_length"),
                        value.get("rf_repeat", default_rf_repeat),
                    ))
                case _:
                    logging.info("%s: Invalid entity type: %s", key, value.get("type"))
    return configured_entities


def on_message(client: paho.Client, userdata, msg: paho.MQTTMessage):
    payload = msg.payload.decode()
    logging.info("MQTT: Received message: %s %s, retain=%s", msg.topic, payload, msg.retain)

    if config["ha"]["birth_topic"] is not None:
        if msg.topic == config["ha"]["birth_topic"] and payload == config["ha"]["birth_payload"]:
            # TODO add a randomized delay
            publish_entity_discovery_messages(client)

    for entity in entities:
        entity.handle_message(client, msg.topic, payload)


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
        if config["ha"]["birth_topic"] is not None and config["ha"]["birth_payload"] is not None:
            client.subscribe(config["ha"]["birth_topic"])
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
    with open("config.toml", "rb") as f:
        config = tomllib.load(f)

    device_name = config["ha"].get("device_name") if isinstance(config["ha"], dict) else None
    hostname = re.sub(r'[^a-zA-Z0-9_-]', '_', device_name or socket.gethostname())

    rf_gpio_pin = config["rf"].get("gpio_pin", 17) if isinstance(config["rf"], dict) else None
    default_rf_protocol = config["rf"].get("protocol", 1) if isinstance(config["rf"], dict) else None
    default_rf_repeat = config["rf"].get("repeat", 10) if isinstance(config["rf"], dict) else None

    if len(config) == 0 or config.get("entities") is None or len(config.get("entities")) == 0:
        logging.error("No entities defined in config")
        exit(1)

    entities = create_entities(config.get("entities"))

    client = paho.Client(paho.CallbackAPIVersion.VERSION2,
                         client_id=config["mqtt"]["topic_prefix"] + "-" + hostname + "_" + str(int(time.time())))
    client.enable_logger(logging.root)
    client.username_pw_set(config["mqtt"]["user"], config["mqtt"]["password"])
    client.on_log = on_log
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    # client.will_set(config["mqtt"]["topic_prefix"] + "/" + hostname + "/status", "0",
    #                 qos=config.qos, retain=config.retain)
    client.connect_async(config["mqtt"]["host"], int(config["mqtt"]["port"]))

    try:
        client.loop_forever(retry_first_connection=True)
    except KeyboardInterrupt:
        logging.info("Exiting...")
