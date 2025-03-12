#!/bin/bash

# Start up Paperless again as sync is over and it is safe again to change data.
docker start paperless-broker-1 paperless-tika-1 paperless-db-1 paperless-gotenberg-1
docker start paperless-webserver-1
