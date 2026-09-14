#!/bin/bash
# Ensure the Bluetooth speaker is connected, in A2DP (music quality) and is the default output.
#   asa_bt_speaker.sh wait   -> boot: retry up to ~60 s
#   asa_bt_speaker.sh once   -> before each clip: quick check, one reconnect try
MAC=9D:C9:74:8C:B7:62          # ZEB-CLIPPER (speaker only)
NAME=CLIPPER                   # matched case-insensitively in the sink list
VOL=$(cat /home/arduino/.asa_volume 2>/dev/null)   # saved dashboard volume
[[ "$VOL" =~ ^(0\.[3-9][0-9]?|1|1\.00?)$ ]] || VOL=0.70   # valid 0.30-1.00, else default
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}
CARD=bluez_card.${MAC//:/_}
sink_id() { wpctl status 2>/dev/null | sed -n '/Sinks:/,/Sources:/p' | grep -i "$NAME" | grep -oE '[0-9]+\.' | head -1 | tr -d .; }
ensure_a2dp() {   # switch only when the profile is known AND not A2DP (a slow pactl must not re-trigger it)
  local prof
  prof=$(timeout 3 pactl list cards 2>/dev/null | sed -n "/Name: $CARD/,/Active Profile/p" | sed -n 's/.*Active Profile: //p')
  case "$prof" in a2dp*|"") return ;; esac
  logger -t asa-bt "speaker profile '$prof' -> a2dp-sink"
  pactl set-card-profile "$CARD" a2dp-sink 2>/dev/null && sleep 1
}
use() { wpctl set-default "$1"; wpctl set-volume "$1" "$VOL"; }
tries=1; [ "$1" = "wait" ] && tries=12
for i in $(seq 1 $tries); do
  ensure_a2dp; ID=$(sink_id)
  if [ -n "$ID" ]; then use "$ID"; echo "speaker ready (node $ID)"; exit 0; fi
  bluetoothctl connect $MAC >/dev/null 2>&1
  for j in 1 2 3 4; do sleep 1; ensure_a2dp; ID=$(sink_id); [ -n "$ID" ] && break; done
  if [ -n "$ID" ]; then use "$ID"; echo "speaker connected (node $ID)"; exit 0; fi
  [ "$1" = "wait" ] && sleep 1
done
echo "speaker not available - continuing without it"
exit 0
