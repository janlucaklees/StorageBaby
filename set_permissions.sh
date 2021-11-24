#!/bin/bash

find /pool -not -perm -g+r | xargs -d '\n' chmod g+r 
