#!/bin/bash

find /pool -not -group nas | xargs -d '\n' chgrp nas
