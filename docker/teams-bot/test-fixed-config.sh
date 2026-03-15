#!/bin/bash

echo "🧪 Testing fixed FFmpeg configuration..."

# Test the new configuration that should eliminate stuttering
echo "📹 Recording 10 seconds with optimized settings..."

# Use lavfi sources instead of x11grab for testing
ffmpeg -f lavfi -i "color=c=blue:size=1280x720:duration=10" \
  -f lavfi -i "sine=frequency=1000:duration=10" \
  -c:v libx264 -preset ultrafast -crf 32 \
  -profile:v baseline -level 3.0 -pix_fmt yuv420p \
  -threads 1 -tune zerolatency -g 30 -keyint_min 30 \
  -bf 0 -refs 1 \
  -c:a aac -b:a 128k \
  -t 10 -y test_fixed.mp4 2>&1 | grep -E "(frame=|fps=|speed=|error)"

echo "✅ Recording completed. Analyzing results..."

# Analyze the generated video
echo "🔍 Analyzing the generated video..."
ffprobe -v quiet -show_entries stream=codec_name,profile,level,has_b_frames,refs,bit_rate -of csv test_fixed.mp4

echo "📊 Video analysis:"
echo "=================="

# Check if B-frames are disabled
if ffprobe -v quiet -show_entries stream=has_b_frames -of csv test_fixed.mp4 | grep -q ",0"; then
    echo "✅ B-frames successfully disabled"
else
    echo "❌ B-frames still present"
fi

# Check profile
if ffprobe -v quiet -show_entries stream=profile -of csv test_fixed.mp4 | grep -q "baseline"; then
    echo "✅ Baseline profile applied"
else
    echo "❌ Wrong profile detected"
fi

# Check level
if ffprobe -v quiet -show_entries stream=level -of csv test_fixed.mp4 | grep -q "30"; then
    echo "✅ Level 3.0 applied"
else
    echo "❌ Wrong level detected"
fi

# Check file size
size=$(stat -f%z test_fixed.mp4 2>/dev/null || stat -c%s test_fixed.mp4 2>/dev/null)
size_mb=$(echo "scale=2; $size / 1024 / 1024" | bc -l 2>/dev/null || echo "unknown")
echo "📁 File size: ${size_mb}MB"

# Cleanup
rm -f test_fixed.mp4

echo "✅ Test completed!" 