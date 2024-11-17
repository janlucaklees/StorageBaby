#!/bin/bash

PUID=`id -u jlk`
PGID=`getent group wheel | cut -d: -f3`
