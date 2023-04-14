#!/bin/bash

# Get the current directory of the script
SCRIPT_DIR="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

# Move the script to /etc/ if it's not already there
if [[ "$SCRIPT_DIR" != "/etc" ]]; then
  sudo mv "$BASH_SOURCE" /etc/user.sh || { echo "Error: Failed to move script to /etc/" >&2; exit 1; }
  echo "Script moved to /etc/user.sh"
fi

# Prompt the user to enter a username
read -p "Enter a username [lowercase]: " username

# Validate that the username is lowercase
if [[ "$username" != "${username,,}" ]]; then
  echo "Error: Username must be lowercase" >&2
  exit 1
fi

# Prompt the user to enter a password
while true; do
  read -s -p "Enter a password: " password
  echo
  read -s -p "Re-enter password: " password_confirm
  echo
  if [[ "$password" == "$password_confirm" ]]; then
    break
  else
    echo "Error: Passwords do not match. Please try again." >&2
  fi
done

# Create the user and set the password
useradd -m -s /bin/bash "$username" || { echo "Error: Failed to create user" >&2; exit 1; }
echo "${username}:${password}" | chpasswd || { echo "Error: Failed to set password" >&2; exit 1; }

# Add the user to the sudo group
usermod -aG sudo "$username" || { echo "Error: Failed to add user to sudo group" >&2; exit 1; }

# Configure sudo to not require a password for the user
echo "$username ALL=(ALL) NOPASSWD: ALL" > "/etc/sudoers.d/$username"

# Set ownership and permissions on the sudo binary
chown root:root /usr/bin/sudo && chmod 4755 /usr/bin/sudo

# Output a message to the user with the username and password
echo "User account created successfully:"
echo "Username: $username"
echo "Password: $password"

# Prompt the user to create another user
read -p "Create another user? [y/n]: " choice
if [[ "$choice" == "y" ]]; then
  sudo bash /etc/user.sh
else
  echo "Done"
fi
