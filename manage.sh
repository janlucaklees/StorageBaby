#!/bin/bash

# Give the first argument a name for more readable code.
service_name=$1
service_root="${PWD}/${service_name}"
service_compose="${service_root}/docker-compose.yml"
service_dynamic_env="${service_root}/.env.sh"

# Make sure there actually is some configuration for the given service name.
if [ ! -f  "${service_compose}" ]; then
    echo "No service group '${service_name}' found."
    exit 1
fi

# Load global environment variables
set -o allexport
. ${PWD}/.env
set +o allexport

# Load dynamic, service specific environment variables
if [ -f  "${service_dynamic_env}" ]; then
    set -o allexport
    . ${service_dynamic_env}
    set +o allexport
fi

# Assemble the docker-compose command
DC="docker compose"

cd $service_root
eval $DC "${@:2}"
