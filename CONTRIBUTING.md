# Proot-Distro Contributor Guide

Proot-Distro is yet another utility for installing Linux distributions on the
non-rooted Android device. As the most of existing software projects, this
utility created with aim to solve a specific range of tasks using the way
preferred by its original author.

This document describes the principles that you must take into account when
contributing to the Proot-Distro project.

## Disclaimer

DON'T expect:

* That Proot-Distro will satisfy your needs

* That Proot-Distro will work flawlessly on your device

* That all your issues will be resolved by maintainers immediately

* That all your submitted patches would be accepted

* That maintainers will teach you how to use Linux distributions

DON'T report bugs for:

* Distributions

* Restricted access to distribution hosting service

* Lack of Internet access

* Usage of mobile data plan and disk space

DON'T ask for features such as:

* Support for runtime platforms other than Termux

* Classical Linux chroot and other things that require rooted device

* Modified or pre-configured distributions

* Distributions related to hacking: Kali Linux, Nethunter, Parrot OS,
  Black Arch, etc

* Distribution init systems: systemd, openrc, etc

* Additionally: *here goes all stuff that would require me to redesign
  Proot-Distro from scratch*

***

## Design insights

### 1. General information

**1.1** Proot-Distro has a specific, well defined structure which makes it
unique and separates from analogous projects. This structure expected to be
followed during whole project lifetime. If you have ideas how to restructure
the Proot-Distro, then please keep them in your own fork! Customizing sources
for your own preferences is not welcome.

Review the `proot-distro.sh` script to understand how it works and see key
parts of the design.

**1.2** Proot-Distro is written in Bash script language with using features
which are not compatible with POSIX shell. The script itself is monolithic
and is not supposed to be split on multiple parts.

**1.3** Tabs must be used for indenting code blocks everywhere.

**1.4** All features are split on functions. There are 3 types of functions:

* Commands: implement specific features of Proot-Distro such as install,
  backup, login the distribution. These functions have following name scheme:
  `command_name()`.

* Command help: supplementary functions implementing informational output
  describing usage of Proot-Distro commands. These functions are named as
  `command_name_help()` and are defined AFTER the commands.

* Utility functions: reusable or very long pieces of code.

**1.5** Each command function definition should begin with comment section.

Comment sections logically separate functions on groups and provide a brief
overview which part of Proot-Distro is implemented here. The comment must
provide a description of how the function works.

Utility functions are allowed to have a very brief description and command help
functions.

Command help functions should not have comments. They are self descriptive.

**1.6** Code pieces that do non-obvious actions must be commented.

**1.7** Functions are not allowed to define global variables. Use keyword
`local` to define the local variables instead.

**1.8** Global variables must be defined either at beginning of Proot-Distro
script or at the entry point.

**1.9** Use of getopt for options handling is considered as non-flexible and
thus not allowed.

**1.10** The checks for error conditions must be implemented. If error has been
encountered, a message should be printed on stderr and further execution should
be terminated.

**1.11** If error was encountered inside function, exiting should be done using
the command `return 1` to pass a status code to the caller. Use of `exit`
command is not allowed unless used in script entry point.

**1.12** User input in command functions must be validated during the command
line arguments handling.

**1.13** Use heredocs to write files, especially if their content is long.

**1.14** Certain configuration such as plug-in directory path may be set only
during installation of the Proot-Distro. Such configuration is treated as
persistent and should not be changed by user.

**1.15** Proot-Distro uses string place holders to define certain common values
for some variables:

* `@TERMUX_APP_PACKAGE@` - sets Termux app package (com.termux).

* `@TERMUX_PREFIX@` - sets the installation prefix path.

* `@TERMUX_HOME@` - sets the Termux home directory path.

It is not allowed to hardcode the mentioned values instead of using the place
holder.

***

### 2. Informational messages output

**2.1** All informational messages are printed in specific format.

**2.2** Use function `msg` to print messages.

**2.3** Messages printed by help functions must fit in 76 columns. If the
message is too long, split in on multiple lines. This is not relevant to
errors and other informational messages. Pay extra attention to keep the
message properly indented.

**2.4** All messages printable by Proot-Distro script must support colored
text through the escape sequences defined below by variables such as `${RED}`
(red), `${BRED}` (bold red text), `${YELLOW}`, `${BYELLOW}`, etc.

**2.5** Proot-Distro operation errors must have bold red color (`${BRED}`).
The key information such as arguments caused an error should be encloded in
quotes and highlighted by yellow color (`${YELLOW}`).

