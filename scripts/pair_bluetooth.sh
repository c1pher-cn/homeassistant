#!/bin/bash

bluetoothctl << EOF
pair B8:69:C2:16:B1:6D
trust B8:69:C2:16:B1:6D
connect B8:69:C2:16:B1:6D
EOF
