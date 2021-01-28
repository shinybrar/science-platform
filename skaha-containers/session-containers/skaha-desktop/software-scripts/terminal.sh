#!/bin/bash 

IP_ADDRESS=`hostname --ip-address`

/opt/shibboleth/bin/curl -v -L -k -E /cavern/home/${skaha_username}/.ssl/cadcproxy.pem -d "target-ip=${IP_ADDRESS}" -d "software=harbor.canfar.net/skaha-desktop/terminal:0.1" https://${skaha_hostname}/skaha/session/${VNC_PW}/app