#!/bin/bash

set -e
set -x

# Installing dependencies
yay -S --needed --noconfirm \
    mesa \
    vulkan-mesa-layers \
    vulkan-radeon \
    vulkan-tools \
    vdpauinfo \
    libva-utils
