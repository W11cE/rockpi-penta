# ROCK Pi Penta SATA

Top Board control program

[Penta SATA HAT wiki](<https://wiki.radxa.com/Penta_SATA_HAT>)

[Penta SATA HAT docs](https://docs.radxa.com/en/accessories/penta-sata-hat)

![penta-hat](images/penta-sata-hat.png)

## Installation

Run the provided `install.sh` script on the target system. It installs
`python3-venv`, `smartmontools` and other prerequisites, sets up the
Python environment and enables the service:

```bash
sudo ./rockpi-penta/install.sh
```

After installation, monitor the daemon with:

```bash
sudo journalctl -u rockpi-penta -f
```