**2.6** Messages should be terminated by `${RST}` which resets the text colors
and other attributes.

**2.7** Informational messages produced by Proot-Distro command steps must have
the following format:

    ${BLUE}[${GREEN}*${BLUE}] ${CYAN}Message...${RST}"

**2.8** If the step encountered a condition where an error should be printed,
the following message format should be used instead:

    ${BLUE}[${RED}!${BLUE}] ${CYAN}Error or warning message.${RST}

**2.9** Warnings about issues happened due to unexpected circumstances rather
than due to failure of Proot-Distro actions should be printed in this format:

    ${BRED}Warning: message.${RST}

Similarly to warnings, the error messages should have this format:

    ${BRED}Error: message.${RST}

***

### 3. Handling distributions

**3.1** Proot-Distro script is intended to be distribution-agnostic. That means
it treats all distributions equally and handles them in the same way despite
the possible differences at level of distribition root file system package.

**3.2** Support of specific distribution is enabled by using a plug-in. The
plug-in is a Bash script that is sourced by Proot-Distro and has the following
format:

    DISTRO_NAME="Example"
    DISTRO_COMMENT="Example distribution."

    TARBALL_STRIP_OPT=1

    TARBALL_URL['aarch64']="https://example.com/archive-aarch64.tar.gz"
    TARBALL_URL['arm']="https://example.com/archive-armv7.tar.gz"
    TARBALL_URL['i686']="https://example.com/archive-i386.tar.gz"
    TARBALL_URL['riscv64']="https://example.com/archive-riscv64.tar.gz"
    TARBALL_URL['x86_64']="https://example.com/archive-amd64.tar.gz"

    TARBALL_SHA256['aarch64']="0000000000000000000000000000000000000000000000000000000000000000"
    TARBALL_SHA256['arm']="0000000000000000000000000000000000000000000000000000000000000000"
    TARBALL_SHA256['i686']="0000000000000000000000000000000000000000000000000000000000000000"
    TARBALL_SHA256['riscv64']="0000000000000000000000000000000000000000000000000000000000000000"
    TARBALL_SHA256['x86_64']="0000000000000000000000000000000000000000000000000000000000000000"

     distro_setup() {
       run_proot_cmd touch /etc/hello-world
       run_proot_cmd bash -c "echo '127.0.0.1 hello-world' >> /etc/hosts"
     }

**3.3** The variable `DISTRO_NAME` specifies a full name of distribution such
as `Ubuntu` or `Debian (stable)`. This variable is mandatory.

**3.4** The variable `TARBALL_URL` is a Bash associative array which contains
URLs to distribution rootfs tarball for given CPU architectures. This variable
is mandatory. The URL should start with proper protocol scheme.
For example `https://`, `ftp://` or `file://`.

**3.5** The variable `TARBALL_SHA256` is a Bash associative array which
contains SHA-256 checksums of rootfs tarballs for given CPU architectures.
This variable is mandatory.

**3.6** Post-installation steps that may be required for some distributions
are defined in `distro_setup()` function (optional). This function has access
to all variables defined by Proot-Distro during the execution of
`command_install()`.

**3.7** Commands inside `distro_setup()` that are intended to be executed in
distribution environment must be defined as arguments of the command
`run_proot_cmd`.

**3.8** Distributions must be adressed by their alias which in fact is a
file name of plug-in except the extension part.

**3.9** The alias files are located in `${TERMUX_PREFIX}/etc/proot-distro` and
have extensions `.sh` or `.override.sh`.

* `dist.sh` name format is used for standard plug-ins.

* `dist2.override.sh` name is used to indicate that this is a renamed
distribution created by command `rename` or by the option `--override-alias`
of command `install`.

**3.10** The alias for distribution must be unique and Proot-Distro should take
care of that. User should not end with having two plug-ins for same alias, for
example `ubuntu.sh` and `ubuntu.override.sh`.

**3.11** The rootfs for distribution may be extracted only under PRoot session
with active link2symlink extension.

***

### 4. Project versioning scheme: major.minor.patch

**4.1** Major version should be incremented when breaking changes were
released. Examples are deprecated features, changed locations of files,
command line format changes.

**4.2** Minor version is incremented for significant but non-breaking changes
such as added new features or upgraded distributions. The minor version is set
to 0 when major version was incremented.

**4.3** Patch version is incremented for small changes such as bug fixes. It
is set to 0 when major or minor versions were incremented.
