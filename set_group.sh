#!/bin/bash

find /mnt/pool -not -group nas | xargs -d '\n' chgrp nas
