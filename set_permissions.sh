#!/bin/bash

find /mnt/pool -not -perm -g+r | xargs -d '\n' chmod g+r 
