A Bash script for managing proot'ed Linux distributions in Termux.

For now it supports installation of these distributions:

* Alpine Linux
* Arch Linux
* Kali Nethunter (rootless)
* Ubuntu (18.04 / 20.04)

## Usage example

Install package in Termux:
```
pkg install proot-distro
```

Example on how to install Ubuntu and launch shell:
```
proot-distro install ubuntu
proot-distro login ubuntu
```
