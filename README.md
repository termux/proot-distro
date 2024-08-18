# PRoot Distro

A Bash script wrapper for utility [proot] for easy management of chroot-based
Linux distribution installations. It does not require root or any special ROM,
kernel, etc. Everything you need to get started is the latest version of
[Termux] application. See [Installing](#installing) for details.

PRoot Distro is not a virtual machine. This is a container environment manager
based on `proot` utility which able to emulate `chroot` and `mount --bind`.

This script should never be run as root user. If you do so, file permissions
and SELinux labels could get messed up. There also possibility of damaging
system if being executed as root. For safety, PRoot Distro checks the user id
before run and refuses to work if detected user id `0` (root).

## Donate

Although you get PRoot-Distro for free, remember that developers of such
free projects have to spend their free time after work or on weekends to
keep them up. If you found PRoot-Distro to be useful, interested in new
features and want to support the developer, consider donating.

As of now donations can be made in cryptocurrency:

* Ethereum: `0x76c7f1FC3C7396396fBD7e8cBDc2fc741FFa4aFa`
* Litecoin: `ltc1q2yne7e2p5ypf2ky0j3tg3vd6yktd5u57rmlly9`
* Tron: `TUP941DmHfrBNxvbcYkThx9hHrskU7FyTa`

***

## Supported distributions

PRoot Distro provides support only one version of distribution types, i.e. one
of stable, LTS or rolling-release. Support of versioned distributions ended
with branch 2.x.

PRoot Distro tends to provide only one version of given distributions to reduce
maintenance requirements. Thus we will choose a one of LTS, stable or rolling
release, while LTS is the most preferred and rolling is the least. At our
discretion we may choose to provide 1 or 2 of old LTS or stable releases for
the top distributions.

Here are the supported distributions (alias: name):

* `alpine`: Alpine Linux (edge)
* `archlinux`: Arch Linux ARM
* `artix`: Artix Linux (AArch64 only)
* `debian`: Debian (stable)
* `debian-oldstable`: Debian (old stable)
* `deepin`: Deepin (beige)
* `fedora`: Fedora 40 (64bit only)
* `manjaro`: Manjaro (AArch64 only)
* `openkylin`: OpenKylin (Yangtze)
* `opensuse`: OpenSUSE (Tumbleweed)
* `pardus`: Pardus (yirmibir)
* `ubuntu`: Ubuntu (24.04)
* `ubuntu-oldlts`: Ubuntu (22.04)
* `void`: Void Linux

All systems come in a bare-minimum variant, typically consisting of package
manager, shell, coreutils, util-linux and few more. Extended functionality
like shell completion or package install suggestions should be configured
manually.

If you need a custom version, you will need to add it on your own.
See [Adding distribution](#adding-distribution).

All rootfs archives provided by this project are built using [GitHub Actions](https://github.com/termux/proot-distro/actions):

* Configuration: https://github.com/termux/proot-distro/blob/master/.github/workflows/build.yml
* Rootfs packaging scripts: https://github.com/termux/proot-distro/tree/master/distro-build

Feel free to fork repository, make changes and build own distributions.

### Security

The distributions available in the catalog derived from the official sources.
Tarballs include the latest system upgrades and security patches during their
build time.

It is highly recommended to check for updates using the package manager after
installing a distribution as tarballs are updated on opportunistic basis (once
in few months).

PRoot (core of `proot-distro`) does not provide high grade isolation like
Docker. Consider this if you suspect that your installation can be an attack
target or your Android OS is too old.

## Installing

With package manager:
```
pkg install proot-distro
```

With git:
```
pkg install git
git clone https://github.com/termux/proot-distro
cd proot-distro
./install.sh
```

Dependencies: bash, bzip2, coreutils, curl, findutils, gzip, ncurses-utils,
proot, sed, tar, xz-utils

If you want command line auto complete, install the `bash-completion` package.

## Functionality overview

PRoot Distro aims to provide all-in-one functionality for managing the
installed distributions: installation, de-installation, backup, restore, login.
Each action is defined through command. Each command accepts its unique set
of options, specific to the task that it performs.

Usage basics:
```
proot-distro <command> <arguments>
```

Alternative variant (v4.0.0+):
```
pd <command> <arguments>
```

Where `<command>` is a proot-distro action command (see below to learn what
is available) and `<arguments>` is a list of options specific to given command.

Example of installing the distribution:
```
proot-distro install debian
```

Some commands support aliases. For example, instead of
```
proot-distro list
proot-distro install debian
proot-distro login debian
proot-distro remove debian
```

you can type this:
```
proot-distro ls
proot-distro i debian
proot-distro sh debian
proot-distro rm debian
```

Information about supported aliases can be viewed in help for each command.

Known distributions are defined through plug-in scripts, which define URLs
from where root file system archive will be downloaded and set of checksums
for integrity check. Plug-ins also can define a set of commands which would
be executed during distribution installation.

See [Adding distribution](#adding-distribution) to learn more how to add own
distribution to PRoot Distro.

### Accessing built-in help

Command: `help`

This command will show the help information about `proot-distro` usage.
* `proot-distro help` - main page.
* `proot-distro <command> --help` - view help for specific command.

### Backing up distribution

Command: `backup`

Aliases: `bak`, `bkp`

Backup specified distribution and its plug-in into tar archive. The contents
of backup can be either printed to stdout for further processing or written
to a file.

Compression is determined according to file extension, e.g.`.tar.gz` will lead
to GZip compression and `.tar.xz` will lead to XZ. Piped backup data is always
not compressed giving user freedom for further processing.

Usage example:
```
proot-distro backup debian | xz | ssh example.com 'cat > /backups/pd-debian-backup.tar.xz'
proot-distro backup --output backup.tar.gz debian
```

*This command is generic. All additional processing like encryption should be
done by user through external commands.*

### Installing a distribution

Command: `install`

Aliases: `add`, `i`, `in`, `ins`

Install a distribution specified by alias - a short name referring to the
plug-in of chosen distribution.

Usage example:
```
proot-distro install alpine
```

By default the installed distribution will have same alias as specified on
command line. This means you will be unable to install multiple copies at
same time. You can rename distribution during installation time by using
option `--override-alias` which will create a copy of distribution plug-in.

Usage example:
```
proot-distro install --override-alias alpine-test alpine
proot-distro login alpine-test
```

Copied plug-in has following name format `<name>.override.sh` and is stored
in directory with others (`$PREFIX/etc/proot-distro`).

It is possible to force specify a custom CPU architecture of distribution to
install. To do this you need to set `DISTRO_ARCH` environment variable to
one of these values: `aarch64`, `arm`, `i686`, `x86_64`. Example:

```
DISTRO_ARCH=arm proot-distro install alpine
```

Typically if your host is 64bit, the 32bit version of distribution for same
architecture should work seamlessly, but that's not guaranteed. Thus if you
encounter an issue while using ARM version of the system on AArch64 host,
this would be rather a bug of [proot](https://github.com/termux/proot) utility
or incompatibility with CPU instructions supported by host.

Usage of foreign architectures, like x86_64 target on AArch64 host, always
would require QEMU user mode packages.

Install all supported QEMU user mode packages with one command:

```
pkg install qemu-user-aarch64 qemu-user-arm qemu-user-i386 qemu-user-x86-64
```

`x86_64` target also supports a Blink user mode CPU emulator (experimental).
See [below](#experimental-blink-emulator-support) for usage details.

### Listing distributions

Command: `list`

Aliases: `li`, `ls`

Shows a list of available distributions, their aliases, installation status
and comments.

### Start shell session

Command: `login`

Aliases: `sh`

Execute a shell within the given distribution. Example:
```
proot-distro login debian
```

Execute a shell as specified user in the given distribution:
```
proot-distro login --user admin debian
```

You can run a custom command as well:
```
proot-distro login debian -- /usr/local/bin/mycommand --sample-option1
```

Argument `--` acts as terminator of `proot-distro login` options processing.
All arguments behind it would not be treated as options of PRoot Distro.

Login command supports these behavior modifying options:
* `--user <username>`

  Use a custom login user instead of default `root`. You need to create the
  user via `useradd -U -m username` before using this option.

* `--fix-low-ports`

  Force redirect low networking ports to a high number (2000 + port). Use
  this with software requiring low ports which are not possible without real
  root permissions.

  For example this option will redirect port 80 to something like 2080.

* `--isolated`

  Do not mount host volumes inside chroot environment. If this option was
  given, following mount points will not be accessible inside chroot:

  * /apex (only Android 10+)
  * /data/dalvik-cache
  * /data/data/com.termux
  * /sdcard
  * /storage
  * /system
  * /vendor

  You will not be able to use Termux utilities inside chroot environment.

* `--termux-home`

  Mount Termux home directory as user home inside chroot environment.

  This option takes priority over option `--isolated`.

* `--shared-tmp`

  Share Termux temporary directory with chroot environment. Takes priority
  over option `--isolated`.

* `--bind path:path`

  Create a custom file system path binding. Option expects argument in the
  given format:
  ```
  <host path>:<chroot path>
  ```

  Takes priority over option `--isolated`.

* `--no-link2symlink`

  Disable PRoot link2symlink extension. This will disable hard link emulation.
  You can use this option only if SELinux is disabled or is in permissive mode.

* `--no-sysvipc`

  Disable PRoot System V IPC emulation. Try this option if you experience
  crashes.

* `--no-kill-on-exit`

  Do not kill processes when shell session terminates. Typically will cause
  session to hang if you have any background processes running.

* `--kernel`

  Set the kernel release and compatibility level to given value.

* `--work-dir`

  Set the working directory to given value. By default the working directory
  is same as user home.

### Uninstall distribution

Command: `remove`

Aliases: `rm`

This command completely deletes the installation of given system. Be careful
as it does not ask for confirmation. Deleted data is irrecoverably lost.

Usage example:
```
proot-distro remove debian
```

### Rename distribution

Command: `rename`

Aliases: `mv`

Rename the distribution by changing the alias name, renaming its plug-in and
root file system directory. In case when default distribution is being renamed,
a copy of plug-in will be created.

Usage example:
```
proot-distro rename ubuntu ubuntu-test01
```

Only installed distribution can be renamed.

### Reinstall distribution

Command: `reset`

Aliases: \-

Delete the specified distribution and install it again. This is a shortcut for
```
proot-distro remove <dist> && proot-distro install <dist>
```

Usage example:
```
proot-distro reset debian
```

Same as with command `remove`, deleted data is lost irrecoverably. Be careful.

### Restore from backup

Command: `restore`

Aliases: \-

Restore the distribution from the given proot-distro backup (tar archive).

Restore operation performs a complete rollback to the backup state as was in
archive. Be careful as this command deletes previous data irrecoverably.

Compression is determined automatically from file extension. Piped data
must be always uncompressed before being supplied to `proot-distro`.

Usage example:
```
ssh example.com 'cat /backups/pd-debian-backup.tar.xz' | xz -d | proot-distro restore
proot-distro restore ./pd-debian-backup.tar.xz
```

### Clear downloads cache

Command: `clear-cache`

Aliases: `clear`, `cl`

This will remove all cached root file system archives.

## Adding distribution

Distribution is defined through the plug-in script that contains variables
with metadata. A minimal one would look like this:
```.bash
DISTRO_NAME="Debian"
TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v1.10.1/debian-aarch64-pd-v1.10.1.tar.xz"
TARBALL_SHA256['aarch64']="f34802fbb300b4d088a638c638683fd2bfc1c03f4b40fa4cb7d2113231401a21"
```

Script is stored in directory `$PREFIX/etc/proot-distro` and should be named
like `<alias>.sh`, where `<alias>` is a desired name for referencing the
distribution. For example, Debian plug-in will typically be named `debian.sh`.

### Plug-in variables reference

`DISTRO_ARCH`: specifies which CPU architecture variant of distribution to
install.

Normally this variable is determined automatically, and you should not set it.
Typical use case is to set a custom architecture to run the distribution under
QEMU emulator (user mode).

Supported architectures are: `aarch64`, `arm`, `i686`, `x86_64`.

`DISTRO_NAME`: a name of distribution, something like "Alpine Linux (3.14.1)".

`DISTRO_COMMENT`: comments for current distribution.

Normally this variable is not needed. Use it to notify user that something is
not working or additional steps required to get started with this distribution.

`TARBALL_STRIP_OPT`: how many leading path components should be stripped when
extracting rootfs archive. The default value is 1 because all default rootfs
tarballs store contents in a subdirectory.

`TARBALL_URL`: a Bash associative array of root file system tarballs URLs.

Should be defined at least for your CPU architecture. Valid architecture names
are same as for `DISTRO_ARCH`. Should start with proper protocol scheme.
For example, `https://`, `file://`, `ftp://` etc. to access local or remote file.

`TARBALL_SHA256`: a Bash associative array of SHA-256 checksums for each rootfs
variant.

Must be defined for each tarball set in `TARBALL_URL`.

### Running additional installation steps

Plug-in can be configured to execute specified commands after installing the
distribution. This is done through function `distro_setup`.

Example:
```.bash
distro_setup() {
	run_proot_cmd apt update
	run_proot_cmd apt upgrade -yq
}
```

`run_proot_cmd` is used when command should be executed inside the rootfs.

## Experimental Blink emulator support

If user specified `DISTRO_ARCH` different from the current device architecture,
a CPU emulation mode will be used.

The default CPU emulation backend is QEMU user mode. However for `x86_64`
target architecture user can enable use of Blink emulator. To use Blink
as emulation backend user need to set an environment variable:
```
export PROOT_DISTRO_X64_EMULATOR=BLINK
```

`PROOT_DISTRO_X64_EMULATOR` accepts values only `QEMU` or `BLINK`.

Install Blink emulator package with this command:
```
pkg install blink
```

Emulation mode doesn't guarantee stability. User can observe a weird behavior
of programs and crashes. Some distributions may work while others may not.
The performance also would be reduced due to emulator overhead.

## PRoot issues and differences from Chroot

While PRoot is often referred as user space chroot implementation, it is much
different from it both by implementation and features of work. Here is a list
of most significant differences you should be aware of.

1. PRoot is slow and potentially unstable due to non-native execution

   Every process is hooked through `ptrace()`. This is done to be able
   translate file paths (emulate `chroot`), fake root user identity and
   workaround unsupported system calls.

   Such intrusion into execution flow usually works properly. However under
   certain cases user may observe "impossible" bugs such as crashes or
   strange program behavior, that are not reproducible on native Linux
   distribution setups (PC, Raspberry Pi).

   Using debugger tools such as gdb or strace could be problematic.

2. PRoot cannot detach from the running process.

   Since PRoot controls the running processes via `ptrace()` it cannot detach
   from them. This means you can't start a daemon process (e.g. sshd) and close
   PRoot session. You will have to either kill process, wait until it finish or
   let proot kill it immediately on session close.

3. PRoot does not elevate privileges.

   Chroot also does not elevate privileges on its own. Just PRoot is configured
   to hijack user id as well, i.e. make it appear as `root`. So in reality your
   user name, id and privileges remain to be same as without PRoot but programs
   that do sanity check for current user will assume you are running as
   root user.

   Particularly, the fake root user makes it possible to use package manager
   in chroot environment.

4. PRoot does not emulate privilege separation.

   Your root and non-root effectively are same. Files would appear as owned
   by your current user, which means both root and non-root user will be able
   to edit files of your `proot` distribution setup.

   Depending on your PRoot Distro use cases, this might be a security issue.

5. PRoot does not enable access to hardware and file system mounting.

   You won't be able read/write to devices such as partitions of internal
   and external drives, USB devices, Wi-Fi and Bluetooth dongles.

   Mounting file systems using FUSE also not possible: Android OS doesn't
   set world-writeable permissions on `/dev/fuse`, unlike standard Linux
   distributions.

6. Appimage, Flatpak and Snap do not work under PRoot.

   Self-sufficient application containers such as Appimage, Flatpak or Snap
   rely on file system mounting capabilities, FUSE and other features that
   not available without real root permissions.

## Hacking

PROOT DISTRO DEVELOPERS CHOSE TO NOT HELP WITH INSTALLING, CONFIGURING, USING
AND TROUBLESHOOTING ALL KINDS OF TOOLS RELATED TO HACKING, PHISHING, DENIAL OF
SERVICE, SPAM, FRAUD, SOCIAL ENGINEERING, OSINT AND THEIR PRECURSORS.

Please don't ask about Black Arch, Kali Linux, Nethunter, Parrot OS and
similar distributions.

## Forking

If you wish to use PRoot Distro or its part as a base for your own project,
please make sure you comply with GNU GPL v3.0 license.

Forks must be distributed under different name.

[Termux]: <https://termux.com>
[proot]: <https://github.com/termux/proot>
