#!/bin/bash

echo "🧪 Testing current code configuration with virtual sources..."

echo "📹 Recording 10 seconds with current code settings..."

# Use lavfi sources with the exact same settings as your current code
ffmpeg -f lavfi -i "color=c=blue:size=1280x720:duration=10" \
  -f lavfi -i "testsrc=duration=10:size=1280x720:rate=30" \
  -f lavfi -i "sine=frequency=1000:duration=10" \
  -map 0:v:0 -c:v libx264 -preset fast -crf 23 \
  -profile:v main -level 4.0 -pix_fmt yuv420p \
  -bf 0 -refs 1 \
  -map 1:v:0 -filter_complex "[0:v][1:v]overlay=0:0" \
  -map 2:a:0 -c:a aac -b:a 128k \
  -t 10 -y test-simple.mp4 2>&1 | grep -E "(frame=|fps=|speed=|error)"

echo "✅ Recording completed. Analyzing results..."

# Analyze the generated video
echo "🔍 Analyzing the generated video..."
ffprobe -v quiet -show_entries stream=codec_name,profile,level,has_b_frames,refs,bit_rate -of csv test-simple.mp4

echo "📊 Video analysis:"
echo "=================="

# Check if B-frames are disabled
if ffprobe -v quiet -show_entries stream=has_b_frames -of csv test-simple.mp4 | grep -q ",0"; then
    echo "✅ B-frames successfully disabled"
else
    echo "❌ B-frames still present"
fi

# Check profile
if ffprobe -v quiet -show_entries stream=profile -of csv test-simple.mp4 | grep -q "Main"; then
    echo "✅ Main profile applied"
else
    echo "❌ Wrong profile detected"
fi

# Check level
if ffprobe -v quiet -show_entries stream=level -of csv test-simple.mp4 | grep -q "40"; then
    echo "✅ Level 4.0 applied"
else
    echo "❌ Wrong level detected"
fi

# Check file size
size=$(stat -f%z test-simple.mp4 2>/dev/null || stat -c%s test-simple.mp4 2>/dev/null)
size_mb=$(echo "scale=2; $size / 1024 / 1024" | bc -l 2>/dev/null || echo "unknown")
echo "📁 File size: ${size_mb}MB"

echo "🎥 Video saved as: test-simple.mp4"
echo "✅ Test completed! You can now play the video to check quality." 