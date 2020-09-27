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
### Launching on startup

Instead of typing ```proot-distro login distro``` every time, you can just add it to the .bashrc. You can type ```nano .bashrc``` in your home directory and then type this: ```proot-distro login ubuntu```.
Note: you can put this in ~/.termux/shell but I'm not sure as to how.
