# rpi-RF-MQTT

RF MQTT Client script for Raspberry Pi using [rpi-rf](https://github.com/milaq/rpi-rf).

## Install

```sh
cd rpi-rf-mqtt/
```

### Install dependencies (in venv)

```sh
sh install_dependencies.sh
```

## Run

### Manually (without service)

```sh
python3 rpi-rf-mqtt.py
```

### Service

#### Enable and start service

```sh
sudo systemctl enable $(/bin/readlink -f rpi-rf-mqtt.service)
sudo systemctl start rpi-rf-mqtt.service
```

---

### Helpful resources:

* https://www.instructables.com/Super-Simple-Raspberry-Pi-433MHz-Home-Automation/
* https://github.com/sui77/rc-switch/issues/103
