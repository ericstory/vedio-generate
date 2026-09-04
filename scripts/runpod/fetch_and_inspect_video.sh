#!/bin/bash
# usage: fetch_analyze.sh <name> <media-uuid>
S=${SCRATCH:-/tmp/papa-h3}; mkdir -p $S/frames
C="Cookie: $(cat $S/.cookie)"; U="https://video-generator-production-8c1e.up.railway.app/generate/media/$2.mp4"; F=$S/$1.mp4
total=$(curl -s -H "$C" -r 0-0 -D - -o /dev/null --max-time 30 "$U" | grep -i "^content-range" | sed 's#.*/##' | tr -dc '0-9'); echo "total=$total"
rm -f $F
for i in $(seq 1 40); do
  curl -s -H "$C" --max-time 60 -C - -o "$F" "$U"; rc=$?
  have=$(stat -f %z "$F" 2>/dev/null || echo 0); echo "try $i rc=$rc have=$have/$total"
  [ -n "$total" ] && [ "$have" = "$total" ] && { echo "COMPLETE"; break; }
  sleep 3
done
[ -n "$total" ] && [ "$(stat -f %z $F)" = "$total" ] || { echo "INCOMPLETE"; exit 1; }
ffprobe -v error -show_entries stream=codec_type,width,height,nb_frames -show_entries format=duration,bit_rate -of compact $F
for t in 0.5 2.5 4.5; do ffmpeg -v error -y -ss $t -i $F -frames:v 1 -vf scale=672:-1 $S/frames/$1_${t}s.png; done
echo "=====luma stats====="; ffmpeg -v info -i $F -vf "select='not(mod(n,30))',signalstats,metadata=print:file=-" -f null - 2>/dev/null | grep -oE "YAVG=[0-9.]+|YSTDEV=[0-9.]+" | paste - - | head -5
echo "=====audio====="; ffmpeg -v info -i $F -vn -af volumedetect -f null - 2>&1 | grep -E "mean_volume|max_volume"
